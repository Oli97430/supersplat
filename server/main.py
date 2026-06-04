"""FastAPI backend for OneClick SPLAT training pipeline.

Endpoints:
  GET    /                    service info
  GET    /gpu                 GPU label (cached at startup)
  GET    /gpu/live            live VRAM read
  GET    /presets             capture profile catalogue
  POST   /jobs                multipart upload (video or photos)
  GET    /jobs                list of jobs
  GET    /jobs/{id}           current status JSON
  GET    /jobs/{id}/stream    SSE stream of status updates
  GET    /jobs/{id}/log       plain-text log
  GET    /jobs/{id}/ply       download finished PLY
  GET    /jobs/{id}/thumbnail 320 px JPG of first frame
  GET    /jobs/{id}/metrics   gaussian count + file size + duration
  POST   /jobs/{id}/cancel    cancel running job
  DELETE /jobs/{id}           remove job

Single-job queue: one training runs at a time, others are queued.

Config via env vars (see .env.example):
  OCS_CORS_ORIGINS    comma-separated origins (default: localhost:3000, 127.0.0.1:3000)
  OCS_MAX_UPLOAD_MB   per-file upload cap (default: 4096)
  OCS_AUTH_TOKEN      optional bearer token; if set, all endpoints require Authorization: Bearer <token>
  OCS_RATE_LIMIT      max job creations per IP per hour (default: 20, 0 to disable)
"""
from __future__ import annotations

import asyncio
import json
import os
import shutil
import threading
import time
import uuid
from collections import defaultdict, deque
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Optional

from fastapi import (
    FastAPI, File, HTTPException, UploadFile, Form, Request, Depends, Header
)
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, PlainTextResponse, JSONResponse
from sse_starlette.sse import EventSourceResponse

from pipeline import (
    JobConfig, JobUpdate, kill_active_subprocess, run_job, get_gpu_info,
    CAPTURE_PRESETS, check_disk_space, extract_thumbnail, ply_metrics,
)

# ── Env config ─────────────────────────────────────────────────────────────
_DEFAULT_ORIGINS = "http://localhost:3000,http://127.0.0.1:3000"
CORS_ORIGINS = [o.strip() for o in os.environ.get("OCS_CORS_ORIGINS", _DEFAULT_ORIGINS).split(",") if o.strip()]
MAX_UPLOAD_MB = int(os.environ.get("OCS_MAX_UPLOAD_MB", "4096"))
AUTH_TOKEN = os.environ.get("OCS_AUTH_TOKEN", "").strip()
RATE_LIMIT = int(os.environ.get("OCS_RATE_LIMIT", "20"))
MIN_DISK_GB = float(os.environ.get("OCS_MIN_DISK_GB", "5.0"))

# App version. Read from server/VERSION if the installer dropped one (so
# /jobs metadata reflects the installed package version), otherwise fall
# back to a baked-in default. Bumped here in tandem with package.json.
_FALLBACK_VERSION = "2.27-train"
def _read_app_version() -> str:
    vf = Path(__file__).parent / "VERSION"
    try:
        if vf.is_file():
            txt = vf.read_text(encoding="utf-8").strip()
            if txt:
                return txt
    except Exception:
        pass
    return _FALLBACK_VERSION
APP_VERSION = _read_app_version()

# Jobs directory — defaults to a per-user location when installed system-wide,
# falls back to ./jobs next to main.py for local development.
def _resolve_jobs_root() -> Path:
    env = os.environ.get("OCS_JOBS_DIR", "").strip()
    if env:
        return Path(env).expanduser().resolve()
    if os.name == "nt":
        appdata = os.environ.get("LOCALAPPDATA")
        if appdata:
            return Path(appdata) / "OneClickSPLAT" / "jobs"
    return Path.home() / ".oneclicksplat" / "jobs"

# Env var always wins (the launcher sets it).
if os.environ.get("OCS_JOBS_DIR"):
    JOBS_ROOT = _resolve_jobs_root()
else:
    # Try ./jobs next to main.py first (local dev). If it's not writable
    # (e.g. installed under Program Files), fall back to LOCALAPPDATA.
    _local_jobs = Path(__file__).parent / "jobs"
    try:
        _local_jobs.mkdir(exist_ok=True)
        JOBS_ROOT = _local_jobs
    except (PermissionError, OSError):
        JOBS_ROOT = _resolve_jobs_root()

JOBS_ROOT.mkdir(parents=True, exist_ok=True)


