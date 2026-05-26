<div align="center">

<br>

```
  ██████  ███    ██ ███████  ██████ ██      ██  ████████   ██     ███████ ██████  ██       █████  ████████
 ██    ██ ████   ██ ██      ██      ██      ██ ██      ██  ██     ██      ██   ██ ██      ██   ██    ██
 ██    ██ ██ ██  ██ █████   ██      ██      ██ ██      █████      ███████ ██████  ██      ███████    ██
 ██    ██ ██  ██ ██ ██      ██      ██      ██ ██      ██  ██          ██ ██      ██      ██   ██    ██
  ██████  ██   ████ ███████  ██████ ███████ ██  ██████ ██   ██    ███████ ██      ███████ ██   ██    ██
```

### *Reconstruct the world — one gaussian at a time.*

[![Release](https://img.shields.io/github/v/release/Oli97430/supersplat?label=release&color=5df5e0&labelColor=060810)](https://github.com/Oli97430/supersplat/releases)
[![License](https://img.shields.io/github/license/playcanvas/supersplat?color=a08cff&labelColor=060810)](LICENSE)
[![Built on PlayCanvas](https://img.shields.io/badge/built%20on-PlayCanvas-ff8052?labelColor=060810)](https://playcanvas.com)
[![Python](https://img.shields.io/badge/backend-FastAPI%20%2B%20nerfstudio-5df5e0?labelColor=060810)](server/)

<br>

[**Launch Editor**](#-quick-start) · [**Training Pipeline**](#-training-pipeline) · [**Supported Formats**](#-supported-formats) · [**Local Dev**](#-local-development)

<br>

</div>

---

**OneClick SPLAT** is a free, open-source browser-based studio for capturing, editing, and publishing **3D Gaussian Splat** scenes. Drop a short video or folder of photos — a local GPU pipeline handles camera pose recovery (COLMAP) and gaussian training (nerfstudio), then loads the result directly into the editor. Crop, clean, transform, and publish without leaving the browser.

> *Fork of [playcanvas/supersplat](https://github.com/playcanvas/supersplat) — adds a full local training pipeline on top of the original editor.*

<br>

## ✦ What it does

| | Feature | Detail |
|---|---|---|
| **◈ Train** | Photos or video → 3D splat | Drop a video (MP4/MOV/MKV) or photo folder. The pipeline extracts frames, runs COLMAP for camera pose recovery, and trains a splatfacto model with nerfstudio. ~30 min on RTX 3090. Fully offline. |
| **◈ Edit** | Sculpt and refine | Select and delete unwanted Gaussians with box, lasso, brush, flood, or sphere tools. Adjust transforms, combine scenes, crop, and inspect per-Gaussian data. |
| **◈ Publish** | Export anywhere | Save as `.ply`, `.spz`, or `.ssproj`. Generate LODs for web embedding. Publish to the cloud or hand off to any engine that speaks Gaussian Splat. |

<br>

## ◈ Training Pipeline

```
  Photos / Video
       │
       ▼
  ┌─────────────┐    ffmpeg extracts frames at configurable FPS
  │   Extract   │    (auto-detected: 1–5 fps based on video length)
  └──────┬──────┘
         │
         ▼
  ┌─────────────┐    COLMAP feature extraction → matching → SfM
  │    COLMAP   │    Sequential or vocab_tree matcher
  └──────┬──────┘    Validates ≥ 20 registered camera poses
         │
         ▼
  ┌─────────────┐    nerfstudio splatfacto — up to 30 000 iterations
  │    Train    │    Progress streams in real time via SSE
  └──────┬──────┘    Step-level progress parsed from stdout
         │
         ▼
  ┌─────────────┐    ns-export gaussian-splat → splat.ply
  │   Export    │    Auto-loaded into editor on completion
  └─────────────┘
```

### Requirements

| Component | Minimum | Recommended |
|---|---|---|
| GPU | NVIDIA RTX (8 GB VRAM) | RTX 3090 / 4090 |
| Python | 3.10 | 3.10 |
| CUDA | 11.8 | 12.x |
| RAM | 16 GB | 32 GB |
| Storage | 10 GB free | 50 GB free |
| Tools | ffmpeg, COLMAP | — |

### Backend setup

```sh
# 1. Create a Python 3.10 venv
python3.10 -m venv venv

# 2. Install nerfstudio + dependencies
venv/Scripts/pip install nerfstudio torch torchvision --index-url https://download.pytorch.org/whl/cu118

# 3. Start the FastAPI training server
venv/Scripts/python -m uvicorn main:app --host 127.0.0.1 --port 8000 --app-dir server

# 4. The editor auto-connects at http://127.0.0.1:8000
```

### API endpoints

```
POST   /jobs              Upload video or photos, queue training
GET    /jobs              List all jobs
GET    /jobs/{id}         Current status JSON
GET    /jobs/{id}/stream  SSE stream of live progress
GET    /jobs/{id}/log     Raw subprocess log (ffmpeg / COLMAP / nerfstudio)
GET    /jobs/{id}/ply     Download finished PLY
POST   /jobs/{id}/cancel  Cancel running job
DELETE /jobs/{id}         Remove job and all files
GET    /gpu               Detected GPU info (name, VRAM, free VRAM)
```

<br>

## ◈ Supported Formats

| Format | Read | Write | Notes |
|---|:---:|:---:|---|
| `.ply` | ✓ | ✓ | Primary format — standard 3DGS PLY |
| `.splat` | ✓ | ✓ | Compact binary splat |
| `.spz` | ✓ | ✓ | Compressed, web-optimised |
| `.ksplat` | ✓ | — | Kevin Kwok format |
| `.sog` | ✓ | — | Scene Object Graph |
| `.ssproj` | ✓ | ✓ | OneClick SPLAT project (with animation data) |

<br>

## ◈ Quick Start

### Run the editor (no training)

```sh
# Clone
git clone https://github.com/Oli97430/supersplat.git
cd supersplat

# Install
npm install

# Develop (auto-rebuild on save)
npm run develop
```

Open **`http://localhost:3000`** — drag any `.ply` or `.splat` file onto the canvas.

### Run with training backend

```sh
# Terminal 1 — editor
npm run develop

# Terminal 2 — training server
cd server
python -m uvicorn main:app --reload
```

Click **Train** in the editor, drop a video or photos, and hit **Begin Training**.

<br>

## ◈ Local Development

```sh
# Production build
npm run build

# Lint
npm run lint

# Serve the built dist/
npm run serve
```

> Node.js ≥ 20 required. Tested on Windows (primary), Linux, macOS.

### Project structure

```
repo/
├── src/
│   ├── ui/               # PCUI-based component system
│   │   ├── train-popup.ts       # Training console UI
│   │   ├── empty-state.ts       # Welcome / onboarding state
│   │   └── scss/                # Per-component SCSS
│   ├── shaders/          # WebGL GLSL shaders (inline TS)
│   ├── tools/            # Selection & transform tools
│   └── data-processor/   # Worker-thread GPU data processing
├── server/
│   ├── main.py           # FastAPI app, job registry, SSE
│   └── pipeline.py       # ffmpeg → COLMAP → nerfstudio pipeline
├── static/
│   ├── locales/          # i18n JSON files (9 languages)
│   └── icons/
└── landing.html          # Product landing page
```

<br>

## ◈ Localisation

Nine languages are supported out of the box: **English, French, German, Spanish, Japanese, Korean, Portuguese (BR), Russian, Simplified Chinese**.

Add a new language:

1. Create `static/locales/<locale>.json` (copy `en.json` as a base).
2. Add the locale to `src/ui/localization.ts`.
3. Test at `http://localhost:3000/?lng=<locale>`.

<br>

## ◈ Keyboard Shortcuts

| Action | Shortcut |
|---|---|
| Open file | `Ctrl / Cmd + O` |
| Save project | `Ctrl / Cmd + S` |
| Undo / Redo | `Ctrl + Z` / `Ctrl + Y` |
| Delete selected | `Del` |
| Focus camera on selection | `F` |
| Box select | `B` |
| Lasso select | `L` |
| Brush select | `P` |
| Toggle splat / centers view | `Tab` |
| Dismiss training console | `Esc` |

<br>

## ◈ Credits

OneClick SPLAT is built on **[SuperSplat](https://github.com/playcanvas/supersplat)** by PlayCanvas — an extraordinary open-source foundation.

Training pipeline powered by:
- **[nerfstudio](https://github.com/nerfstudio-project/nerfstudio)** — splatfacto trainer & exporter
- **[COLMAP](https://colmap.github.io/)** — structure-from-motion
- **[ffmpeg](https://ffmpeg.org/)** — video frame extraction

<br>

---

<div align="center">

*Built on [PlayCanvas](https://playcanvas.com) · v2.27.0 · MIT License*

</div>
