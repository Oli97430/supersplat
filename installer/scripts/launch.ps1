# ============================================================================
# OneClick SPLAT -- launcher
# Starts the FastAPI backend, serves the prebuilt frontend, opens the browser.
# Runtime data (logs, jobs, pids) goes to %LOCALAPPDATA%\OneClickSPLAT
# so it survives a read-only Program Files installation.
# ============================================================================

param([string]$AppDir = (Split-Path $PSScriptRoot -Parent))

$ErrorActionPreference = "Continue"
$ProgressPreference    = "SilentlyContinue"

# Normalise the install path -- the OneClickSPLAT.cmd wrapper passes "%~dp0."
# which produces a trailing "\." that's ugly in error messages.
try { $AppDir = [IO.Path]::GetFullPath($AppDir) } catch { }

# ── User-writable data location ──────────────────────────────────────────
$UserData = Join-Path $env:LOCALAPPDATA "OneClickSPLAT"
$LogDir   = Join-Path $UserData "logs"
$JobsDir  = Join-Path $UserData "jobs"
New-Item -ItemType Directory -Path $LogDir  -Force | Out-Null
New-Item -ItemType Directory -Path $JobsDir -Force | Out-Null

$BackendLog     = Join-Path $LogDir "backend.log"
$BackendLogErr  = Join-Path $LogDir "backend.err.log"
$FrontendLog    = Join-Path $LogDir "frontend.log"
$FrontendLogErr = Join-Path $LogDir "frontend.err.log"

# ── Install-time paths ───────────────────────────────────────────────────
$Venv         = Join-Path $AppDir "venv"
$VenvPy       = Join-Path $Venv  "Scripts\python.exe"
$Server       = Join-Path $AppDir "server"
$Frontend     = Join-Path $AppDir "frontend"
$ColmapBin    = Join-Path $AppDir "tools\colmap\bin"
# COLMAP DLLs (boost/ceres/cudart/Qt/etc) live in lib/, not bin/. Without
# lib/ on PATH, colmap.exe fails to load and nerfstudio's check_colmap_installed
# reports "Could not find COLMAP" even though the binary is right there.
$ColmapLib    = Join-Path $AppDir "tools\colmap\lib"
$FfmpegBin    = Join-Path $AppDir "tools\ffmpeg\bin"
$ServeScript  = Join-Path $AppDir "scripts\serve-frontend.ps1"

# ── Sanity check: was install-deps run AND did it finish? ───────────────
# Just checking python.exe isn't enough -- install-deps can stop midway,
# leaving a half-built venv. Probe the site-packages directly (filesystem
# check, no subprocess) so a missing-module stderr can't crash this script
# via PowerShell's NativeCommandError trap.
function Test-VenvHealthy {
    param([string]$VenvRoot)
    $sp = Join-Path $VenvRoot "Lib\site-packages"
    foreach ($mod in @("uvicorn", "fastapi", "torch", "nerfstudio")) {
        if (-not (Test-Path (Join-Path $sp "$mod\__init__.py"))) {
            return $false
        }
    }
    return $true
}