# ---- In-memory job registry ------------------------------------------------

@dataclass
class JobState:
    id: str
    name: str
    created_at: float
    stage: str = "queued"
    stage_progress: float = 0.0
    overall: float = 0.0
    message: str = ""
    error: Optional[str] = None
    upload_kind: str = "unknown"     # 'video' | 'photos'
    num_files: int = 0
    matcher: str = "sequential"
    max_iters: int = 30000
    extract_fps: int = 2
    preset: str = "custom"
    blur_threshold: float = 0.0
    dedupe_threshold: int = 0
    prune_opacity_logit: float = -2.5
    remove_background: bool = False
    refine_geometry: bool = False
    render_turntable: bool = False
    clip_start: float = 0.0
    clip_end: float = 0.0
    finished_at: Optional[float] = None
    metrics: Optional[dict] = None    # populated when done
    viewer_url: Optional[str] = None  # live nerfstudio viewer URL during training
    capture_report: Optional[dict] = None  # pre-flight frame-quality analysis

    def to_dict(self) -> dict:
        d = asdict(self)
        job_dir = JOBS_ROOT / self.id
        d["has_ply"] = (job_dir / "splat.ply").exists()
        d["has_turntable"] = (job_dir / "turntable.mp4").exists()
        d["has_thumbnail"] = (job_dir / "thumbnail.jpg").exists()
        d["duration_sec"] = round((self.finished_at - self.created_at), 1) if self.finished_at else None
        return d


