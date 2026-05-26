# ============================================================================
# OneClick SPLAT — launcher
# Starts the FastAPI backend, serves the prebuilt frontend, opens the browser.
# Runtime data (logs, jobs, pids) goes to %LOCALAPPDATA%\OneClickSPLAT
# so it survives a read-only Program Files installation.
# ============================================================================

param([string]$AppDir = (Split-Path $PSScriptRoot -Parent))

$ErrorActionPreference = "Continue"
$ProgressPreference    = "SilentlyContinue"

# ── User-writable data location (Program Files is read-only) ─────────────
$UserData = Join-Path $env:LOCALAPPDATA "OneClickSPLAT"
$LogDir   = Join-Path $UserData "logs"
$JobsDir  = Join-Path $UserData "jobs"
New-Item -ItemType Directory -Path $LogDir  -Force | Out-Null
New-Item -ItemType Directory -Path $JobsDir -Force | Out-Null

$BackendLog  = Join-Path $LogDir "backend.log"
$FrontendLog = Join-Path $LogDir "frontend.log"

# ── Resolve install-time paths ───────────────────────────────────────────
$Venv      = Join-Path $AppDir "venv"
$VenvPy    = Join-Path $Venv  "Scripts\python.exe"
$Server    = Join-Path $AppDir "server"
$Frontend  = Join-Path $AppDir "frontend"
$ColmapBin = Join-Path $AppDir "tools\colmap\bin"
$FfmpegBin = Join-Path $AppDir "tools\ffmpeg\bin"

# ── Sanity check: was install-deps.ps1 actually run? ─────────────────────
if (-not (Test-Path $VenvPy)) {
    Write-Host ""
    Write-Host "  ┌────────────────────────────────────────────────────────┐" -ForegroundColor Yellow
    Write-Host "  │  Python virtual environment not found.                 │" -ForegroundColor Yellow
    Write-Host "  │  ML dependencies were not installed during setup.      │" -ForegroundColor Yellow
    Write-Host "  └────────────────────────────────────────────────────────┘" -ForegroundColor Yellow
    Write-Host ""
    Write-Host "  Expected:  $VenvPy" -ForegroundColor Gray
    Write-Host ""
    Write-Host "  To finish the install, run this in an Administrator PowerShell:" -ForegroundColor Cyan
    Write-Host ""
    Write-Host "      Start-Process powershell -Verb RunAs -ArgumentList ``" -ForegroundColor White
    Write-Host "          '-NoProfile -ExecutionPolicy Bypass -File `"$AppDir\scripts\install-deps.ps1`" -AppDir `"$AppDir`"'" -ForegroundColor White
    Write-Host ""
    Write-Host "  This will download Python + PyTorch + nerfstudio + COLMAP + ffmpeg (~6 GB, 10-15 min)." -ForegroundColor Gray
    Write-Host ""
    Read-Host "Press Enter to exit"
    exit 1
}

# ── Bootstrap PATH so backend subprocess finds colmap.exe and ffmpeg.exe ─
$env:PATH = "$ColmapBin;$FfmpegBin;$Venv\Scripts;$env:PATH"

# ── Tell the backend where to put job output ─────────────────────────────
$env:OCS_JOBS_DIR = $JobsDir

# ── Stop any previous instance from this same Python venv ────────────────
Get-Process python -ErrorAction SilentlyContinue |
    Where-Object { $_.Path -eq $VenvPy } |
    Stop-Process -Force -ErrorAction SilentlyContinue

Write-Host ""
Write-Host "  ┌────────────────────────────────────────┐"
Write-Host "  │       OneClick SPLAT  starting…        │"
Write-Host "  └────────────────────────────────────────┘"
Write-Host ""
Write-Host "  Install:  $AppDir"
Write-Host "  Data:     $UserData"
Write-Host ""

# ── Backend ──────────────────────────────────────────────────────────────
Write-Host "[1/3] Starting FastAPI backend on http://127.0.0.1:8000"
$backendArgs = @(
    "-m", "uvicorn", "main:app",
    "--host", "127.0.0.1",
    "--port", "8000"
)
$backend = Start-Process -FilePath $VenvPy `
    -ArgumentList $backendArgs `
    -WorkingDirectory $Server `
    -PassThru `
    -WindowStyle Hidden `
    -RedirectStandardOutput $BackendLog `
    -RedirectStandardError  "$BackendLog.err"

if (-not $backend) {
    Write-Host "      Failed to spawn backend process." -ForegroundColor Red
    Write-Host "      Check log: $BackendLog" -ForegroundColor Red
    Read-Host "Press Enter to exit"
    exit 1
}

# Wait for backend to respond
Write-Host "      Waiting for backend to come up…"
$ready = $false
$resp  = $null
for ($i = 0; $i -lt 40; $i++) {
    Start-Sleep -Milliseconds 500
    try {
        $resp  = Invoke-RestMethod -Uri "http://127.0.0.1:8000/" -TimeoutSec 2
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

# ── Frontend (lightweight static server using HttpListener) ──────────────
Write-Host "[2/3] Starting static frontend on http://127.0.0.1:3000"
$frontendCmd = "powershell"
$frontendArgs = @(
    "-NoProfile", "-Command",
    "& { Add-Type -AssemblyName System.Web; " +
    "`$listener = [System.Net.HttpListener]::new(); " +
    "`$listener.Prefixes.Add('http://127.0.0.1:3000/'); " +
    "`$listener.Start(); " +
    "while (`$listener.IsListening) { " +
    "  try { " +
    "    `$ctx = `$listener.GetContext(); " +
    "    `$rel = `$ctx.Request.Url.AbsolutePath.TrimStart('/'); " +
    "    if (-not `$rel) { `$rel = 'index.html' } " +
    "    `$path = Join-Path '$Frontend' `$rel; " +
    "    if (Test-Path `$path -PathType Leaf) { " +
    "      `$bytes = [IO.File]::ReadAllBytes(`$path); " +
    "      `$ext = [IO.Path]::GetExtension(`$path); " +
    "      `$mime = switch (`$ext) { '.html' {'text/html'} '.js' {'application/javascript'} '.css' {'text/css'} '.json' {'application/json'} '.png' {'image/png'} '.svg' {'image/svg+xml'} '.wasm' {'application/wasm'} '.woff2' {'font/woff2'} default {'application/octet-stream'} }; " +
    "      `$ctx.Response.ContentType = `$mime; " +
    "      `$ctx.Response.OutputStream.Write(`$bytes, 0, `$bytes.Length); " +
    "    } else { `$ctx.Response.StatusCode = 404 } " +
    "    `$ctx.Response.Close(); " +
    "  } catch { } " +
    "} }"
)
$frontend = Start-Process -FilePath $frontendCmd `
    -ArgumentList $frontendArgs `
    -PassThru `
    -WindowStyle Hidden `
    -RedirectStandardOutput $FrontendLog `
    -RedirectStandardError  "$FrontendLog.err"

Start-Sleep -Milliseconds 800

# ── Browser ──────────────────────────────────────────────────────────────
Write-Host "[3/3] Opening browser…"
Start-Process "http://127.0.0.1:3000/"

Write-Host ""
Write-Host "  Backend PID:  $($backend.Id)"
Write-Host "  Frontend PID: $($frontend.Id)"
Write-Host ""
Write-Host "  Logs:   $LogDir"
Write-Host "  Jobs:   $JobsDir"
Write-Host ""

# Save PIDs so we can stop them later
@{
    Backend  = $backend.Id
    Frontend = if ($frontend) { $frontend.Id } else { $null }
    Started  = (Get-Date).ToString("o")
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
