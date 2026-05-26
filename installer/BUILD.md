# Building the OneClick SPLAT installer

## Prerequisites

| Tool | Version | Where |
|---|---|---|
| **Node.js** | 20+ | https://nodejs.org |
| **Inno Setup 6** | Latest | https://jrsoftware.org/isdl.php |
| **Windows** | 10 1809+ | — |

## One-command build

From this folder:

```bat
build.bat
```

That script:

1. Builds the frontend with `npm run build` (produces `dist/`)
2. Locates Inno Setup compiler (ISCC.exe)
3. Compiles `setup.iss` → `dist/OneClickSPLAT-Setup-2.27.3.exe`

The resulting `.exe` is ~30-50 MB. **It does not bundle ML deps** —
those download on first run (PyTorch + nerfstudio + COLMAP + ffmpeg ≈ 6 GB).

## What the installer does

| Step | Action |
|---|---|
| 1 | Detects NVIDIA GPU (warns if absent) |
| 2 | Installs Python 3.10 silently if missing |
| 3 | Creates a `venv` under `Program Files\OneClick SPLAT` |
| 4 | Downloads PyTorch 2.1.2 + CUDA 11.8 |
| 5 | Downloads nerfstudio 1.1.4 |
| 6 | Downloads FastAPI + sse-starlette + multipart |
| 7 | Downloads COLMAP 3.9.1 Windows CUDA build |
| 8 | Downloads ffmpeg release-essentials |
| 9 | Copies `server/` Python files |
| 10 | Copies prebuilt `frontend/` from `dist/` |
| 11 | Creates Start Menu + optional Desktop shortcut |
| 12 | Optional: auto-start at login |

## Layout after install

```
C:\Program Files\OneClick SPLAT\
├── OneClickSPLAT.cmd          ← double-click launcher
├── server\
│   ├── main.py
│   ├── pipeline.py
│   └── .env.example
├── frontend\
│   ├── index.html
│   ├── index-*.js
│   └── …                       (built artifacts)
├── venv\                       (Python 3.10 + all deps)
├── tools\
│   ├── colmap\bin\colmap.exe
│   └── ffmpeg\bin\ffmpeg.exe
├── scripts\
│   ├── launch.ps1
│   ├── install-deps.ps1
│   ├── update.ps1
│   └── check-gpu.ps1
├── logs\
└── jobs\                       (training output, gitignored)
```

## Customising the installer

Edit `setup.iss` for branding, paths, behaviour:

- `[Setup]` section — name, version, install dir, compression
- `[Files]` — what to bundle
- `[Tasks]` — optional install steps (desktop icon, startup, deps download)
- `[Run]` — post-install commands
- `[Code]` — Pascal hooks for custom logic

## Code-signing (optional, recommended for distribution)

Without a code-signing certificate, Windows SmartScreen will warn users
the first time they run the installer. To sign:

```bat
signtool sign /tr http://timestamp.digicert.com /td sha256 /fd sha256 ^
    /a dist\OneClickSPLAT-Setup-2.27.3.exe
```

Requires `signtool.exe` from the Windows SDK and a valid Authenticode
certificate from a CA (DigiCert, Sectigo, etc.).

## Silent install (for IT deployment)

```bat
OneClickSPLAT-Setup-2.27.3.exe /VERYSILENT /SUPPRESSMSGBOXES /NORESTART /TASKS="downloadml"
```

| Flag | Effect |
|---|---|
| `/VERYSILENT` | No UI |
| `/SUPPRESSMSGBOXES` | Suppress all dialogs |
| `/DIR="C:\OCS"` | Custom install path |
| `/TASKS="desktopicon,downloadml"` | Pre-select tasks |
| `/LOG="C:\ocs-install.log"` | Verbose log to file |