class JobRegistry:
    def __init__(self):
        self._jobs: dict[str, JobState] = {}
        self._cancel_events: dict[str, threading.Event] = {}
        self._configs: dict[str, JobConfig] = {}
        self._lock = threading.Lock()
        self._listeners: dict[str, list[asyncio.Queue]] = {}
        self._queue: asyncio.Queue[str] = asyncio.Queue()

    def create(self, name: str, kind: str, num_files: int,
               matcher: str, max_iters: int, extract_fps: int,
               preset: str = "custom", blur_threshold: float = 0.0,
               dedupe_threshold: int = 0,
               prune_opacity_logit: float = -2.5,
               remove_background: bool = False,
               refine_geometry: bool = False,
               render_turntable: bool = False,
               clip_start: float = 0.0,
               clip_end: float = 0.0) -> JobState:
        jid = uuid.uuid4().hex[:12]
        st = JobState(
            id=jid, name=name, created_at=time.time(),
            upload_kind=kind, num_files=num_files,
            matcher=matcher, max_iters=max_iters, extract_fps=extract_fps,
            preset=preset, blur_threshold=blur_threshold,
            dedupe_threshold=dedupe_threshold,
            prune_opacity_logit=prune_opacity_logit,
            remove_background=remove_background,
            refine_geometry=refine_geometry,
            render_turntable=render_turntable,
            clip_start=clip_start,
            clip_end=clip_end,
        )
        with self._lock:
            self._jobs[jid] = st
            self._listeners[jid] = []
            self._cancel_events[jid] = threading.Event()
        self._persist(st)
        return st

    def rehydrate(self):
        """Load completed jobs from disk into the registry. Skips jobs whose
        previous run did not reach a terminal state (we'd need to re-run them
        to be useful, and we don't want to do that automatically)."""
        if not JOBS_ROOT.exists():
            return
        loaded = 0
        for d in sorted(JOBS_ROOT.iterdir(), key=lambda p: p.name):
            if not d.is_dir():
                continue
            status_file = d / "status.json"
            if not status_file.exists():
                continue
            try:
                data = json.loads(status_file.read_text())
                # Skip jobs that didn't finish (would need re-running).
                if data.get("stage") not in ("done", "failed", "cancelled"):
                    continue
                # Drop derived fields that aren't on JobState
                data.pop("has_ply", None)
                st = JobState(**{k: v for k, v in data.items() if k in JobState.__dataclass_fields__})
                with self._lock:
                    self._jobs[st.id] = st
                    self._listeners.setdefault(st.id, [])
                loaded += 1
            except Exception as e:
                print(f"failed to rehydrate {d.name}: {e}")
        if loaded:
            print(f"Rehydrated {loaded} job(s) from disk")

    def get(self, jid: str) -> JobState:
        with self._lock:
            if jid not in self._jobs:
                raise HTTPException(404, f"job {jid} not found")
            return self._jobs[jid]

    def all(self) -> list[JobState]:
        with self._lock:
            return sorted(self._jobs.values(), key=lambda s: s.created_at, reverse=True)

    def update(self, jid: str, upd: JobUpdate):
        with self._lock:
            st = self._jobs.get(jid)
            if not st:
                return
            st.stage = upd.stage
            st.stage_progress = upd.stage_progress
            st.overall = upd.overall
            st.message = upd.message
            if upd.error:
                st.error = upd.error
            if upd.viewer_url:
                st.viewer_url = upd.viewer_url
            if upd.capture_report is not None:
                st.capture_report = upd.capture_report
            if upd.stage in ("done", "failed", "cancelled") and st.finished_at is None:
                st.finished_at = time.time()
                # Clear the live viewer URL — nerfstudio shut it down.
                st.viewer_url = None
                if upd.stage == "done":
                    st.metrics = ply_metrics(JOBS_ROOT / jid / "splat.ply")
            listeners = list(self._listeners.get(jid, []))
        self._persist(st)
        payload = json.dumps(st.to_dict())
        for q in listeners:
            try:
                q.put_nowait(payload)
            except asyncio.QueueFull:
                pass

    def add_listener(self, jid: str) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=64)
        with self._lock:
            self._listeners.setdefault(jid, []).append(q)
            initial = self._jobs.get(jid)
        if initial:
            q.put_nowait(json.dumps(initial.to_dict()))
        return q

    def remove_listener(self, jid: str, q: asyncio.Queue):
        with self._lock:
            if jid in self._listeners and q in self._listeners[jid]:
                self._listeners[jid].remove(q)

    def delete(self, jid: str):
        with self._lock:
            st = self._jobs.pop(jid, None)
            self._listeners.pop(jid, None)
            self._cancel_events.pop(jid, None)
            self._configs.pop(jid, None)
        if st:
            shutil.rmtree(JOBS_ROOT / jid, ignore_errors=True)

    def cancel(self, jid: str) -> bool:
        """Request cancellation. The worker thread sees the cancel_event flag
        and the live subprocess (if any) is terminated. Returns True if a job
        was actually live."""
        with self._lock:
            ev = self._cancel_events.get(jid)
            cfg = self._configs.get(jid)
        if not ev:
            return False
        ev.set()
        if cfg is not None:
            kill_active_subprocess(cfg)
        return True

    def get_cancel_event(self, jid: str) -> threading.Event:
        with self._lock:
            ev = self._cancel_events.get(jid)
            if ev is None:
                ev = threading.Event()
                self._cancel_events[jid] = ev
            return ev

    def requeue(self, jid: str) -> bool:
        """Reset a terminal job back to 'queued' and clear its cancel flag so
        the worker can re-run it. The pipeline reuses any extracted frames,
        COLMAP reconstruction and training checkpoint already on disk, so a
        retry resumes instead of starting over. Returns False if the job is
        still active (nothing to retry)."""
        with self._lock:
            st = self._jobs.get(jid)
            if not st:
                return False
            if st.stage not in ("failed", "cancelled", "done"):
                return False  # still running/queued
            st.stage = "queued"
            st.stage_progress = 0.0
            st.overall = 0.0
            st.message = "re-queued for retry"
            st.error = None
            st.finished_at = None
            st.viewer_url = None
            # Fresh cancel event (the old one may be set from a prior cancel).
            self._cancel_events[jid] = threading.Event()
        self._persist(st)
        return True

    def register_config(self, jid: str, cfg: JobConfig):
        with self._lock:
            self._configs[jid] = cfg

    def unregister_config(self, jid: str):
        with self._lock:
            self._configs.pop(jid, None)

    def _persist(self, st: JobState):
        d = JOBS_ROOT / st.id
        d.mkdir(parents=True, exist_ok=True)
        (d / "status.json").write_text(json.dumps(st.to_dict(), indent=2))


registry = JobRegistry()

# GPU info cached at startup (importing torch is slow, do it once)
_gpu_info: dict = {"available": False, "label": "detecting…"}


# ---- Background worker (single GPU job at a time) --------------------------