$venvOk = (Test-Path $VenvPy) -and (Test-VenvHealthy -VenvRoot $Venv)
if (-not $venvOk -and (Test-Path $VenvPy)) {
    Write-Host "  ! Venv exists but core modules are missing -- treating as incomplete install." -ForegroundColor Yellow
}
if (-not $venvOk) {
    $InstallDeps = Join-Path $AppDir "scripts\install-deps.ps1"

    Write-Host ""
    Write-Host "  +------------------------------------------------------+" -ForegroundColor Yellow
    Write-Host "  |  Python virtual environment not found.               |" -ForegroundColor Yellow
    Write-Host "  |  ML dependencies were not installed during setup.    |" -ForegroundColor Yellow
    Write-Host "  +------------------------------------------------------+" -ForegroundColor Yellow
    Write-Host ""
    Write-Host "  Expected: $VenvPy" -ForegroundColor Gray
    Write-Host ""
    Write-Host "  I can run the dependency installer for you now."  -ForegroundColor Cyan
    Write-Host "  It will:"
    Write-Host "    - Open an Administrator PowerShell window (UAC prompt)"
    Write-Host "    - Install Python 3.10 silently if missing"
    Write-Host "    - Download PyTorch + nerfstudio + COLMAP + ffmpeg"
    Write-Host "    - ~6 GB total, 10-15 min depending on bandwidth"
    Write-Host ""
    $ans = Read-Host "  Run installer now? [Y/n]"
    if ([string]::IsNullOrEmpty($ans) -or $ans -match '^[yYoO]') {
        Write-Host ""
        Write-Host "  Launching admin installer..." -ForegroundColor Cyan
        Write-Host "  (a console window will open; keep it open until it says DONE)" -ForegroundColor Gray
        try {
            # Pass the *user's* LOCALAPPDATA explicitly so the elevated process
            # writes logs where this (non-elevated) process can read them.
            # -NoExit keeps the elevated window open if anything goes wrong so
            # the user can read the error.
            $psArgs = @(
                '-NoProfile',
                '-ExecutionPolicy', 'Bypass',
                '-NoExit',
                '-File', $InstallDeps,
                '-AppDir', $AppDir,
                '-UserDataDir', $UserData
            )
            Start-Process powershell -Verb RunAs -Wait -ArgumentList $psArgs
            Write-Host ""
            # Re-run the FULL health check, not just Test-Path on python.exe.
            # install-deps can return apparent success while the venv is still
            # missing torch/nerfstudio (e.g. if a pip install half-succeeded).
            if (Test-VenvHealthy -VenvRoot $Venv) {
                Write-Host "  Done. All modules present. Continuing startup..." -ForegroundColor Green
                Write-Host ""
            } else {
                Write-Host "  Installer finished but the venv is still incomplete:" -ForegroundColor Red
                $sp = Join-Path $Venv "Lib\site-packages"
                foreach ($mod in @("uvicorn", "fastapi", "torch", "nerfstudio")) {
                    $present = Test-Path (Join-Path $sp "$mod\__init__.py")
                    $glyph = if ($present) { "OK" } else { "MISSING" }
                    Write-Host ("    {0,-10}  {1}" -f $mod, $glyph) -ForegroundColor $(if ($present) { 'Green' } else { 'Red' })
                }
                Write-Host ""
                Write-Host "  Logs to check:"  -ForegroundColor Gray
                Write-Host "    $LogDir\install.log" -ForegroundColor Gray
                Write-Host "    $env:TEMP\oneclicksplat-install-crash.log" -ForegroundColor Gray
                Read-Host "  Press Enter to exit"
                exit 1
            }
        } catch {
            Write-Host ""
            Write-Host "  Failed to elevate: $_" -ForegroundColor Red
            Write-Host "  Run this manually in an Administrator PowerShell:" -ForegroundColor Cyan
            Write-Host "      & `"$InstallDeps`" -AppDir `"$AppDir`"" -ForegroundColor White
            Read-Host "  Press Enter to exit"
            exit 1
        }
    } else {
        Write-Host ""
        Write-Host "  To install later, run this in an Administrator PowerShell:" -ForegroundColor Cyan
        Write-Host "      & `"$InstallDeps`" -AppDir `"$AppDir`"" -ForegroundColor White
        Write-Host ""
        Read-Host "  Press Enter to exit"
        exit 1
    }
}

# ── PATH bootstrap so backend subprocesses find colmap.exe and ffmpeg.exe
# Order matters: ColmapLib comes first so DLL resolution finds boost/ceres/Qt
# before anything else, then ColmapBin (where colmap.exe sits), then FfmpegBin.
$env:PATH = "$ColmapLib;$ColmapBin;$FfmpegBin;$Venv\Scripts;$env:PATH"
$env:QT_PLUGIN_PATH = "$ColmapLib\plugins;$env:QT_PLUGIN_PATH"

