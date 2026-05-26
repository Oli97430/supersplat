# ============================================================================
# OneClick SPLAT — launcher
# Starts the FastAPI backend, serves the prebuilt frontend, opens the browser.
# Runtime data (logs, jobs, pids) goes to %LOCALAPPDATA%\OneClickSPLAT
# so it survives a read-only Program Files installation.
# ============================================================================

param([string]$AppDir = (Split-Path $PSScriptRoot -Parent))

$ErrorActionPreference = "Continue"
$ProgressPreference    = "SilentlyContinue"

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

# ── Sanity check: was install-deps run? ──────────────────────────────────
if (-not (Test-Path $VenvPy)) {
    Write-Host ""
    Write-Host "  +------------------------------------------------------+" -ForegroundColor Yellow
    Write-Host "  |  Python virtual environment not found.               |" -ForegroundColor Yellow
    Write-Host "  |  ML dependencies were not installed during setup.    |" -ForegroundColor Yellow
    Write-Host "  +------------------------------------------------------+" -ForegroundColor Yellow
    Write-Host ""
    Write-Host "  Expected:  $VenvPy" -ForegroundColor Gray
    Write-Host ""
    Write-Host "  To finish the install, run this in an Administrator PowerShell:" -ForegroundColor Cyan
    Write-Host ""
    Write-Host "      & '$AppDir\scripts\install-deps.ps1' -AppDir '$AppDir'" -ForegroundColor White
    Write-Host ""
    Write-Host "  ~6 GB download, 10-15 min." -ForegroundColor Gray
    Write-Host ""
    Read-Host "Press Enter to exit"
    exit 1
}

# ── PATH bootstrap so backend subprocesses find colmap.exe and ffmpeg.exe
$env:PATH = "$ColmapBin;$FfmpegBin;$Venv\Scripts;$env:PATH"

# ── Tell backend where to write job output ───────────────────────────────
$env:OCS_JOBS_DIR = $JobsDir

# ── Stop any prior backend from THIS install only ────────────────────────
Get-Process python -ErrorAction SilentlyContinue |
    Where-Object { $_.Path -eq $VenvPy } |
    Stop-Process -Force -ErrorAction SilentlyContinue

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
    try {
        $resp = Invoke-RestMethod -Uri "http://127.0.0.1:8000/" -TimeoutSec 2
        if ($resp.service) { $ready = $true; break }
    } catch { }
}
if (-not $ready) {
    Write-Host "      Backend failed to start. Check $BackendLog" -ForegroundColor Red
    Stop-Process -Id $backend.Id -Force -ErrorAction SilentlyContinue
    Read-Host "Press Enter to exit"
    exit 1
}
Write-Host "      Backend ready (GPU: $($resp.gpu))" -ForegroundColor Green

# ── 2. Frontend (separate ps1 file — no quoting nightmares) ─────────────
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