async def worker():
    loop = asyncio.get_event_loop()
    while True:
        jid = await registry._queue.get()
        try:
            st = registry.get(jid)
        except HTTPException:
            # Job was deleted before it ran
            continue

        cfg = JobConfig(
            job_id=jid,
            job_dir=JOBS_ROOT / jid,
            matcher=st.matcher,
            max_iters=st.max_iters,
            extract_fps=st.extract_fps,
            blur_threshold=st.blur_threshold,
            dedupe_threshold=st.dedupe_threshold,
            prune_opacity_logit=st.prune_opacity_logit,
            remove_background=st.remove_background,
            refine_geometry=st.refine_geometry,
            render_turntable=st.render_turntable,
            clip_start=st.clip_start,
            clip_end=st.clip_end,
            cancel_event=registry.get_cancel_event(jid),
        )
        registry.register_config(jid, cfg)

        def cb(upd: JobUpdate):
            registry.update(jid, upd)

        try:
            await loop.run_in_executor(None, run_job, cfg, cb)
        except Exception as e:
            registry.update(jid, JobUpdate(stage="failed", overall=0.0, message=str(e), error=str(e)))
        finally:
            registry.unregister_config(jid)


# ---- FastAPI ---------------------------------------------------------------

app = FastAPI(title="OneClick SPLAT Training Backend")

app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ── Auth (optional bearer token) ──────────────────────────────────────────
def require_auth(authorization: Optional[str] = Header(None)) -> None:
    if not AUTH_TOKEN:
        return  # disabled when env var is empty
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(401, "missing bearer token")
    if authorization[7:].strip() != AUTH_TOKEN:
        raise HTTPException(403, "invalid bearer token")


# ── Rate limit (in-memory, per client IP) ─────────────────────────────────
_rate_history: dict[str, deque] = defaultdict(deque)
_rate_lock = threading.Lock()


def _check_rate_limit(request: Request) -> None:
    if RATE_LIMIT <= 0:
        return
    client = (request.client.host if request.client else "unknown")
    now = time.time()
    cutoff = now - 3600
    with _rate_lock:
        history = _rate_history[client]
        while history and history[0] < cutoff:
            history.popleft()
        if len(history) >= RATE_LIMIT:
            raise HTTPException(
                429,
                f"rate limit: {RATE_LIMIT} jobs / hour per IP — wait "
                f"{int((history[0] + 3600 - now) / 60) + 1} min",
            )
        history.append(now)


@app.on_event("startup")
async def _startup():
    global _gpu_info
    registry.rehydrate()
    asyncio.create_task(worker())
    # Probe GPU in a thread so we don't block the event loop on torch import
    loop = asyncio.get_event_loop()
    _gpu_info = await loop.run_in_executor(None, get_gpu_info)
    print(f"GPU: {_gpu_info.get('label', 'unknown')}")


@app.get("/gpu", dependencies=[Depends(require_auth)])
async def gpu_info():
    return _gpu_info


@app.get("/gpu/live", dependencies=[Depends(require_auth)])
async def gpu_live():
    """Read VRAM in real time (cheap; safe to poll every 2-3 s)."""
    try:
        import torch  # type: ignore
        if not torch.cuda.is_available():
            return {"available": False, "free_gb": None, "used_gb": None, "total_gb": None}
        free_bytes, total_bytes = torch.cuda.mem_get_info(0)
        used_bytes = total_bytes - free_bytes
        return {
            "available": True,
            "free_gb": round(free_bytes / 1024 ** 3, 2),
            "used_gb": round(used_bytes / 1024 ** 3, 2),
            "total_gb": round(total_bytes / 1024 ** 3, 2),
            "pct_used": round(used_bytes / total_bytes * 100, 1),
        }
    except Exception as e:
        return {"available": False, "error": str(e)}


@app.get("/presets", dependencies=[Depends(require_auth)])
async def presets():
    return CAPTURE_PRESETS


def _dir_size_bytes(path: Path) -> int:
    total = 0
    try:
        for p in path.rglob("*"):
            try:
                if p.is_file():
                    total += p.stat().st_size
            except OSError:
                pass
    except OSError:
        pass
    return total


@app.get("/disk", dependencies=[Depends(require_auth)])
async def disk():
    ok, free_gb = check_disk_space(JOBS_ROOT, MIN_DISK_GB)
    total_gb = used_gb = jobs_gb = None
    jobs_count = 0
    try:
        du = shutil.disk_usage(JOBS_ROOT)
        total_gb = round(du.total / 1024 ** 3, 1)
        used_gb = round((du.total - du.free) / 1024 ** 3, 1)
    except Exception:
        pass
    try:
        if JOBS_ROOT.exists():
            jobs_count = sum(1 for d in JOBS_ROOT.iterdir() if d.is_dir())
            jobs_gb = round(_dir_size_bytes(JOBS_ROOT) / 1024 ** 3, 2)
    except Exception:
        pass
    return {"ok": ok, "free_gb": free_gb, "required_gb": MIN_DISK_GB,
            "total_gb": total_gb, "used_gb": used_gb,
            "jobs_gb": jobs_gb, "jobs_count": jobs_count}