# ── MSVC + CUDA env (required for gsplat JIT compile via torch.utils.cpp_extension)
# gsplat 1.0.0 (pinned by nerfstudio 1.1.4) ships only as a sdist and JIT-compiles
# its CUDA kernels on first call. That needs cl.exe (MSVC) on PATH plus the
# matching INCLUDE/LIB env, AND nvcc + CUDA_HOME for the CUDA side.
function Initialize-MsvcCudaEnv {
    # 1. MSVC -- source vcvars64.bat so we inherit PATH/INCLUDE/LIB
    $vswhere = "${env:ProgramFiles(x86)}\Microsoft Visual Studio\Installer\vswhere.exe"
    if (-not (Test-Path $vswhere)) {
        Write-Host "  ! vswhere.exe not found -- Visual Studio Build Tools likely missing." -ForegroundColor Yellow
        Write-Host "    gsplat CUDA kernels will fail to compile on first training." -ForegroundColor Yellow
        return $false
    }
    $vsInstall = & $vswhere -latest -products * -requires Microsoft.VisualStudio.Component.VC.Tools.x86.x64 -property installationPath 2>$null
    if (-not $vsInstall) {
        Write-Host "  ! No VS install with C++ tools found." -ForegroundColor Yellow
        return $false
    }
    $vcvars = Join-Path $vsInstall "VC\Auxiliary\Build\vcvars64.bat"
    if (-not (Test-Path $vcvars)) {
        Write-Host "  ! vcvars64.bat missing at $vcvars" -ForegroundColor Yellow
        return $false
    }
    # Source vcvars64.bat by running it in cmd, dumping env, and re-importing.
    & cmd /c "`"$vcvars`" >nul 2>&1 && set" 2>$null | ForEach-Object {
        if ($_ -match '^([^=]+)=(.*)$') {
            [Environment]::SetEnvironmentVariable($matches[1], $matches[2], "Process")
        }
    }
    Write-Host "  MSVC env loaded from: $vcvars" -ForegroundColor DarkGray

    # 2. CUDA Toolkit -- needed for nvcc to compile gsplat's .cu files.
    # Prefer CUDA 12.x for newer MSVC compatibility. Fall back to 11.8 if only
    # that's installed. Torch cu118 runtime is forward-compatible with newer
    # nvcc-compiled binaries via the CUDA 11.x driver API.
    # Walk newest -> oldest. CUDA 13.x is required for MSVC >= 14.44.
    # nvcc is backward-compatible at compile time for older arches, so
    # using 13.x with torch+cu118 at runtime works as long as the kernel
    # ABI doesn't pull in symbols that aren't in cudart 11.8.
    $cudaCandidates = @(
        "${env:ProgramFiles}\NVIDIA GPU Computing Toolkit\CUDA\v13.2",
        "${env:ProgramFiles}\NVIDIA GPU Computing Toolkit\CUDA\v13.1",
        "${env:ProgramFiles}\NVIDIA GPU Computing Toolkit\CUDA\v13.0",
        "${env:ProgramFiles}\NVIDIA GPU Computing Toolkit\CUDA\v12.9",
        "${env:ProgramFiles}\NVIDIA GPU Computing Toolkit\CUDA\v12.8",
        "${env:ProgramFiles}\NVIDIA GPU Computing Toolkit\CUDA\v12.1",
        "${env:ProgramFiles}\NVIDIA GPU Computing Toolkit\CUDA\v11.8",
        "$env:CUDA_PATH_V13_2",
        "$env:CUDA_PATH_V12_8",
        "$env:CUDA_PATH_V11_8"
    ) | Where-Object { $_ -and (Test-Path "$_\bin\nvcc.exe") } | Select-Object -First 1
    if (-not $cudaCandidates) {
        Write-Host "  ! No CUDA Toolkit found (tried v13.2 down to v11.8)." -ForegroundColor Yellow
        return $false
    }
    $env:CUDA_HOME = $cudaCandidates
    $env:CUDA_PATH = $cudaCandidates
    $env:PATH = "$cudaCandidates\bin;$cudaCandidates\libnvvp;$env:PATH"
    Write-Host "  CUDA Toolkit:       $cudaCandidates" -ForegroundColor DarkGray
    return $true
}
$msvcOk = Initialize-MsvcCudaEnv
if (-not $msvcOk) {
    Write-Host "  ! Training may fail on first gsplat CUDA call -- install VS Build Tools + CUDA 11.8" -ForegroundColor Yellow
    Write-Host "    https://visualstudio.microsoft.com/visual-cpp-build-tools/" -ForegroundColor Gray
    Write-Host "    https://developer.nvidia.com/cuda-11-8-0-download-archive" -ForegroundColor Gray
}

# ── Tell backend where to write job output ───────────────────────────────
$env:OCS_JOBS_DIR = $JobsDir

# ── Stop any prior backend from THIS install only ────────────────────────
Get-Process python -ErrorAction SilentlyContinue |
    Where-Object { $_.Path -eq $VenvPy } |
    Stop-Process -Force -ErrorAction SilentlyContinue

# ── Detect stale uvicorn squatting port 8000 (e.g. dev session) ──────────
# If something is already listening on 8000 that's NOT our venv, kill it
# rather than failing silently when the new uvicorn can't bind.
$squatter = Get-NetTCPConnection -State Listen -LocalPort 8000 -ErrorAction SilentlyContinue
if ($squatter) {
    $pid_ = $squatter[0].OwningProcess
    $proc = Get-Process -Id $pid_ -ErrorAction SilentlyContinue
    if ($proc -and $proc.Path -ne $VenvPy) {
        Write-Host "  ! Port 8000 was held by another process (PID $pid_, $($proc.ProcessName)). Killing it." -ForegroundColor Yellow
        Stop-Process -Id $pid_ -Force -ErrorAction SilentlyContinue
        Start-Sleep -Milliseconds 500
    }
}

Write-Host ""
Write-Host "  +----------------------------------------+"
Write-Host "  |       OneClick SPLAT  starting...      |"
Write-Host "  +----------------------------------------+"
Write-Host ""
Write-Host "  Install:  $AppDir"
Write-Host "  Data:     $UserData"
Write-Host ""

# ── 1. Backend ───────────────────────────────────────────────────────────
Write-Host "[1/3] Starting FastAPI backend on http://127.0.0.1:8000"

$backendArgs = @('-m', 'uvicorn', 'main:app', '--host', '127.0.0.1', '--port', '8000')
$backend = Start-Process -FilePath $VenvPy `
    -ArgumentList $backendArgs `
    -WorkingDirectory $Server `
    -PassThru `
    -WindowStyle Hidden `
    -RedirectStandardOutput $BackendLog `
    -RedirectStandardError  $BackendLogErr

if (-not $backend) {
    Write-Host "      Failed to spawn backend process." -ForegroundColor Red
    Read-Host "Press Enter to exit"
    exit 1
}

Write-Host "      Waiting for backend to come up..."
$ready = $false
$resp  = $null
for ($i = 0; $i -lt 40; $i++) {
    Start-Sleep -Milliseconds 500
    # Verify our spawned process is still alive before probing
    if (-not (Get-Process -Id $backend.Id -ErrorAction SilentlyContinue)) {
        Write-Host "      Backend process died during startup. Check $BackendLog" -ForegroundColor Red
        if (Test-Path $BackendLogErr) {
            Write-Host "      Last error lines:" -ForegroundColor Gray
            Get-Content $BackendLogErr -Tail 8 -ErrorAction SilentlyContinue |
                ForEach-Object { Write-Host "        $_" -ForegroundColor Gray }
        }
        Read-Host "Press Enter to exit"
        exit 1
    }
    try {
        $resp = Invoke-RestMethod -Uri "http://127.0.0.1:8000/" -TimeoutSec 2
        if ($resp.service) { $ready = $true; break }
    } catch { }
}
if (-not $ready) {
    Write-Host "      Backend failed to respond within 20s. Check $BackendLog" -ForegroundColor Red
    Stop-Process -Id $backend.Id -Force -ErrorAction SilentlyContinue
    Read-Host "Press Enter to exit"
    exit 1
}
Write-Host "      Backend ready (GPU: $($resp.gpu))" -ForegroundColor Green

# ── 2. Frontend (separate ps1 file -- no quoting nightmares) ─────────────
Write-Host "[2/3] Starting static frontend on http://127.0.0.1:3000"

## Quote path args so spaces in "C:\Program Files\..." don't break the spawn.
## Start-Process joins ArgumentList items with single spaces, so a path arg
## containing spaces gets re-split by the child unless we wrap it in quotes.
$frontendArgs = @(
    '-NoProfile',
    '-ExecutionPolicy', 'Bypass',
    '-File', "`"$ServeScript`"",
    '-FrontendDir', "`"$Frontend`"",
    '-Port', '3000'
)
$frontend = Start-Process -FilePath 'powershell.exe' `
    -ArgumentList $frontendArgs `
    -PassThru `
    -WindowStyle Hidden `
    -RedirectStandardOutput $FrontendLog `
    -RedirectStandardError  $FrontendLogErr

Start-Sleep -Milliseconds 800

# ── 3. Browser ──────────────────────────────────────────────────────────
Write-Host "[3/3] Opening browser..."
Start-Process "http://127.0.0.1:3000/"

Write-Host ""
Write-Host "  Backend PID:  $($backend.Id)"
if ($frontend) { Write-Host "  Frontend PID: $($frontend.Id)" }
Write-Host ""
Write-Host "  Logs:   $LogDir"
Write-Host "  Jobs:   $JobsDir"
Write-Host ""

@{
    Backend  = $backend.Id
    Frontend = if ($frontend) { $frontend.Id } else { $null }
    Started  = (Get-Date).ToString('o')
    AppDir   = $AppDir
    UserData = $UserData
} | ConvertTo-Json | Set-Content -Path (Join-Path $LogDir "pids.json") -Encoding UTF8

Write-Host "  Press Ctrl+C or close this window to stop OneClick SPLAT." -ForegroundColor Cyan

try {
    Wait-Process -Id $backend.Id
} catch { }
finally {
    if ($backend  -and $backend.Id)  { Get-Process -Id $backend.Id  -ErrorAction SilentlyContinue | Stop-Process -Force -ErrorAction SilentlyContinue }
    if ($frontend -and $frontend.Id) { Get-Process -Id $frontend.Id -ErrorAction SilentlyContinue | Stop-Process -Force -ErrorAction SilentlyContinue }
    Write-Host "OneClick SPLAT stopped." -ForegroundColor Yellow
}
