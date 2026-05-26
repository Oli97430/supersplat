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

STAGES = ["queued", "preparing", "extracting", "colmap", "training", "exporting", "done"]

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
    full_env["PATH"] = f"{COLMAP_BIN};{VENV_BIN};{full_env.get('PATH', '')}"
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


def stage_extract_frames(cfg: JobConfig, cb: ProgressCb):
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
    _emit(cb, cfg, "extracting", 0.1, f"Extracting frames at {cfg.extract_fps} fps from {video.name}")
    cmd = [
        "ffmpeg", "-y", "-i", str(video),
        "-vf", f"fps={cfg.extract_fps},scale=1600:-2",
        "-q:v", "2",
        str(cfg.images_dir / "frame_%05d.jpg"),
    ]
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


def stage_colmap(cfg: JobConfig, cb: ProgressCb):
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

    def on_line(line: str):
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
        # Propagate the viewer URL on every subsequent emit so reconnecting
        # clients (detach -> reopen popup) immediately get the iframe.
        cb(JobUpdate(
            stage="training",
            stage_progress=p,
            overall=min(1.0, (STAGES.index("training") + p) / (len(STAGES) - 1)),
            message=f"Step {step:,} / {total:,}",
            viewer_url=viewer_url[0],
        ))

    cmd = [
        str(VENV_BIN / "ns-train.exe"), "splatfacto",
        "--data", str(cfg.workspace_dir),
        "--max-num-iterations", str(cfg.max_iters),
        "--output-dir", str(cfg.outputs_dir),
        "--vis", "viewer",
        "--viewer.quit-on-train-completion", "True",
    ]
    if resume_dir:
        # Nerfstudio 1.x expects --load-dir to point at the EXPERIMENT folder
        # (the one containing config.yml), not the nerfstudio_models/ subfolder.
        # nerfstudio internally discovers the highest-step .ckpt under it.
        cmd.extend(["--load-dir", str(resume_dir)])
        _log(cfg, f"↻ Resuming from step {resume_step:,} in {resume_dir.name}")
        _emit(cb, cfg, "training",
              resume_step / max(cfg.max_iters, 1),
              f"Resuming from step {resume_step:,}")

    rc = _run(cmd, cfg, on_line=on_line)
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

    _emit(cb, cfg, "exporting", 1.0, f"PLY ready: {cfg.ply_path.name}")


def run_job(cfg: JobConfig, cb: ProgressCb):
    """Run the whole pipeline. Catches exceptions and re-raises after logging."""
    try:
        stage_prepare(cfg, cb)
        stage_extract_frames(cfg, cb)
        stage_colmap(cfg, cb)
        stage_train(cfg, cb)
        stage_export(cfg, cb)
        _emit(cb, cfg, "done", 1.0, "Done")
    except JobCancelled as e:
        _log(cfg, f"\n!!! CANCELLED: {e}")
        cb(JobUpdate(stage="cancelled", overall=0.0, message=str(e), error=str(e)))
    except Exception as e:
        msg = str(e)
        _log(cfg, f"\n!!! ERROR: {msg}")
        cb(JobUpdate(stage="failed", overall=0.0, message=msg, error=msg))
        raise
