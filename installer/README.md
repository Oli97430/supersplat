# OneClick SPLAT — Windows Installer

This folder contains everything needed to build a Windows `.exe` installer
that deploys OneClick SPLAT (editor + training backend) on any PC with an
NVIDIA GPU.

## TL;DR

```bat
:: Install Inno Setup 6 first (free): https://jrsoftware.org/isdl.php
:: Then from this folder:
build.bat
:: -> dist\OneClickSPLAT-Setup-2.27.3.exe
```

Hand that `.exe` to anyone with a Windows 10/11 + NVIDIA GPU machine.
Double-click → next-next-finish. The installer downloads ~6 GB of ML deps
on first run, then OneClick SPLAT launches from the Start Menu.

## What gets installed

| Component | Source | Size |
|---|---|---|
| Python 3.10 | python.org silent installer | ~30 MB |
| PyTorch 2.1.2 + CUDA 11.8 | PyTorch index | ~2.7 GB |
| nerfstudio 1.1.4 | PyPI | ~2 GB |
| FastAPI stack | PyPI | ~50 MB |
| COLMAP 3.9.1 (CUDA) | GitHub releases | ~750 MB |
| ffmpeg release-essentials | gyan.dev | ~100 MB |
| Backend Python source | bundled in installer | <100 KB |
| Frontend dist bundle | bundled in installer | ~30 MB |

Total disk footprint after install: **~6 GB**.

## See also

- [BUILD.md](BUILD.md) — detailed build instructions, customisation, code-signing
- [setup.iss](setup.iss) — Inno Setup script
- [scripts/](scripts/) — PowerShell installation logic
- [assets/README.txt](assets/README.txt) — end-user readme shown post-install
