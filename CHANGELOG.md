# CHANGELOG

All notable changes to **OneClick SPLAT**.

This project follows [Semantic Versioning](https://semver.org/) with a `-train`
suffix on releases that include training-pipeline changes.

---

## v2.27.39-train

### Added
- **RTX 50 (Blackwell) support** — install-deps reads the GPU's compute capability and installs **torch 2.7.1 + CUDA 12.8** with a new prebuilt `gsplat_cuda` (sm_75/86/89/120 + PTX) on RTX 50 cards; older GPUs keep torch 2.1.2 + CUDA 11.8. An existing venv on the wrong stack is switched in place. `OCS_TORCH_STACK=cu128|cu118` forces a stack.
- `installer/tools/build-gsplat-pyd.ps1` — reproducible build of the prebuilt gsplat extension.

### Fixed
- **Fresh installs failed on PCs without Visual Studio** ("Failed building wheel for fpsample", `CMAKE_CXX_COMPILER not set`): fpsample ≥ 1.0 has no Windows/Python 3.10 wheel. Pinned `fpsample==0.3.3` and `--prefer-binary`; verified with `PIP_ONLY_BINARY=:all:`.
- `numpy<2` pinned (torch 2.7 otherwise pulls numpy 2, which nerfstudio 1.1.4 does not target).
- torch ≥ 2.6 refused nerfstudio checkpoints (`weights_only`): `TORCH_FORCE_NO_WEIGHTS_ONLY_LOAD=1` set by the launcher.
- **Launcher gave up after 20 s** on the first start after a reboot (cold torch import); now waits up to 120 s.
- **Uninstall no longer deletes your trained jobs** — silent uninstall keeps them, interactive uninstall asks (default No). Backend is really stopped first, and no folders are left behind in Program Files.

---
## v2.27.38-train

### Changed
- **Python 3.10 bundled in the installer** — a private CPython 3.10.11 (python.org NuGet build) is installed to `{app}\python` and the venv is built on it. No system Python is ever installed or used. Upgrades from ≤ 2.27.37 repoint the existing venv onto the bundled runtime without re-downloading the ~6 GB of ML packages.

### Fixed
- **Background removal could hang forever** for non-admin users: pymatting's numba cache tried to write into Program Files (Windows `tempfile` retries the permission error endlessly). `NUMBA_CACHE_DIR` now points to `%LOCALAPPDATA%\OneClickSPLAT\numba_cache`.
- **A locked `install.log` aborted the dependency install** (antivirus / editor holding the file) while setup still reported success. Log writes now retry and never abort the install.

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
