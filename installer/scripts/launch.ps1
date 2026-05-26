# ============================================================================
# OneClick SPLAT — launcher
# Starts the FastAPI backend, serves the prebuilt frontend, opens the browser.
# ============================================================================

param([string]$AppDir = (Split-Path $PSScriptRoot -Parent))

$ErrorActionPreference = "Continue"
$ProgressPreference    = "SilentlyContinue"

$LogDir = Join-Path $AppDir "logs"
New-Item -ItemType Directory -Path $LogDir -Force | Out-Null
$BackendLog  = Join-Path $LogDir "backend.log"
$FrontendLog = Join-Path $LogDir "frontend.log"

$Venv     = Join-Path $AppDir "venv"
$VenvPy   = Join-Path $Venv  "Scripts\python.exe"
$Server   = Join-Path $AppDir "server"
$Frontend = Join-Path $AppDir "frontend"
$ColmapBin  = Join-Path $AppDir "tools\colmap\bin"
$FfmpegBin  = Join-Path $AppDir "tools\ffmpeg\bin"

# Bootstrap PATH so backend subprocess can find colmap.exe and ffmpeg.exe
$env:PATH = "$ColmapBin;$FfmpegBin;$Venv\Scripts;$env:PATH"

# Stop any previous instance
Get-Process python -ErrorAction SilentlyContinue |
    Where-Object { $_.Path -eq $VenvPy } |
    Stop-Process -Force -ErrorAction SilentlyContinue

Write-Host ""
Write-Host "  ┌────────────────────────────────────────┐"
Write-Host "  │       OneClick SPLAT  starting…        │"
Write-Host "  └────────────────────────────────────────┘"
Write-Host ""

# ── Backend ───────────────────────────────────────────────────────────────
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

# Wait for backend to respond
Write-Host "      Waiting for backend to come up…"
$ready = $false
for ($i = 0; $i -lt 40; $i++) {
    Start-Sleep -Milliseconds 500
    try {
        $r = Invoke-RestMethod -Uri "http://127.0.0.1:8000/" -TimeoutSec 2
        if ($r.service) { $ready = $true; break }
    } catch { }
}
if (-not $ready) {
    Write-Host "      Backend failed to start. Check $BackendLog" -ForegroundColor Red
    Read-Host "Press Enter to exit"
    exit 1
}
Write-Host "      Backend ready (GPU: $($r.gpu))" -ForegroundColor Green

# ── Frontend ──────────────────────────────────────────────────────────────
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

# ── Browser ───────────────────────────────────────────────────────────────
Write-Host "[3/3] Opening browser…"
Start-Process "http://127.0.0.1:3000/"

Write-Host ""
Write-Host "  Backend PID:  $($backend.Id)"
Write-Host "  Frontend PID: $($frontend.Id)"
Write-Host ""
Write-Host "  Logs:   $LogDir"
Write-Host "  Stop:   close this window, or run scripts\stop.ps1"
Write-Host ""

# Save PIDs so we can stop them later
@{
    Backend  = $backend.Id
    Frontend = $frontend.Id
    Started  = (Get-Date).ToString("o")
} | ConvertTo-Json | Set-Content -Path (Join-Path $LogDir "pids.json")

# Block so closing the console stops the server
Write-Host "  Press Ctrl+C or close this window to stop OneClick SPLAT." -ForegroundColor Cyan
try {
    Wait-Process -Id $backend.Id
} catch { }
finally {
    Get-Process -Id $backend.Id  -ErrorAction SilentlyContinue | Stop-Process -Force -ErrorAction SilentlyContinue
    Get-Process -Id $frontend.Id -ErrorAction SilentlyContinue | Stop-Process -Force -ErrorAction SilentlyContinue
    Write-Host "OneClick SPLAT stopped." -ForegroundColor Yellow
}
