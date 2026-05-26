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
$FfmpegBin    = Join-Path $AppDir "tools\ffmpeg\bin"
$ServeScript  = Join-Path $AppDir "scripts\serve-frontend.ps1"

# ── Sanity check: was install-deps run AND did it finish? ───────────────
# Just checking python.exe isn't enough — install-deps can stop midway
# (e.g. after pip upgrade, before torch install), leaving a half-built venv.
$venvOk = $false
if (Test-Path $VenvPy) {
    # Verify the critical modules import cleanly. If any missing, treat
    # the install as incomplete and re-trigger the deps installer.
    & $VenvPy -c "import uvicorn, fastapi, torch, nerfstudio" 2>$null
    $venvOk = ($LASTEXITCODE -eq 0)
    if (-not $venvOk) {
        Write-Host "  ! Venv exists but core modules are missing -- treating as incomplete install." -ForegroundColor Yellow
    }
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
            if (Test-Path $VenvPy) {
                Write-Host "  Done. Continuing startup..." -ForegroundColor Green
                Write-Host ""
            } else {
                Write-Host "  Installer finished but venv is still missing." -ForegroundColor Red
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
$env:PATH = "$ColmapBin;$FfmpegBin;$Venv\Scripts;$env:PATH"

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

$frontendArgs = @(
    '-NoProfile',
    '-ExecutionPolicy', 'Bypass',
    '-File', $ServeScript,
    '-FrontendDir', $Frontend,
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