@app.get("/")
async def root():
    return {
        "service": "oneclick-splat-training",
        "version": APP_VERSION,
        "gpu": _gpu_info.get("label"),
        "auth_required": bool(AUTH_TOKEN),
        "rate_limit_per_hour": RATE_LIMIT,
        "max_upload_mb": MAX_UPLOAD_MB,
        "endpoints": [
            "POST /jobs", "GET /jobs", "GET /jobs/{id}",
            "GET /jobs/{id}/stream", "GET /jobs/{id}/log",
            "GET /jobs/{id}/ply", "GET /jobs/{id}/thumbnail",
            "GET /jobs/{id}/metrics", "POST /jobs/{id}/cancel",
            "DELETE /jobs/{id}",
            "GET /gpu", "GET /gpu/live", "GET /presets", "GET /disk",
        ],
    }


@app.post("/jobs", dependencies=[Depends(require_auth)])
async def create_job(
    request: Request,
    files: list[UploadFile] = File(...),
    matcher: str = Form("sequential"),
    max_iters: int = Form(30000),
    extract_fps: int = Form(2),
    name: str = Form(""),
    preset: str = Form("custom"),
    remove_background: bool = Form(False),
    refine_geometry: bool = Form(False),
    render_turntable: bool = Form(False),
    clip_start: float = Form(0.0),
    clip_end: float = Form(0.0),
):
    _check_rate_limit(request)

    if not files:
        raise HTTPException(400, "no files uploaded")

    # Disk space gate
    ok, free_gb = check_disk_space(JOBS_ROOT, MIN_DISK_GB)
    if not ok:
        raise HTTPException(
            507, f"only {free_gb} GB free on jobs disk — need {MIN_DISK_GB} GB"
        )

    # Resolve preset → fill in defaults but allow override via form
    blur_threshold      = 0.0
    dedupe_threshold    = 0
    prune_opacity_logit = -2.5
    if preset in CAPTURE_PRESETS:
        p = CAPTURE_PRESETS[preset]
        # form values override preset only if explicitly different from defaults
        if matcher == "sequential" and p["matcher"] != "sequential":
            matcher = p["matcher"]
        if max_iters == 30000:
            max_iters = p["max_iters"]
        if extract_fps == 2:
            extract_fps = p["extract_fps"]
        blur_threshold      = p["blur_threshold"]
        dedupe_threshold    = p.get("dedupe_threshold", 0)
        prune_opacity_logit = p.get("prune_opacity_logit", -2.5)

    names = [f.filename or "" for f in files]
    kind = "video" if any(
        n.lower().endswith((".mp4", ".mov", ".mkv", ".avi", ".webm", ".m4v"))
        for n in names
    ) else "photos"

    # Per-file size check
    max_bytes = MAX_UPLOAD_MB * 1024 * 1024
    for f in files:
        if f.size is not None and f.size > max_bytes:
            raise HTTPException(
                413, f"{f.filename}: {f.size / 1024 / 1024:.1f} MB exceeds {MAX_UPLOAD_MB} MB cap"
            )

    st = registry.create(
        name=name or names[0] or f"job-{int(time.time())}",
        kind=kind,
        num_files=len(files),
        matcher=matcher,
        max_iters=max_iters,
        extract_fps=max(1, min(10, extract_fps)),
        preset=preset,
        blur_threshold=blur_threshold,
        dedupe_threshold=dedupe_threshold,
        prune_opacity_logit=prune_opacity_logit,
        remove_background=remove_background,
        refine_geometry=refine_geometry,
        render_turntable=render_turntable,
        clip_start=clip_start,
        clip_end=clip_end,
    )
    upload_dir = JOBS_ROOT / st.id / "upload"
    upload_dir.mkdir(parents=True, exist_ok=True)
    total_written = 0
    for f in files:
        target = upload_dir / Path(f.filename or "file").name
        with target.open("wb") as out:
            while chunk := await f.read(1 << 20):
                out.write(chunk)
                total_written += len(chunk)
                if total_written > max_bytes * len(files):
                    out.close()
                    target.unlink(missing_ok=True)
                    registry.delete(st.id)
                    raise HTTPException(413, "aggregate upload size exceeded")

    # Extract thumbnail asynchronously (don't block enqueue)
    loop = asyncio.get_event_loop()
    loop.run_in_executor(
        None, extract_thumbnail, upload_dir, JOBS_ROOT / st.id / "thumbnail.jpg"
    )

    await registry._queue.put(st.id)
    return st.to_dict()


