# CHANGELOG

All notable changes to **OneClick SPLAT**.

This project follows [Semantic Versioning](https://semver.org/) with a `-train`
suffix on releases that include training-pipeline changes.

---

## [Unreleased] — v2.27.3-train

### Added
- **Capture presets** — six profiles (`object`, `indoor`, `outdoor`, `portrait`, `preview`, `custom`) that auto-tune matcher, iterations, FPS, and blur threshold in one click.
- **Cost estimator strip** — predicts frame count, upload size, disk usage, and training duration before dispatch.
- **Live GPU memory monitor** — polls `/gpu/live` every 3 s during the session and displays `free/total · % used` in the header.
- **Thumbnails in recent jobs** — backend extracts a 320 px JPG from the first frame; frontend shows it next to each entry.
- **Post-training metrics** — gaussian count, PLY file size, and training duration appear in the console once a job completes.
- **Frame quality filter** — Laplacian-variance pre-filter drops blurry frames before COLMAP (PIL + numpy optional dep; safe no-op if missing).
- **Disk space gate** — backend refuses new jobs when the jobs partition has less than `OCS_MIN_DISK_GB` (default 5 GB) free.
- **Rate limiting** — per-IP cap on `/jobs` creations per hour (`OCS_RATE_LIMIT`, default 20).
- **Optional bearer auth** — all endpoints can require `Authorization: Bearer <token>` via `OCS_AUTH_TOKEN`.
- **Configurable CORS origins** via `OCS_CORS_ORIGINS`.
- **Per-file upload size cap** via `OCS_MAX_UPLOAD_MB` (default 4 GB).
- **`/gpu/live`** — real-time VRAM read endpoint.
- **`/presets`** — preset catalogue endpoint.
- **`/disk`** — free-space probe endpoint.
- **`/jobs/{id}/thumbnail`** — JPG thumbnail download.
- **`/jobs/{id}/metrics`** — gaussian count + file size + duration JSON.
- **Dockerfile + docker-compose** for the backend (CUDA 11.8 base, COLMAP, nerfstudio, FastAPI).
- **`.env.example`** documenting all environment variables.

### Changed
- `JobState` now persists `preset`, `blur_threshold`, `finished_at`, and `metrics`.
- All endpoints except `/`, `/jobs/{id}/thumbnail`, and `/jobs/{id}/stream` now respect optional auth.

---

## [v2.27.2-train] — 2026-05-26

### Polish pass
- Focus-visible amber 2 px rings on every interactive element.
- State-driven progress bar: cyan on done, rust on failed.
- Scanlines overlay on the console frame via `::after`.
- Slider gradient driven by JS `--p` custom property.
- Sticky footer with full-width bleed via negative side margins.
- Retry button shows loading state while probing backend.
- `dragleave` null check stops the ghost `is-over` class.
- Running state dims input sections.
- Idle progress cursor hidden.
- `user-select: none` on labels.
- Empty state version label bumped to `v2.27`.

---

## [v2.27.1-train] — 2026-05-26

### 20 pipeline & UX improvements
- GPU label probed at startup (`get_gpu_info()` via torch.cuda).
- Raw COLMAP / nerfstudio log viewer (toggle in console).
- Detach mode — training continues if dialog is closed; show() reconnects.
- Desktop notifications on training complete / failed.
- Auto-FPS suggestion based on video duration.
- Vocab-tree matcher option for unordered photo sets.
- Job cancellation (frontend abort button + backend subprocess kill).
- COLMAP pose validation (≥ 20 poses required before training).
- Step-level training progress parsed from stdout.
- Per-COLMAP-phase progress (feat 0–30 %, match 30–60 %, map 60–95 %).
- Offline overlay with custom backend URL input.
- `localStorage` persistence of backend URL.
- Recent jobs section with load / download / delete.
- Capture quality hints (resolution, duration, frame count).
- SSE auto-reconnect on transient failures.
- Job rehydration from disk on backend startup.
- `extract_fps` configurable per job.
- 6-stage pipeline visualisation with active/done states.
- Throttled progress emit (max 1/s) to avoid SSE flood.

---

## [v2.27.0-train] — 2026-05-25

### Initial training pipeline integration
- FastAPI backend with single-GPU job queue.
- SSE progress streaming.
- ffmpeg → COLMAP → nerfstudio splatfacto → PLY export pipeline.
- Bespoke train-popup UI with brutalist scientific-instrument aesthetic.
- Empty-state welcome screen with cinematic editorial design.

---

## [v2.27.0] — 2026-05-25 (upstream)

Fork point from [`playcanvas/supersplat`](https://github.com/playcanvas/supersplat) v2.27.0.
See upstream [`RELEASES`](https://github.com/playcanvas/supersplat/releases) for prior history.
