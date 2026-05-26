==============================================================================
                            ONECLICK SPLAT
                        Welcome — Quick Start
==============================================================================

Thank you for installing OneClick SPLAT.

This tool turns photos and video into 3D Gaussian Splats — entirely on your
local GPU, no cloud required.


LAUNCHING
---------
   Double-click OneClickSPLAT.cmd in this folder, or use the Start Menu
   shortcut. The launcher will:

      1. Start the FastAPI backend (port 8000)
      2. Serve the editor frontend (port 3000)
      3. Open http://127.0.0.1:3000/ in your default browser

   Close the console window to stop both services.


FIRST RUN
---------
   The installer downloads PyTorch, nerfstudio, COLMAP and ffmpeg.
   This requires ~6 GB of disk space and an internet connection.
   It only happens once.


REQUIREMENTS
------------
   - Windows 10 1809+ (x64)
   - NVIDIA GPU with at least 8 GB VRAM (RTX 3060 or better recommended)
   - 50 GB free disk space for jobs
   - Up-to-date NVIDIA drivers (R515 or newer)


CONFIGURATION
-------------
   Edit  server\.env.example  to customise:
      - CORS allowed origins
      - Upload size cap
      - Authentication token
      - Rate limit


SUPPORT
-------
   https://github.com/Oli97430/supersplat/issues


UNINSTALL
---------
   Use "Add or Remove Programs" in Windows Settings, then select
   "OneClick SPLAT".

==============================================================================