@app.get("/jobs", dependencies=[Depends(require_auth)])
async def list_jobs():
    return [s.to_dict() for s in registry.all()]


@app.get("/jobs/{jid}", dependencies=[Depends(require_auth)])
async def get_job(jid: str):
    return registry.get(jid).to_dict()


@app.get("/jobs/{jid}/thumbnail")
async def job_thumbnail(jid: str):
    registry.get(jid)
    p = JOBS_ROOT / jid / "thumbnail.jpg"
    if not p.exists():
        raise HTTPException(404, "no thumbnail")
    return FileResponse(p, media_type="image/jpeg")


@app.get("/jobs/{jid}/metrics", dependencies=[Depends(require_auth)])
async def job_metrics(jid: str):
    st = registry.get(jid)
    m = st.metrics or ply_metrics(JOBS_ROOT / jid / "splat.ply")
    return {
        "metrics": m,
        "duration_sec": round((st.finished_at - st.created_at), 1) if st.finished_at else None,
        "stage": st.stage,
        "matcher": st.matcher,
        "max_iters": st.max_iters,
        "extract_fps": st.extract_fps,
        "preset": st.preset,
    }


@app.get("/jobs/{jid}/stream")
async def stream_job(jid: str):
    registry.get(jid)  # 404 if not found
    q = registry.add_listener(jid)

    async def gen():
        try:
            while True:
                try:
                    payload = await asyncio.wait_for(q.get(), timeout=30)
                    yield {"event": "status", "data": payload}
                    # End the stream once the job has reached a terminal state
                    obj = json.loads(payload)
                    if obj.get("stage") in ("done", "failed"):
                        break
                except asyncio.TimeoutError:
                    yield {"event": "ping", "data": "{}"}
        finally:
            registry.remove_listener(jid, q)

    return EventSourceResponse(gen())


@app.get("/jobs/{jid}/log", response_class=PlainTextResponse, dependencies=[Depends(require_auth)])
async def get_log(jid: str):
    registry.get(jid)
    p = JOBS_ROOT / jid / "log.txt"
    return p.read_text(encoding="utf-8") if p.exists() else ""


@app.get("/jobs/{jid}/ply", dependencies=[Depends(require_auth)])
@app.get("/jobs/{jid}/splat.ply", dependencies=[Depends(require_auth)])
async def download_ply(jid: str):
    st = registry.get(jid)
    p = JOBS_ROOT / jid / "splat.ply"
    if not p.exists():
        raise HTTPException(404, "PLY not yet ready")
    return FileResponse(p, media_type="application/octet-stream", filename=f"{st.name}.ply")


@app.get("/jobs/{jid}/turntable.mp4")
async def job_turntable(jid: str, dl: int = 0):
    # No auth dependency: the browser loads this directly via <video>/<a> and
    # cannot attach an Authorization header (same rationale as /thumbnail).
    st = registry.get(jid)
    p = JOBS_ROOT / jid / "turntable.mp4"
    if not p.exists():
        raise HTTPException(404, "no turntable for this job")
    if dl:
        return FileResponse(p, media_type="video/mp4",
                            filename=f"{st.name}-turntable.mp4")
    return FileResponse(p, media_type="video/mp4")


@app.post("/jobs/{jid}/cancel", dependencies=[Depends(require_auth)])
async def cancel_job(jid: str):
    registry.get(jid)
    cancelled = registry.cancel(jid)
    return {"cancelled": jid, "was_live": cancelled}


@app.post("/jobs/{jid}/retry", dependencies=[Depends(require_auth)])
async def retry_job(jid: str):
    """Resume a failed/cancelled job. Reuses frames, COLMAP and checkpoints
    already on disk so it picks up where it left off rather than re-uploading
    and recomputing everything."""
    registry.get(jid)  # 404 if unknown
    if not registry.requeue(jid):
        raise HTTPException(409, "job is still running — cannot retry")
    await registry._queue.put(jid)
    return registry.get(jid).to_dict()


@app.delete("/jobs/{jid}", dependencies=[Depends(require_auth)])
async def delete_job(jid: str):
    registry.delete(jid)
    return {"deleted": jid}
