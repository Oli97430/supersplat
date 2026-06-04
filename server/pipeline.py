"""Photo/video -> Gaussian Splat PLY pipeline.

Drives ffmpeg, COLMAP (via nerfstudio's ns-process-data), and nerfstudio's
splatfacto trainer + exporter as subprocesses. Emits JSON status updates.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional


class JobCancelled(Exception):
    """Raised when a job is cancelled mid-pipeline."""

ROOT = Path(__file__).parent.parent
VENV_PY = ROOT / "venv" / "Scripts" / "python.exe"
VENV_BIN = ROOT / "venv" / "Scripts"
COLMAP_BIN = ROOT / "tools" / "colmap" / "bin"
# COLMAP_LIB holds boost/ceres/cudart/FreeImage/Qt DLLs that colmap.exe must
# load at startup. Without lib/ on PATH, colmap.exe silently fails to load and
# nerfstudio's `check_colmap_installed` (which runs `colmap -h` and inspects
# the exit code) reports "Could not find COLMAP".
COLMAP_LIB = ROOT / "tools" / "colmap" / "lib"
FFMPEG_BIN = ROOT / "tools" / "ffmpeg" / "bin"
# rembg stores its ONNX models under U2NET_HOME. We bundle/cache them in the
# install dir so the (non-admin) backend finds the model the installer
# pre-downloaded as admin, instead of re-fetching into the user's home.
REMBG_HOME = ROOT / "models" / "rembg"

STAGES = ["queued", "preparing", "extracting", "colmap", "training", "exporting", "rendering", "done"]

VIDEO_EXTS = {".mp4", ".mov", ".mkv", ".avi", ".webm", ".m4v"}
IMAGE_EXTS = {".jpg", ".jpeg", ".png"}

# Minimum registered camera poses to proceed to training.
MIN_POSES = 20

# Capture profiles — auto-tune matcher / iterations / fps / blur threshold.
CAPTURE_PRESETS: dict[str, dict] = {
    "object": {
        "label": "Object / Small Scene",
        "description": "Sculpture, product, plant — close range, 360° around",
        "matcher": "sequential", "max_iters": 20000,
        "extract_fps": 3, "blur_threshold": 80.0,
        "dedupe_threshold": 4, "prune_opacity_logit": -2.5,
    },
    "indoor": {
        "label": "Indoor Scene",
        "description": "Room, café, museum interior — wide spaces",
        "matcher": "sequential", "max_iters": 30000,
        "extract_fps": 2, "blur_threshold": 100.0,
        "dedupe_threshold": 4, "prune_opacity_logit": -2.5,
    },
    "outdoor": {
        "label": "Outdoor Scene",
        "description": "Building, landscape, monument — varied lighting",
        "matcher": "vocab_tree", "max_iters": 40000,
        "extract_fps": 2, "blur_threshold": 100.0,
        "dedupe_threshold": 3, "prune_opacity_logit": -2.5,
    },
    "drone": {
        "label": "Drone Orbit",
        "description": "Aerial orbit around a subject — building, monument, terrain feature",
        # vocab_tree finds the cross-orbit / loop-closure matches that a
        # multi-altitude orbit needs. 3 fps samples parallax densely on a
        # smooth flight; conservative dedup (2) keeps useful orbit frames
        # instead of dropping half of them.
        "matcher": "vocab_tree", "max_iters": 30000,
        "extract_fps": 3, "blur_threshold": 60.0,
        "dedupe_threshold": 2, "prune_opacity_logit": -2.5,
    },
    "portrait": {
        "label": "Person / Portrait",
        "description": "Full body around a single subject",
        "matcher": "sequential", "max_iters": 25000,
        "extract_fps": 3, "blur_threshold": 120.0,
        "dedupe_threshold": 4, "prune_opacity_logit": -2.5,
    },
    "preview": {
        "label": "Quick Preview",
        "description": "5k iterations — fast check before full training",
        "matcher": "sequential", "max_iters": 5000,
        "extract_fps": 2, "blur_threshold": 80.0,
        "dedupe_threshold": 5, "prune_opacity_logit": -2.0,
    },
    "custom": {
        "label": "Custom",
        "description": "Manual parameters — blur filter disabled",
        "matcher": "sequential", "max_iters": 30000,
        "extract_fps": 2, "blur_threshold": 0.0,
        "dedupe_threshold": 0, "prune_opacity_logit": -2.5,
    },
}


def check_disk_space(path: Path, required_gb: float = 5.0) -> tuple[bool, float]:
    """Check if `path` has at least `required_gb` free. Returns (ok, free_gb)."""
    try:
        free_bytes = shutil.disk_usage(path).free
        free_gb = free_bytes / 1024 / 1024 / 1024
        return free_gb >= required_gb, round(free_gb, 2)
    except OSError:
        return True, 0.0  # don't block on unknown filesystems


def extract_thumbnail(upload_dir: Path, out_path: Path) -> bool:
    """Make a 320px JPG thumbnail from the first video frame or first photo."""
    try:
        items = sorted(upload_dir.iterdir())
    except FileNotFoundError:
        return False

    video = next((p for p in items if p.suffix.lower() in VIDEO_EXTS), None)
    photo = next((p for p in items if p.suffix.lower() in IMAGE_EXTS), None)

    if video is not None:
        cmd = ["ffmpeg", "-y", "-loglevel", "error", "-i", str(video),
               "-ss", "00:00:01", "-vframes", "1", "-vf", "scale=320:-2",
               "-q:v", "4", str(out_path)]
    elif photo is not None:
        cmd = ["ffmpeg", "-y", "-loglevel", "error", "-i", str(photo),
               "-vf", "scale=320:-2", "-q:v", "4", str(out_path)]
    else:
        return False

    try:
        subprocess.run(cmd, capture_output=True, timeout=30, check=False)
        return out_path.exists()
    except Exception:
        return False


def ply_metrics(ply_path: Path) -> dict:
    """Read PLY header and return file_size_mb + gaussian_count."""
    if not ply_path.exists():
        return {}
    file_size = ply_path.stat().st_size
    gaussian_count = None
    try:
        with ply_path.open("rb") as f:
            for _ in range(60):  # safety bound
                line = f.readline()
                if not line:
                    break
                if line.startswith(b"element vertex"):
                    parts = line.decode("ascii", errors="ignore").strip().split()
                    if len(parts) >= 3:
                        try:
                            gaussian_count = int(parts[2])
                        except ValueError:
                            pass
                    break
                if line.strip() == b"end_header":
                    break
    except Exception:
        pass
    return {
        "file_size_mb": round(file_size / 1024 / 1024, 2),
        "gaussian_count": gaussian_count,
    }


def filter_blurry_frames(images_dir: Path, threshold: float) -> int:
    """Drop frames whose Laplacian-variance proxy is below threshold (more blur).
    Returns count removed. Requires numpy+PIL; no-op if either missing or threshold<=0."""
    if threshold <= 0:
        return 0
    try:
        from PIL import Image  # type: ignore
        import numpy as np      # type: ignore
    except ImportError:
        return 0

    removed = 0
    candidates = sorted(images_dir.glob("*.jpg")) + sorted(images_dir.glob("*.png"))
    if len(candidates) < 30:
        return 0  # don't risk dropping any frames if we have very few

    keep_min = max(20, int(len(candidates) * 0.6))  # never drop more than 40%
    for img_path in candidates:
        try:
            with Image.open(img_path) as img:
                arr = np.asarray(img.convert("L"), dtype=np.float32)
            # Cheap Laplacian-variance proxy via finite differences
            dy = arr[:-1, :] - arr[1:, :]
            dx = arr[:, :-1] - arr[:, 1:]
            variance = float(np.var(dy)) + float(np.var(dx))
            if variance < threshold and (len(candidates) - removed) > keep_min:
                img_path.unlink()
                removed += 1
        except Exception:
            pass
    return removed


def dedupe_consecutive_frames(images_dir: Path, hamming_threshold: int = 4) -> int:
    """Drop frames that are near-identical to the previous frame using dHash.
    Useful when the camera moves slowly: consecutive frames at 2-3 fps can be
    almost the same and add no information for COLMAP / training.

    Algorithm:
      - 64-bit dHash (difference hash) per frame: resize to 9x8 grey,
        compare each pixel to its right neighbour, flatten to 64 bits.
      - Greedy walk: keep the first frame, drop the next if hamming(prev, cur)
        is below threshold (4/64 by default ~ 6% of bits).
      - Never drop more than half the frames; never run on fewer than 30.

    Returns the number of frames removed."""
    if hamming_threshold <= 0:
        return 0
    try:
        from PIL import Image  # type: ignore
        import numpy as np      # type: ignore
    except ImportError:
        return 0

    candidates = sorted(images_dir.glob("*.jpg")) + sorted(images_dir.glob("*.png"))
    if len(candidates) < 30:
        return 0

    # Compute dHash for every frame upfront.
    hashes: list[tuple[Path, int | None]] = []
    for img_path in candidates:
        try:
            with Image.open(img_path) as img:
                arr = np.asarray(img.convert("L").resize((9, 8), Image.LANCZOS),
                                 dtype=np.int16)
            diff = arr[:, 1:] > arr[:, :-1]                # (8, 8) bool grid
            bits = 0
            for b in diff.flatten():
                bits = (bits << 1) | int(b)
            hashes.append((img_path, bits))
        except Exception:
            hashes.append((img_path, None))

    keep_min = max(20, int(len(candidates) * 0.5))         # never drop > 50%
    removed = 0
    last_kept_hash: int | None = hashes[0][1]

    for img_path, h in hashes[1:]:
        if h is None or last_kept_hash is None:
            last_kept_hash = h
            continue
        dist = bin(h ^ last_kept_hash).count("1")
        if dist <= hamming_threshold and (len(candidates) - removed) > keep_min:
            try:
                img_path.unlink()
                removed += 1
            except Exception:
                last_kept_hash = h
        else:
            last_kept_hash = h
    return removed


def analyze_capture(images_dir: Path, max_pairs: int = 14) -> dict:
    """Pre-flight check on the extracted frames — runs in a few seconds before
    the (slow) COLMAP pass so a doomed capture is caught early instead of after
    20+ minutes of reconstruction that ends with "only 5 poses".

    Heuristics (all cheap, on downscaled frames):
      • parallax  — median dense optical-flow magnitude between consecutive
        frames. Near-zero ⇒ the camera barely translated (pano / hover /
        distant vista) ⇒ COLMAP can't triangulate.
      • sharpness — Laplacian variance; flags a mostly-blurry capture.
      • sky_frac  — fraction of bright, low-texture pixels (sky / blown sky).
      • texture   — overall gradient energy; flags a featureless scene.

    Returns a report dict with a verdict ('good' | 'risky' | 'poor') and a
    list of {level, msg} warnings. Never raises — analysis is best-effort and
    must not block the pipeline."""
    report: dict = {"verdict": "good", "warnings": [], "n_frames": 0}
    try:
        import cv2          # type: ignore
        import numpy as np  # type: ignore
    except ImportError:
        return report

    frames = sorted(images_dir.glob("*.jpg")) + sorted(images_dir.glob("*.png"))
    n = len(frames)
    report["n_frames"] = n
    if n < 5:
        report["verdict"] = "poor"
        report["warnings"].append({"level": "error",
            "msg": f"Only {n} frames — far too few (need 20+)."})
        return report

    def load_gray(p, w=480):
        img = cv2.imdecode(np.fromfile(str(p), dtype=np.uint8), cv2.IMREAD_GRAYSCALE)
        if img is None:
            return None
        h = int(img.shape[0] * w / img.shape[1])
        return cv2.resize(img, (w, max(1, h)), interpolation=cv2.INTER_AREA)

    orb = cv2.ORB_create(1200)
    bf  = cv2.BFMatcher(cv2.NORM_HAMMING, crossCheck=True)

    idxs = sorted(set(int(i) for i in np.linspace(0, n - 2, min(max_pairs, n - 1))))
    residuals, inliers, sharps, skies = [], [], [], []
    for i in idxs:
        a = load_gray(frames[i]); b = load_gray(frames[i + 1])
        if a is None or b is None:
            continue
        # sharpness + sky on frame a
        sharps.append(float(cv2.Laplacian(a, cv2.CV_64F).var()))
        gx = cv2.Sobel(a, cv2.CV_32F, 1, 0, ksize=3)
        gy = cv2.Sobel(a, cv2.CV_32F, 0, 1, ksize=3)
        gmag = np.sqrt(gx ** 2 + gy ** 2)
        skies.append(float(np.logical_and(a > 200, gmag < 8).mean()))

        # Parallax via the homography-degeneracy idea COLMAP itself uses: match
        # ORB features, fit the camera ego-motion (a partial affine — handles
        # pan / rotation / zoom of a distant or planar scene exactly), and look
        # at the RESIDUAL. A pano/hover is fully explained by that model →
        # ~0 residual → no depth parallax → COLMAP can't triangulate. A real
        # orbit of a near subject leaves a clear residual.
        ka, da = orb.detectAndCompute(a, None)
        kb, db = orb.detectAndCompute(b, None)
        if da is None or db is None or len(ka) < 20 or len(kb) < 20:
            inliers.append(0)
            continue
        matches = bf.match(da, db)
        if len(matches) < 20:
            inliers.append(len(matches))
            continue
        matches = sorted(matches, key=lambda x: x.distance)[:200]
        pa = np.float32([ka[m.queryIdx].pt for m in matches])
        pb = np.float32([kb[m.trainIdx].pt for m in matches])
        M, inl = cv2.estimateAffinePartial2D(pa, pb, method=cv2.RANSAC,
                                             ransacReprojThreshold=3)
        if M is None:
            inliers.append(0)
            continue
        proj = (pa @ M[:, :2].T) + M[:, 2]
        residuals.append(float(np.median(np.linalg.norm(proj - pb, axis=1))))
        inliers.append(int(inl.sum()) if inl is not None else 0)

    parallax  = float(np.median(residuals)) if residuals else 0.0
    avg_inl   = float(np.mean(inliers)) if inliers else 0.0
    sharpness = float(np.median(sharps)) if sharps else 0.0
    sky_frac  = float(np.median(skies)) if skies else 0.0
    report.update(parallax=round(parallax, 2), inliers=round(avg_inl),
                  sharpness=round(sharpness, 1), sky_frac=round(sky_frac, 3))

    warns = report["warnings"]
    # ── Parallax (the decisive one) ───────────────────────────────────────
    if not residuals or parallax < 1.0:
        report["verdict"] = "poor"
        warns.append({"level": "error",
            "msg": f"Very low parallax (residual {parallax:.1f}px). The camera "
                   "isn't translating relative to the scene — looks like a "
                   "pano / hover / distant subject. COLMAP will likely fail. "
                   "Orbit a NEAR subject (camera pointed at it, fly around it)."})
    elif parallax < 1.6:
        report["verdict"] = "risky"
        warns.append({"level": "warn",
            "msg": f"Low parallax (residual {parallax:.1f}px). Move more AROUND "
                   "the subject, or get closer so it fills the frame."})
    # ── Feature matching (texture) ────────────────────────────────────────
    if avg_inl < 30:
        if report["verdict"] != "poor":
            report["verdict"] = "risky"
        warns.append({"level": "warn",
            "msg": f"Few stable features ({avg_inl:.0f}/pair) — low texture, "
                   "blur or fog. COLMAP matching may struggle."})
    # ── Sky / blank ───────────────────────────────────────────────────────
    if sky_frac > 0.45:
        if report["verdict"] != "poor":
            report["verdict"] = "risky"
        warns.append({"level": "warn",
            "msg": f"~{sky_frac*100:.0f}% of the frame is sky/blank — frame the "
                   "subject tighter."})
    # ── Blur ──────────────────────────────────────────────────────────────
    if sharpness < 120:
        if report["verdict"] != "poor":
            report["verdict"] = "risky"
        warns.append({"level": "warn",
            "msg": "Frames look soft/blurry — faster shutter, avoid fast moves."})
    # ── Frame count ───────────────────────────────────────────────────────
    if n < 30:
        warns.append({"level": "warn",
            "msg": f"Only {n} frames — a longer capture or higher fps helps."})
    return report


def optimize_ply(in_path: Path, out_path: Path,
                 opacity_min_logit: float = -2.5) -> dict:
    """Prune near-invisible gaussians from a 3D Gaussian Splat PLY.

    The "opacity" field in a 3DGS PLY is the logit (pre-sigmoid) of the
    visible alpha; sigmoid(-2.5) ~ 0.076 — these gaussians contribute almost
    nothing visually but inflate the file size and slow editor loading.

    Returns a stats dict: original_count, kept_count, pruned_count,
    pruned_pct, original_size_mb, new_size_mb, size_reduction_pct.
    On any parse failure returns {'error': '...'} and leaves files untouched."""
    if not in_path.exists():
        return {"error": "input missing"}
    try:
        import numpy as np  # type: ignore
    except ImportError:
        return {"error": "numpy not available"}

    try:
        with in_path.open("rb") as f:
            header = bytearray()
            while not header.endswith(b"end_header\n") and len(header) < 65536:
                ch = f.read(1)
                if not ch:
                    return {"error": "header truncated"}
                header += ch
            data = f.read()

        text = header.decode("ascii", errors="ignore")
        m_count = re.search(r"element vertex (\d+)", text)
        if not m_count:
            return {"error": "no vertex count"}
        count = int(m_count.group(1))

        properties = re.findall(r"property float ([a-zA-Z_0-9]+)", text)
        if "opacity" not in properties:
            return {"error": "no opacity field"}

        opacity_idx = properties.index("opacity")
        nfloats     = len(properties)
        expected    = count * nfloats * 4
        if len(data) < expected:
            return {"error": f"data truncated: got {len(data)} expected {expected}"}

        arr  = np.frombuffer(data[:expected], dtype=np.float32).reshape(count, nfloats)
        mask = arr[:, opacity_idx] >= opacity_min_logit
        kept = arr[mask]
        new_count = int(kept.shape[0])

        if new_count == count:
            # nothing pruned -- just copy through
            shutil.copy(in_path, out_path)
        else:
            new_header = text.replace(f"element vertex {count}",
                                      f"element vertex {new_count}", 1)
            out_path.parent.mkdir(parents=True, exist_ok=True)
            with out_path.open("wb") as f:
                f.write(new_header.encode("ascii"))
                f.write(kept.tobytes())

        orig_size = in_path.stat().st_size
        new_size  = out_path.stat().st_size
        return {
            "original_count":      count,
            "kept_count":          new_count,
            "pruned_count":        count - new_count,
            "pruned_pct":          round((count - new_count) / count * 100, 1) if count else 0,
            "original_size_mb":    round(orig_size / 1024 / 1024, 2),
            "new_size_mb":         round(new_size  / 1024 / 1024, 2),
            "size_reduction_pct":  round((1 - new_size / orig_size) * 100, 1) if orig_size else 0,
        }
    except Exception as e:
        return {"error": str(e)}


def remove_floaters(in_path: Path, out_path: Path,
                    nb_neighbors: int = 20, std_ratio: float = 2.0,
                    max_drop_frac: float = 0.12) -> dict:
    """Remove statistically-isolated "floater" gaussians.

    3DGS training often leaves a cloud of stray gaussians far from the real
    surface (artefacts of under-constrained regions). We treat the gaussian
    centres as a point cloud and drop points whose mean distance to their
    `nb_neighbors` nearest neighbours is more than `std_ratio` std-devs above
    the global mean — open3d's statistical outlier removal.

    Safe: only drops PLY rows (never moves/rotates), so spherical-harmonic
    colour stays valid. Capped at `max_drop_frac`: if the filter wants to
    remove more than that (likely eating thin legitimate geometry), we skip
    and copy through untouched.

    Returns a stats dict; on any failure returns {'error': ...} and leaves the
    output as a plain copy of the input."""
    if not in_path.exists():
        return {"error": "input missing"}
    try:
        import numpy as np  # type: ignore
        import open3d as o3d  # type: ignore
    except ImportError as e:
        return {"error": f"dependency missing: {e}"}

    try:
        with in_path.open("rb") as f:
            header = bytearray()
            while not header.endswith(b"end_header\n") and len(header) < 65536:
                ch = f.read(1)
                if not ch:
                    return {"error": "header truncated"}
                header += ch
            data = f.read()

        text = header.decode("ascii", errors="ignore")
        m_count = re.search(r"element vertex (\d+)", text)
        if not m_count:
            return {"error": "no vertex count"}
        count = int(m_count.group(1))
        properties = re.findall(r"property float ([a-zA-Z_0-9]+)", text)
        if not all(c in properties for c in ("x", "y", "z")):
            return {"error": "no xyz fields"}
        nfloats = len(properties)
        expected = count * nfloats * 4
        if len(data) < expected:
            return {"error": "data truncated"}

        arr = np.frombuffer(data[:expected], dtype=np.float32).reshape(count, nfloats)
        xi, yi, zi = (properties.index(c) for c in ("x", "y", "z"))
        pts = np.ascontiguousarray(arr[:, [xi, yi, zi]], dtype=np.float64)

        pcd = o3d.geometry.PointCloud()
        pcd.points = o3d.utility.Vector3dVector(pts)
        _, keep_idx = pcd.remove_statistical_outlier(
            nb_neighbors=nb_neighbors, std_ratio=std_ratio)
        keep_idx = np.asarray(keep_idx, dtype=np.int64)
        new_count = int(keep_idx.shape[0])
        dropped = count - new_count

        # Safety: never eat more than max_drop_frac of the model.
        if count and (dropped / count) > max_drop_frac:
            shutil.copy(in_path, out_path)
            return {"skipped": True,
                    "reason": f"would drop {dropped}/{count} "
                              f"({dropped / count * 100:.1f}%) > cap",
                    "original_count": count, "kept_count": count}

        if dropped == 0:
            shutil.copy(in_path, out_path)
        else:
            kept = arr[keep_idx]
            new_header = text.replace(f"element vertex {count}",
                                      f"element vertex {new_count}", 1)
            out_path.parent.mkdir(parents=True, exist_ok=True)
            with out_path.open("wb") as f:
                f.write(new_header.encode("ascii"))
                f.write(kept.tobytes())

        return {
            "original_count": count,
            "kept_count": new_count,
            "dropped_count": dropped,
            "dropped_pct": round(dropped / count * 100, 1) if count else 0,
        }
    except Exception as e:
        try:
            shutil.copy(in_path, out_path)
        except Exception:
            pass
        return {"error": str(e)}


def crop_and_center(in_path: Path, out_path: Path,
                    lo_pct: float = 1.0, hi_pct: float = 99.0,
                    margin: float = 0.08, max_drop_frac: float = 0.30) -> dict:
    """Auto-crop stray background gaussians to a robust bounding box, then
    recenter the model on its (post-crop) median so it loads centred.

    Safe: only drops PLY rows and translates xyz — spherical-harmonic colour is
    rotation-dependent, not translation-dependent, so it stays valid. Capped at
    max_drop_frac; if cropping would eat more than that, we keep every gaussian
    and only recenter.

    Returns a stats dict, or {'error': ...} leaving out_path as a plain copy."""
    if not in_path.exists():
        return {"error": "input missing"}
    try:
        import numpy as np  # type: ignore
    except ImportError:
        return {"error": "numpy not available"}
    try:
        with in_path.open("rb") as f:
            header = bytearray()
            while not header.endswith(b"end_header\n") and len(header) < 65536:
                ch = f.read(1)
                if not ch:
                    return {"error": "header truncated"}
                header += ch
            data = f.read()
        text = header.decode("ascii", errors="ignore")
        m_count = re.search(r"element vertex (\d+)", text)
        if not m_count:
            return {"error": "no vertex count"}
        count = int(m_count.group(1))
        properties = re.findall(r"property float ([a-zA-Z_0-9]+)", text)
        if not all(c in properties for c in ("x", "y", "z")):
            return {"error": "no xyz fields"}
        nfloats = len(properties)
        expected = count * nfloats * 4
        if len(data) < expected:
            return {"error": "data truncated"}
        # .copy() — frombuffer is read-only; we translate xyz in place below.
        arr = np.frombuffer(data[:expected], dtype=np.float32).reshape(count, nfloats).copy()
        xi, yi, zi = (properties.index(c) for c in ("x", "y", "z"))
        idx = [xi, yi, zi]
        pts = arr[:, idx].astype(np.float64)

        # Stage 1: drop gross outliers (> 8 robust-sigma) via per-axis MAD so a
        # handful of far floaters can't bias the percentile box below.
        med = np.median(pts, axis=0)
        sigma = np.maximum(np.median(np.abs(pts - med), axis=0) * 1.4826, 1e-6)
        gross_in = np.all(np.abs(pts - med) <= 8.0 * sigma, axis=1)
        ref = pts[gross_in] if int(gross_in.sum()) >= 64 else pts

        # Stage 2: tight robust per-axis box from the cleaned reference set,
        # expanded by a margin so we never clip into the subject.
        lo = np.percentile(ref, lo_pct, axis=0)
        hi = np.percentile(ref, hi_pct, axis=0)
        span = np.maximum(hi - lo, 1e-6)
        lo_m = lo - span * margin
        hi_m = hi + span * margin
        inside = np.all((pts >= lo_m) & (pts <= hi_m), axis=1)
        new_count = int(inside.sum())

        cropped = True
        if count == 0 or new_count < max(64, int(count * (1.0 - max_drop_frac))):
            # Cropping would eat too much — keep everything, only recenter.
            cropped = False
            kept = arr
            new_count = count
        else:
            kept = arr[inside]
        dropped = count - new_count

        # Recenter on the robust median of the kept gaussians.
        center = np.median(kept[:, idx], axis=0).astype(np.float32)
        kept[:, xi] -= center[0]
        kept[:, yi] -= center[1]
        kept[:, zi] -= center[2]

        new_header = text.replace(f"element vertex {count}",
                                  f"element vertex {new_count}", 1)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        with out_path.open("wb") as f:
            f.write(new_header.encode("ascii"))
            f.write(np.ascontiguousarray(kept).tobytes())

        return {
            "cropped": cropped,
            "original_count": count,
            "kept_count": new_count,
            "dropped_count": dropped,
            "dropped_pct": round(dropped / count * 100, 1) if count else 0,
            "center": [round(float(c), 3) for c in center],
        }
    except Exception as e:
        try:
            shutil.copy(in_path, out_path)
        except Exception:
            pass
        return {"error": str(e)}


def find_latest_checkpoint(outputs_dir: Path) -> tuple[Optional[Path], int]:
    """Locate the highest-step checkpoint of a previous splatfacto run.

    Returns (run_dir, step) where run_dir is the timestamped folder
    containing the checkpoint and step is the iteration count.
    Returns (None, 0) if no checkpoint exists."""
    splatfacto_root = outputs_dir / "workspace" / "splatfacto"
    if not splatfacto_root.exists():
        return None, 0
    best_dir, best_step = None, 0
    for run in sorted(splatfacto_root.iterdir(), reverse=True):
        if not run.is_dir():
            continue
        ckpt_dir = run / "nerfstudio_models"
        if not ckpt_dir.exists():
            continue
        for ckpt in ckpt_dir.glob("step-*.ckpt"):
            m = re.match(r"step-(\d+)\.ckpt", ckpt.name)
            if not m:
                continue
            step = int(m.group(1))
            if step > best_step:
                best_step = step
                best_dir  = run
    return best_dir, best_step

# Regex patterns for progress parsing from subprocess stdout.
_RE_COLMAP_FEAT  = re.compile(r'Extracting features.*?\[(\d+)/(\d+)\]', re.IGNORECASE)
_RE_COLMAP_MATCH = re.compile(r'[Mm]atch.*?\[(\d+)/(\d+)\]')
_RE_COLMAP_REG   = re.compile(r'[Rr]egister\w*\s+image.*?(\d+)\s+\((\d+)\)', re.IGNORECASE)
_RE_COLMAP_MAP   = re.compile(r'[Mm]apping.*?\[(\d+)/(\d+)\]', re.IGNORECASE)
_RE_TRAIN_STEP   = re.compile(r'[Ss]tep[:\s]+(\d+)\s*[/,|]\s*(\d+)')
_RE_TRAIN_STEP2  = re.compile(r'(\d{3,})\s*/\s*(\d{4,})')  # fallback: bare N/M
# nerfstudio prints a line like "[NOTE] Open the viewer at https://viewer.nerf.studio/?websocket_url=ws://localhost:7007"
_RE_VIEWER_URL   = re.compile(r'(https?://[^\s]*?(?:viewer\.nerf\.studio|localhost:\d+)[^\s]*)')


def get_gpu_info() -> dict:
    """Return GPU name and memory stats via torch.cuda, or a graceful fallback."""
    try:
        import torch  # type: ignore
        if torch.cuda.is_available():
            idx = 0
            name = torch.cuda.get_device_name(idx)
            props = torch.cuda.get_device_properties(idx)
            total_gb = round(props.total_memory / 1024 ** 3, 1)
            try:
                free_bytes, _ = torch.cuda.mem_get_info(idx)
                free_gb = round(free_bytes / 1024 ** 3, 1)
            except Exception:
                free_gb = None
            short = name.replace("NVIDIA ", "").replace("GeForce ", "")
            return {
                "available": True,
                "name": name,
                "short": short,
                "total_gb": total_gb,
                "free_gb": free_gb,
                "label": f"{short} / {total_gb}G",
            }
    except Exception:
        pass
    return {"available": False, "name": "unknown", "short": "CPU?", "label": "no GPU"}


@dataclass
class JobConfig:
    job_id: str
    job_dir: Path
    matcher: str = "sequential"
    max_iters: int = 30000
    extract_fps: int = 2
    blur_threshold: float = 0.0       # 0 disables blur filter
    dedupe_threshold: int = 0         # 0 disables dedup; 4 ≈ drop near-identical
    prune_opacity_logit: float = -2.5 # PLY pruning threshold (sigmoid≈0.076)
    remove_background: bool = False   # rembg subject isolation (mask the loss)
    mask_model: str = "isnet-general-use"  # rembg model for background removal
    refine_geometry: bool = False     # scale-reg + bilateral grid + floater cull
    render_turntable: bool = False    # render an orbit MP4 preview after export
    clip_start: float = 0.0           # trim: seconds into the video to start extraction
    clip_end: float = 0.0             # trim: seconds to stop (0 = until the end)
    cancel_event: Optional[threading.Event] = None
    _proc_ref: dict = field(default_factory=dict)

    @property
    def upload_dir(self) -> Path:
        return self.job_dir / "upload"

    @property
    def images_dir(self) -> Path:
        return self.job_dir / "images"

    @property
    def workspace_dir(self) -> Path:
        return self.job_dir / "workspace"

    @property
    def outputs_dir(self) -> Path:
        return self.job_dir / "outputs"

    @property
    def exports_dir(self) -> Path:
        return self.job_dir / "exports"

    @property
    def ply_path(self) -> Path:
        return self.job_dir / "splat.ply"

    @property
    def turntable_path(self) -> Path:
        return self.job_dir / "turntable.mp4"

    @property
    def log_path(self) -> Path:
        return self.job_dir / "log.txt"


@dataclass
class JobUpdate:
    stage: str
    stage_progress: float = 0.0
    overall: float = 0.0
    message: str = ""
    error: Optional[str] = None
    viewer_url: Optional[str] = None      # Live nerfstudio viewer URL (training stage)
    capture_report: Optional[dict] = None  # Pre-flight frame analysis (one-shot)


ProgressCb = Callable[[JobUpdate], None]


def _emit(cb: ProgressCb, cfg: JobConfig, stage: str, stage_progress: float, message: str = ""):
    stage_idx = STAGES.index(stage)
    n = len(STAGES) - 1
    overall = min(1.0, (stage_idx + stage_progress) / n)
    cb(JobUpdate(stage=stage, stage_progress=stage_progress, overall=overall, message=message))


def _log(cfg: JobConfig, line: str):
    cfg.log_path.parent.mkdir(parents=True, exist_ok=True)
    with cfg.log_path.open("a", encoding="utf-8") as f:
        f.write(line.rstrip() + "\n")


def _check_cancel(cfg: JobConfig):
    if cfg.cancel_event is not None and cfg.cancel_event.is_set():
        raise JobCancelled("cancelled by user")


def _run(
    cmd: list[str],
    cfg: JobConfig,
    cwd: Path | None = None,
    env: dict | None = None,
    on_line: Callable[[str], None] | None = None,
) -> int:
    """Run a subprocess, streaming stdout+stderr to the job log file.
    Optional on_line(line) callback receives each output line for progress parsing.
    Returns the process exit code.
    """
    _check_cancel(cfg)

    full_env = os.environ.copy()
    # Put COLMAP_LIB *before* COLMAP_BIN so the DLL search for colmap.exe finds
    # boost/ceres/cudart/Qt/etc. immediately. Also include ffmpeg/bin so
    # nerfstudio's check_ffmpeg_installed sees it.
    full_env["PATH"] = (
        f"{COLMAP_LIB};{COLMAP_BIN};{FFMPEG_BIN};{VENV_BIN};"
        f"{full_env.get('PATH', '')}"
    )
    full_env["QT_PLUGIN_PATH"] = f"{COLMAP_LIB / 'plugins'};{full_env.get('QT_PLUGIN_PATH', '')}"
    full_env["PYTHONIOENCODING"] = "utf-8"
    full_env["PYTHONUTF8"] = "1"
    if env:
        full_env.update(env)
    _log(cfg, f"\n$ {' '.join(str(c) for c in cmd)}")

    creationflags = 0
    if sys.platform == "win32":
        creationflags = subprocess.CREATE_NEW_PROCESS_GROUP

    proc = subprocess.Popen(
        cmd, cwd=cwd or cfg.job_dir, env=full_env,
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        text=True, encoding="utf-8", errors="replace", bufsize=1,
        creationflags=creationflags,
    )
    cfg._proc_ref["proc"] = proc
    assert proc.stdout is not None
    try:
        for line in proc.stdout:
            _log(cfg, line)
            if on_line is not None:
                try:
                    on_line(line)
                except Exception:
                    pass
        rc = proc.wait()
    finally:
        cfg._proc_ref.pop("proc", None)
    _check_cancel(cfg)
    return rc


def kill_active_subprocess(cfg: JobConfig):
    """Kill the subprocess currently running for this job, if any."""
    if cfg.cancel_event is not None:
        cfg.cancel_event.set()
    proc = cfg._proc_ref.get("proc")
    if proc is not None and proc.poll() is None:
        try:
            proc.terminate()
        except Exception:
            pass


def classify_upload(cfg: JobConfig) -> str:
    for p in cfg.upload_dir.iterdir():
        if p.suffix.lower() in VIDEO_EXTS:
            return "video"
    return "photos"


# ── Pipeline stages ──────────────────────────────────────────────────────────

def stage_prepare(cfg: JobConfig, cb: ProgressCb):
    _emit(cb, cfg, "preparing", 0.1, "Sorting uploaded files")
    cfg.images_dir.mkdir(parents=True, exist_ok=True)
    cfg.workspace_dir.mkdir(parents=True, exist_ok=True)


def _count_images(d: Path) -> int:
    if not d.exists():
        return 0
    return sum(1 for p in d.iterdir()
               if p.suffix.lower() in IMAGE_EXTS)


def stage_extract_frames(cfg: JobConfig, cb: ProgressCb):
    # Idempotent: on a retry the frames are already extracted+filtered. Reuse
    # them so we don't re-run ffmpeg / blur / dedup (and don't risk dropping
    # frames a second time).
    existing = _count_images(cfg.images_dir)
    if existing >= 20:
        _log(cfg, f"✓ Reusing {existing} extracted frames")
        _emit(cb, cfg, "extracting", 1.0, f"Reusing {existing} frames")
        return

    kind = classify_upload(cfg)
    if kind == "photos":
        photos = [p for p in cfg.upload_dir.iterdir() if p.suffix.lower() in IMAGE_EXTS]
        if not photos:
            raise RuntimeError("No usable photos or video found in upload")
        for i, p in enumerate(sorted(photos)):
            shutil.copy(p, cfg.images_dir / p.name)
            if i % 10 == 0:
                _emit(cb, cfg, "extracting", (i + 1) / len(photos), f"Copying photo {i + 1}/{len(photos)}")
        _emit(cb, cfg, "extracting", 1.0, f"{len(photos)} photos ready")
        return

    video = next(p for p in cfg.upload_dir.iterdir() if p.suffix.lower() in VIDEO_EXTS)

    # Optional trim: only extract a [clip_start, clip_end] slice. Input seek
    # (-ss before -i) is fast; -t (output duration) is version-agnostic and
    # always means "seconds to read from the seek point".
    start = max(0.0, cfg.clip_start)
    end = cfg.clip_end if cfg.clip_end and cfg.clip_end > start else 0.0
    trim_note = ""
    cmd = ["ffmpeg", "-y"]
    if start > 0.05:
        cmd += ["-ss", f"{start:.3f}"]
    cmd += ["-i", str(video)]
    if end > start:
        cmd += ["-t", f"{end - start:.3f}"]
        trim_note = f" [trim {start:.1f}s→{end:.1f}s]"
    elif start > 0.05:
        trim_note = f" [trim from {start:.1f}s]"
    cmd += [
        "-vf", f"fps={cfg.extract_fps},scale=1600:-2",
        "-q:v", "2",
        str(cfg.images_dir / "frame_%05d.jpg"),
    ]
    _emit(cb, cfg, "extracting", 0.1,
          f"Extracting frames at {cfg.extract_fps} fps from {video.name}{trim_note}")
    rc = _run(cmd, cfg)
    if rc != 0:
        raise RuntimeError(f"ffmpeg failed (exit {rc})")
    n = len(list(cfg.images_dir.iterdir()))
    if n < 20:
        raise RuntimeError(
            f"Only {n} frames extracted — need at least 20. "
            "Use a longer video (60–90s) or increase frame extraction FPS."
        )

    # Blur filter — drop the worst frames before COLMAP.
    if cfg.blur_threshold > 0:
        _emit(cb, cfg, "extracting", 0.78, f"Filtering blurry frames (threshold {cfg.blur_threshold:.0f})")
        removed = filter_blurry_frames(cfg.images_dir, cfg.blur_threshold)
        if removed:
            _log(cfg, f"✓ Removed {removed} blurry frames")
            n = len(list(cfg.images_dir.iterdir()))

    # Deduplicate consecutive near-identical frames (slow camera motion).
    if cfg.dedupe_threshold > 0:
        _emit(cb, cfg, "extracting", 0.88,
              f"Deduplicating frames (hamming ≤ {cfg.dedupe_threshold})")
        dup_removed = dedupe_consecutive_frames(cfg.images_dir, cfg.dedupe_threshold)
        if dup_removed:
            _log(cfg, f"✓ Dropped {dup_removed} near-duplicate frames")
            n = len(list(cfg.images_dir.iterdir()))

    _emit(cb, cfg, "extracting", 1.0, f"{n} frames ready")


def stage_preflight(cfg: JobConfig, cb: ProgressCb):
    """Analyse the extracted frames and surface capture-quality warnings BEFORE
    the slow COLMAP pass, so a doomed capture is caught in seconds. Advisory
    only — never blocks the pipeline."""
    # Skip if COLMAP already succeeded (retry path) — nothing to warn about.
    if (cfg.workspace_dir / "transforms.json").exists():
        return
    _emit(cb, cfg, "extracting", 1.0, "Checking capture quality…")
    report = analyze_capture(cfg.images_dir)
    if not report.get("warnings"):
        _log(cfg, f"✓ Pre-flight OK — parallax {report.get('parallax', '?')}px, "
                  f"{report.get('n_frames', '?')} frames")
        cb(JobUpdate(stage="extracting", stage_progress=1.0,
                     overall=min(1.0, (STAGES.index('extracting') + 1) / (len(STAGES) - 1)),
                     message="Capture looks good", capture_report=report))
        return

    verdict = report.get("verdict", "good")
    icon = "‼" if verdict == "poor" else "⚠"
    _log(cfg, f"{icon} Pre-flight: capture looks {verdict.upper()} "
              f"(parallax {report.get('parallax', '?')}px, "
              f"sky {report.get('sky_frac', 0)*100:.0f}%, "
              f"{report.get('n_frames', '?')} frames)")
    for w in report["warnings"]:
        _log(cfg, f"  {('!!' if w['level'] == 'error' else '·')} {w['msg']}")
    headline = report["warnings"][0]["msg"]
    cb(JobUpdate(
        stage="extracting", stage_progress=1.0,
        overall=min(1.0, (STAGES.index('extracting') + 1) / (len(STAGES) - 1)),
        message=(f"⚠ {headline}" if verdict != "good" else "Capture looks good"),
        capture_report=report,
    ))


def stage_colmap(cfg: JobConfig, cb: ProgressCb):
    # Idempotent: on a retry the reconstruction is already on disk. Reuse it so
    # we skip the slow feature-extraction / matching / mapping passes.
    transforms = cfg.workspace_dir / "transforms.json"
    if transforms.exists():
        try:
            n = len(json.loads(transforms.read_text(encoding="utf-8")).get("frames", []))
        except Exception:
            n = 0
        if n >= MIN_POSES:
            _log(cfg, f"✓ Reusing COLMAP reconstruction — {n} camera poses")
            _emit(cb, cfg, "colmap", 1.0, f"Reusing {n} camera poses")
            return

    _emit(cb, cfg, "colmap", 0.03, "Running COLMAP — feature extraction")

    state = {"phase": "feat", "last_p": 0.03}

    def on_line(line: str):
        p = state["last_p"]

        # Feature extraction: 0.03 – 0.30
        m = _RE_COLMAP_FEAT.search(line)
        if m:
            cur, tot = int(m.group(1)), int(m.group(2))
            p = 0.03 + 0.27 * (cur / max(tot, 1))
            state["phase"] = "feat"
            _emit(cb, cfg, "colmap", p, f"Feature extraction {cur}/{tot}")
            state["last_p"] = p
            return

        # Feature matching: 0.30 – 0.60
        m = _RE_COLMAP_MATCH.search(line)
        if m:
            cur, tot = int(m.group(1)), int(m.group(2))
            p = 0.30 + 0.30 * (cur / max(tot, 1))
            state["phase"] = "match"
            _emit(cb, cfg, "colmap", p, f"Feature matching {cur}/{tot}")
            state["last_p"] = p
            return

        # Reconstruction / mapping: 0.60 – 0.95
        m = _RE_COLMAP_REG.search(line) or _RE_COLMAP_MAP.search(line)
        if m and state["phase"] in ("match", "map"):
            cur = int(m.group(1))
            tot = int(m.group(2)) if m.lastindex >= 2 else max(cur, 10)
            p = 0.60 + 0.35 * min(cur / max(tot, 1), 1.0)
            state["phase"] = "map"
            _emit(cb, cfg, "colmap", p, f"Reconstruction — {cur} images registered")
            state["last_p"] = p

    cmd = [
        str(VENV_BIN / "ns-process-data.exe"), "images",
        "--data", str(cfg.images_dir),
        "--output-dir", str(cfg.workspace_dir),
        "--matching-method", cfg.matcher,
        "--gpu",
    ]
    rc = _run(cmd, cfg, on_line=on_line)
    if rc != 0:
        raise RuntimeError(f"ns-process-data (COLMAP) failed (exit {rc})")

    # ── Validate reconstruction quality ──────────────────────────────────────
    transforms = cfg.workspace_dir / "transforms.json"
    if not transforms.exists():
        raise RuntimeError(
            "COLMAP finished but produced no transforms.json — reconstruction failed.\n"
            "Tips: slower camera movement, better lighting, more image overlap, try 'vocab_tree' matcher."
        )
    try:
        data = json.loads(transforms.read_text(encoding="utf-8"))
        n_poses = len(data.get("frames", []))
    except json.JSONDecodeError:
        raise RuntimeError("transforms.json is corrupted — COLMAP output may be incomplete")

    if n_poses < MIN_POSES:
        raise RuntimeError(
            f"COLMAP registered only {n_poses} camera poses (need {MIN_POSES}+).\n"
            "Capture with more overlap, better lighting, or try 'vocab_tree' matcher for unordered photos."
        )

    _log(cfg, f"✓ COLMAP registered {n_poses} camera poses")
    _emit(cb, cfg, "colmap", 1.0, f"Camera poses recovered — {n_poses} frames registered")


def _replicate_masks_downscaled(workspace_dir: Path, masks_dir: Path, log=None) -> None:
    """Mirror the full-res masks into masks_<N>/ for every images_<N>/ folder
    that ns-process-data generated.

    nerfstudio auto-picks a training downscale (e.g. 2) based on which
    images_<N>/ folder exists, then looks for masks at the SAME level
    (masks_2/...). Since we generate masks after ns-process-data ran, those
    folders don't exist yet — without this, training dies with
    FileNotFoundError: masks_2/frame_xxxxx.png.

    splatfacto asserts the mask and image have identical H,W, so we resize
    each mask to the *exact* size of the matching downscaled image, using
    NEAREST so the mask stays binary."""
    try:
        from PIL import Image  # type: ignore
    except ImportError:
        return
    for img_dir in sorted(workspace_dir.glob("images_*")):
        suffix = img_dir.name.split("_", 1)[1]
        if not suffix.isdigit():
            continue
        mdir = workspace_dir / f"masks_{suffix}"
        mdir.mkdir(exist_ok=True)
        made = 0
        for mp in masks_dir.glob("*.png"):
            dst = mdir / mp.name
            if dst.exists():
                continue
            img_match = next(iter(img_dir.glob(f"{mp.stem}.*")), None)
            if img_match is None:
                continue
            try:
                with Image.open(img_match) as di:
                    tw, th = di.size
                with Image.open(mp) as m:
                    m.resize((tw, th), Image.NEAREST).save(dst)
                made += 1
            except Exception:
                pass
        if made and log:
            log(f"✓ Replicated {made} masks → {mdir.name}")


def apply_background_masks(cfg: JobConfig, cb: ProgressCb) -> int:
    """Isolate the subject in every registered frame with rembg and wire the
    masks into transforms.json.

    Important: COLMAP already ran on the FULL images (background texture helps
    pose estimation). We only mask the *training loss* — splatfacto multiplies
    both the rendered and ground-truth image by the mask, so the background is
    never supervised and its gaussians get culled. The result is a clean
    subject floating without its environment.

    Masks are written to workspace/masks/<stem>.png (255 = subject, 0 = bg)
    and each frame in transforms.json gains a `mask_path`. Returns the number
    of masks written (0 if rembg is unavailable or nothing to do)."""
    transforms = cfg.workspace_dir / "transforms.json"
    if not transforms.exists():
        return 0
    # Point rembg at the bundled model cache before it's imported, unless the
    # launcher already set U2NET_HOME.
    os.environ.setdefault("U2NET_HOME", str(REMBG_HOME))
    try:
        from rembg import new_session, remove  # type: ignore
        from PIL import Image  # type: ignore
    except ImportError:
        _log(cfg, "rembg not installed — skipping background removal")
        return 0

    try:
        data = json.loads(transforms.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return 0
    frames = data.get("frames", [])
    if not frames:
        return 0

    masks_dir = cfg.workspace_dir / "masks"
    masks_dir.mkdir(parents=True, exist_ok=True)

    # Idempotent: on a retry the masks are usually already there. Skip if every
    # frame already carries a mask_path that exists on disk.
    if frames and all(
        fr.get("mask_path") and (cfg.workspace_dir / fr["mask_path"]).exists()
        for fr in frames
    ):
        _log(cfg, f"✓ Reusing {len(frames)} existing background masks")
        _replicate_masks_downscaled(cfg.workspace_dir, masks_dir,
                                    log=lambda m: _log(cfg, m))
        return len(frames)

    _emit(cb, cfg, "colmap", 1.0, f"Isolating subject in {len(frames)} frames…")
    try:
        sess = new_session(cfg.mask_model)
    except Exception as e:
        _log(cfg, f"rembg session failed ({cfg.mask_model}): {e}")
        return 0

    written = 0       # frames with a real rembg mask
    fallback = 0      # frames that fell back to a full-frame (all-white) mask
    unlocatable = 0   # frames whose image couldn't be found at all
    n = len(frames)
    for i, fr in enumerate(frames):
        _check_cancel(cfg)
        rel = fr.get("file_path", "")
        if not rel:
            unlocatable += 1
            continue
        img_path = cfg.workspace_dir / rel
        if not img_path.exists():
            # transforms file_path can omit the extension or the images/ prefix
            cand = cfg.workspace_dir / "images" / Path(rel).name
            if cand.exists():
                img_path = cand
            else:
                hit = next(iter(cfg.workspace_dir.glob(f"images/{Path(rel).stem}.*")), None)
                if hit is None:
                    unlocatable += 1
                    continue
                img_path = hit

        mask_name = Path(rel).stem + ".png"
        try:
            with Image.open(img_path) as im:
                mask = remove(im.convert("RGB"), session=sess,
                              only_mask=True, post_process_mask=True)
            mask.save(masks_dir / mask_name)
            fr["mask_path"] = f"masks/{mask_name}"
            written += 1
        except Exception as e:
            # splatfacto requires masks for EVERY frame or none (it asserts
            # len(masks)==len(images)). A per-frame rembg failure must not
            # break that invariant, so we write an all-white mask (keep the
            # whole frame) — a no-op for that frame's loss.
            _log(cfg, f"mask failed for {img_path.name}: {e} — full-frame fallback")
            try:
                with Image.open(img_path) as im:
                    Image.new("L", im.size, 255).save(masks_dir / mask_name)
                fr["mask_path"] = f"masks/{mask_name}"
                fallback += 1
            except Exception:
                unlocatable += 1
        if i % 5 == 0 or i == n - 1:
            _emit(cb, cfg, "colmap", 1.0, f"Isolating subject {i + 1}/{n}")

    total = written + fallback
    # If even one frame couldn't get a mask, the all-or-nothing invariant is
    # broken — strip every mask_path and train on full frames rather than
    # crash nerfstudio.
    if unlocatable > 0:
        for fr in frames:
            fr.pop("mask_path", None)
        transforms.write_text(json.dumps(data, indent=2), encoding="utf-8")
        _log(cfg, f"Background removal aborted — {unlocatable} frames had no "
                  f"locatable image; training on full frames to stay safe")
        return 0

    if total:
        transforms.write_text(json.dumps(data, indent=2), encoding="utf-8")
        _log(cfg, f"✓ Background removed — {written}/{n} masked"
                  + (f" ({fallback} full-frame fallbacks)" if fallback else ""))
        # Mirror masks into the downscaled folders nerfstudio will train on.
        _replicate_masks_downscaled(cfg.workspace_dir, masks_dir,
                                    log=lambda m: _log(cfg, m))
    else:
        _log(cfg, "Background removal produced no masks — training on full frames")
    return total


def stage_train(cfg: JobConfig, cb: ProgressCb):
    _emit(cb, cfg, "training", 0.01, f"Training splatfacto ({cfg.max_iters:,} iterations)")
    cfg.outputs_dir.mkdir(parents=True, exist_ok=True)

    # ── Resume from existing checkpoint if available ───────────────────────
    resume_dir, resume_step = find_latest_checkpoint(cfg.outputs_dir)
    if resume_dir and resume_step >= cfg.max_iters:
        _log(cfg, f"✓ Training already complete at step {resume_step:,} "
                  f"(target {cfg.max_iters:,}) — skipping to export")
        _emit(cb, cfg, "training", 1.0,
              f"resumed at step {resume_step:,} (already done)")
        return

    last_step = [resume_step]
    last_emit_t = [0.0]
    viewer_url = [None]   # Captured from nerfstudio stdout; reused in subsequent emits
    oom_seen = [False]    # Set if the run prints a CUDA out-of-memory error
    # Rolling (time, step) samples for a live it/s → ETA estimate.
    eta_samples: list[tuple[float, int]] = []

    def _fmt_eta(seconds: float) -> str:
        seconds = max(0, int(seconds))
        if seconds >= 3600:
            return f"~{seconds // 3600}h {seconds % 3600 // 60}m left"
        if seconds >= 60:
            return f"~{seconds // 60}m left"
        return f"~{seconds}s left"

    def _eta_suffix(step: int, now: float) -> str:
        eta_samples.append((now, step))
        # Keep a ~30 s window so the rate reflects current speed.
        while len(eta_samples) > 2 and now - eta_samples[0][0] > 30:
            eta_samples.pop(0)
        if len(eta_samples) >= 2:
            dt = eta_samples[-1][0] - eta_samples[0][0]
            ds = eta_samples[-1][1] - eta_samples[0][1]
            if dt > 0 and ds > 0:
                rate = ds / dt
                remaining = (cfg.max_iters - step) / rate
                return " · " + _fmt_eta(remaining)
        return ""

    def on_line(line: str):
        # Detect CUDA OOM so we can retry at reduced resolution.
        low = line.lower()
        if ("out of memory" in low or "cuda error: out of memory" in low
                or "torch.cuda.outofmemory" in low):
            oom_seen[0] = True

        # Capture the live viewer URL once (first match wins).
        if viewer_url[0] is None:
            vm = _RE_VIEWER_URL.search(line)
            if vm:
                viewer_url[0] = vm.group(1).rstrip(' .,;)]')
                _log(cfg, f"✓ Live viewer URL captured: {viewer_url[0]}")
                # Push an immediate update so the UI shows the iframe ASAP.
                p = last_step[0] / max(cfg.max_iters, 1) if last_step[0] else 0.0
                cb(JobUpdate(
                    stage="training",
                    stage_progress=p,
                    overall=min(1.0, (STAGES.index("training") + p) / (len(STAGES) - 1)),
                    message=f"Live viewer ready" if last_step[0] == 0 else f"Step {last_step[0]:,} / {cfg.max_iters:,}",
                    viewer_url=viewer_url[0],
                ))

        m = _RE_TRAIN_STEP.search(line) or _RE_TRAIN_STEP2.search(line)
        if not m:
            return
        step = int(m.group(1))
        total = int(m.group(2))
        # Ignore spurious matches (wrong total or step going backwards)
        if abs(total - cfg.max_iters) > cfg.max_iters * 0.1 or step < last_step[0]:
            return
        last_step[0] = step
        now = time.time()
        if now - last_emit_t[0] < 1.0:
            return  # throttle to 1 emit/sec
        last_emit_t[0] = now
        p = step / max(total, 1)
        eta = _eta_suffix(step, now)
        # Propagate the viewer URL on every subsequent emit so reconnecting
        # clients (detach -> reopen popup) immediately get the iframe.
        cb(JobUpdate(
            stage="training",
            stage_progress=p,
            overall=min(1.0, (STAGES.index("training") + p) / (len(STAGES) - 1)),
            message=f"Step {step:,} / {total:,}{eta}",
            viewer_url=viewer_url[0],
        ))

    def build_cmd(downscale: Optional[int]) -> list:
        c = [
            str(VENV_BIN / "ns-train.exe"), "splatfacto",
            "--data", str(cfg.workspace_dir),
            "--max-num-iterations", str(cfg.max_iters),
            "--output-dir", str(cfg.outputs_dir),
            "--vis", "viewer",
            "--viewer.quit-on-train-completion", "True",
        ]
        if cfg.refine_geometry:
            # Scale regularization penalises extreme anisotropy — kills the
            # "needle"/spike gaussians that make splats look spiky. Cheap.
            c += ["--pipeline.model.use-scale-regularization", "True"]
            gpu = get_gpu_info()
            total_gb = gpu.get("total_gb") or 0
            # Bilateral grid compensates per-image exposure drift but costs
            # VRAM — only on roomy cards, and never on an OOM retry.
            if total_gb >= 12 and downscale is None:
                c += ["--pipeline.model.use-bilateral-grid", "True"]
        if resume_dir:
            # Nerfstudio 1.x expects --load-dir to point at the EXPERIMENT
            # folder (the one with config.yml), not nerfstudio_models/.
            c += ["--load-dir", str(resume_dir)]
        # The dataparser subcommand must come LAST. We only add it to halve
        # the training resolution on an OOM retry (~4× less VRAM).
        if downscale is not None:
            c += ["nerfstudio-data", "--downscale-factor", str(downscale)]
        return c

    if cfg.refine_geometry:
        _log(cfg, "✓ Geometry refine enabled (scale-reg" +
                  (" + bilateral grid" if (get_gpu_info().get("total_gb") or 0) >= 12 else "") + ")")
    if resume_dir:
        _log(cfg, f"↻ Resuming from step {resume_step:,} in {resume_dir.name}")
        _emit(cb, cfg, "training",
              resume_step / max(cfg.max_iters, 1),
              f"Resuming from step {resume_step:,}")

    # ── First attempt (full resolution) ───────────────────────────────────
    rc = _run(build_cmd(None), cfg, on_line=on_line)

    # ── CUDA OOM auto-recovery: retry once at half resolution ─────────────
    if rc != 0 and oom_seen[0]:
        _log(cfg, "!!! CUDA out of memory — retrying at half resolution "
                  "(downscale 2, ~4× less VRAM)")
        _emit(cb, cfg, "training", last_step[0] / max(cfg.max_iters, 1),
              "GPU out of memory — retrying at half resolution")
        oom_seen[0] = False
        eta_samples.clear()
        last_emit_t[0] = 0.0
        rc = _run(build_cmd(2), cfg, on_line=on_line)
        if rc != 0 and oom_seen[0]:
            raise RuntimeError(
                "ns-train ran out of GPU memory even at half resolution. "
                "Free up VRAM (close other GPU apps), lower the iteration "
                "count, or use a shorter capture."
            )

    if rc != 0:
        raise RuntimeError(f"ns-train failed (exit {rc})")
    _emit(cb, cfg, "training", 1.0, f"Training complete — {last_step[0]:,} steps")


def stage_export(cfg: JobConfig, cb: ProgressCb):
    _emit(cb, cfg, "exporting", 0.1, "Exporting PLY")
    runs = sorted(
        (cfg.outputs_dir / "workspace" / "splatfacto").iterdir(),
        key=lambda p: p.name, reverse=True,
    ) if (cfg.outputs_dir / "workspace" / "splatfacto").exists() else []
    if not runs:
        for sub in cfg.outputs_dir.rglob("config.yml"):
            runs = [sub.parent]
            break
    if not runs:
        raise RuntimeError("No trained model found to export")
    config_yml = runs[0] / "config.yml"
    cfg.exports_dir.mkdir(parents=True, exist_ok=True)
    cmd = [
        str(VENV_BIN / "ns-export.exe"), "gaussian-splat",
        "--load-config", str(config_yml),
        "--output-dir", str(cfg.exports_dir),
    ]
    rc = _run(cmd, cfg)
    if rc != 0:
        raise RuntimeError(f"ns-export failed (exit {rc})")
    plies = list(cfg.exports_dir.glob("*.ply"))
    if not plies:
        raise RuntimeError("ns-export finished but produced no .ply file")

    # Copy raw output, then optimise in place.
    shutil.copy(plies[0], cfg.ply_path)

    if cfg.prune_opacity_logit > -10.0:  # sentinel: very-low value disables
        _emit(cb, cfg, "exporting", 0.85, "Pruning low-opacity gaussians")
        raw_backup = cfg.ply_path.with_suffix(".raw.ply")
        shutil.move(cfg.ply_path, raw_backup)
        stats = optimize_ply(raw_backup, cfg.ply_path,
                             opacity_min_logit=cfg.prune_opacity_logit)
        if "error" in stats:
            # On any failure, keep the original PLY untouched
            shutil.move(raw_backup, cfg.ply_path)
            _log(cfg, f"PLY optimisation skipped: {stats['error']}")
        else:
            _log(cfg,
                 f"✓ Pruned {stats['pruned_count']:,} gaussians "
                 f"({stats['pruned_pct']}%) — "
                 f"PLY {stats['original_size_mb']}MB → "
                 f"{stats['new_size_mb']}MB "
                 f"(-{stats['size_reduction_pct']}%)")
            raw_backup.unlink(missing_ok=True)

    # ── Floater removal (geometry refine) ─────────────────────────────────
    if cfg.refine_geometry:
        _emit(cb, cfg, "exporting", 0.93, "Removing floater gaussians")
        floater_backup = cfg.ply_path.with_suffix(".prefloater.ply")
        shutil.move(cfg.ply_path, floater_backup)
        fstats = remove_floaters(floater_backup, cfg.ply_path)
        if "error" in fstats:
            shutil.move(floater_backup, cfg.ply_path)
            _log(cfg, f"Floater removal skipped: {fstats['error']}")
        elif fstats.get("skipped"):
            shutil.move(floater_backup, cfg.ply_path)
            _log(cfg, f"Floater removal skipped: {fstats.get('reason', 'cap hit')}")
        else:
            _log(cfg,
                 f"✓ Removed {fstats['dropped_count']:,} floater gaussians "
                 f"({fstats['dropped_pct']}%)")
            floater_backup.unlink(missing_ok=True)

        # Auto-crop background + recenter (drop rows + translate xyz — SH-safe).
        _emit(cb, cfg, "exporting", 0.96, "Cropping background + centering")
        crop_backup = cfg.ply_path.with_suffix(".precrop.ply")
        shutil.move(cfg.ply_path, crop_backup)
        cstats = crop_and_center(crop_backup, cfg.ply_path)
        if "error" in cstats:
            shutil.move(crop_backup, cfg.ply_path)
            _log(cfg, f"Crop/center skipped: {cstats['error']}")
        else:
            _log(cfg, (f"✓ Cropped {cstats['dropped_count']:,} background gaussians "
                       f"({cstats['dropped_pct']}%) + recentered"
                       if cstats.get('cropped')
                       else "✓ Recentered (crop skipped — would cut too much)"))
            crop_backup.unlink(missing_ok=True)

    _emit(cb, cfg, "exporting", 1.0, f"PLY ready: {cfg.ply_path.name}")


def _find_trained_run(cfg: JobConfig) -> Optional[Path]:
    """Locate the timestamped nerfstudio run dir that holds config.yml."""
    base = cfg.outputs_dir / "workspace" / "splatfacto"
    runs = sorted(base.iterdir(), key=lambda p: p.name, reverse=True) if base.exists() else []
    if runs:
        return runs[0]
    for sub in cfg.outputs_dir.rglob("config.yml"):
        return sub.parent
    return None


def _count_dataset_cameras(cfg: JobConfig) -> int:
    """Best-effort count of training cameras from transforms.json (0 if unknown)."""
    candidates = [cfg.workspace_dir / "transforms.json", *cfg.job_dir.rglob("transforms.json")]
    for tf in candidates:
        try:
            if tf.exists():
                frames = json.loads(tf.read_text(encoding="utf-8")).get("frames", [])
                if frames:
                    return len(frames)
        except Exception:
            continue
    return 0


def stage_render_turntable(cfg: JobConfig, cb: ProgressCb):
    """Render an orbit/turntable MP4 from the trained model via ns-render.

    Best-effort: the PLY is the primary deliverable, so a render failure is
    logged and swallowed (never fails the job). The clip length is bounded to
    ~5-7 s by deriving the interpolation step count from the camera count.
    """
    _emit(cb, cfg, "rendering", 0.05, "Rendering turntable preview")

    out = cfg.turntable_path
    # Idempotent: reuse a previously rendered clip.
    if out.exists() and out.stat().st_size > 0:
        _emit(cb, cfg, "rendering", 1.0, "Turntable ready (cached)")
        return

    run = _find_trained_run(cfg)
    if run is None:
        _log(cfg, "Turntable skipped: no trained model/config.yml found")
        _emit(cb, cfg, "rendering", 1.0, "Turntable skipped (no model)")
        return
    config_yml = run / "config.yml"

    # Interpolate through the real training cameras (order by proximity so an
    # orbit capture reads as a smooth turntable). Bound total frames ~ 150.
    n_cams = _count_dataset_cameras(cfg)
    steps = 5 if n_cams <= 0 else max(2, min(12, round(150 / n_cams)))
    out.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        str(VENV_BIN / "ns-render.exe"), "interpolate",
        "--load-config", str(config_yml),
        "--output-path", str(out),
        "--pose-source", "train",
        "--order-poses", "True",
        "--interpolation-steps", str(steps),
        "--frame-rate", "30",
        "--output-format", "video",
        "--downscale-factor", "1.5",
    ]
    _emit(cb, cfg, "rendering", 0.15,
          f"ns-render interpolate ({n_cams or '?'} cams x {steps})")
    try:
        rc = _run(cmd, cfg)
    except JobCancelled:
        raise
    except Exception as e:
        _log(cfg, f"Turntable render error: {e}")
        rc = -1

    if rc != 0 or not (out.exists() and out.stat().st_size > 0):
        _log(cfg, f"Turntable render failed (exit {rc}) - continuing without it")
        if out.exists() and out.stat().st_size == 0:
            out.unlink(missing_ok=True)
        _emit(cb, cfg, "rendering", 1.0, "Turntable skipped (render failed)")
        return

    size_mb = round(out.stat().st_size / 1e6, 1)
    _log(cfg, f"OK Turntable rendered: {out.name} ({size_mb} MB)")
    _emit(cb, cfg, "rendering", 1.0, f"Turntable ready: {out.name}")


def run_job(cfg: JobConfig, cb: ProgressCb):
    """Run the whole pipeline. Catches exceptions and re-raises after logging."""
    try:
        stage_prepare(cfg, cb)
        stage_extract_frames(cfg, cb)
        stage_preflight(cfg, cb)
        stage_colmap(cfg, cb)
        if cfg.remove_background:
            apply_background_masks(cfg, cb)
        stage_train(cfg, cb)
        stage_export(cfg, cb)
        if cfg.render_turntable:
            stage_render_turntable(cfg, cb)
        _emit(cb, cfg, "done", 1.0, "Done")
    except JobCancelled as e:
        _log(cfg, f"\n!!! CANCELLED: {e}")
        cb(JobUpdate(stage="cancelled", overall=0.0, message=str(e), error=str(e)))
    except Exception as e:
        msg = str(e)
        _log(cfg, f"\n!!! ERROR: {msg}")
        cb(JobUpdate(stage="failed", overall=0.0, message=msg, error=msg))
        raise
