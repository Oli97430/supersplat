# ============================================================================
# OneClick SPLAT -- first-run dependency installer
# Runs as Administrator from the Inno Setup [Run] section.
# Idempotent: safe to re-run if a step fails halfway.
# ============================================================================

param(
    [Parameter(Mandatory = $true)]
    [string]$AppDir,
    # Caller (launch.ps1) passes the original non-elevated user's LOCALAPPDATA
    # so logs land where the calling process can read them.
    [string]$UserDataDir = ""
)

$ErrorActionPreference = "Stop"
$ProgressPreference    = "SilentlyContinue"  # speeds up Invoke-WebRequest

# Log to LOCALAPPDATA (always writable) with a fallback to AppDir if available.
if ([string]::IsNullOrWhiteSpace($UserDataDir)) {
    $UserDataDir = Join-Path $env:LOCALAPPDATA "OneClickSPLAT"
}
$LogDir = Join-Path $UserDataDir "logs"
try {
    New-Item -ItemType Directory -Path $LogDir -Force | Out-Null
    $LogPath = Join-Path $LogDir "install.log"
} catch {
    # Last-resort fallback (admin run, AppDir writable)
    $LogPath = Join-Path $AppDir "logs\install.log"
    New-Item -ItemType Directory -Path (Split-Path $LogPath) -Force | Out-Null
}

function Log {
    param([string]$Msg, [string]$Level = "INFO")
    $stamp = (Get-Date).ToString("yyyy-MM-dd HH:mm:ss")
    $line  = "[$stamp] [$Level] $Msg"
    Add-Content -Path $LogPath -Value $line -Encoding UTF8
    Write-Host $line
}

function Download-File {
    param([string]$Url, [string]$Dest)
    Log "Downloading $Url"
    try {
        Invoke-WebRequest -Uri $Url -OutFile $Dest -UseBasicParsing -TimeoutSec 600
        Log "  -> $Dest ($([math]::Round((Get-Item $Dest).Length/1MB,1)) MB)"
    } catch {
        Log "FAILED to download: $_" "ERROR"
        throw
    }
}

function Expand-ZipTo {
    param([string]$Zip, [string]$Dest)
    Log "Extracting $Zip -> $Dest"
    if (Test-Path $Dest) { Remove-Item -Recurse -Force $Dest -ErrorAction SilentlyContinue }
    New-Item -ItemType Directory -Path $Dest -Force | Out-Null
    Expand-Archive -Path $Zip -DestinationPath $Dest -Force
}

Log "=== OneClick SPLAT install-deps START ==="
Log "AppDir: $AppDir"

# ─────────────────────────────────────────────────────────────────────────────
# 1. Python 3.10
# ─────────────────────────────────────────────────────────────────────────────
$PythonExe = Get-Command python -ErrorAction SilentlyContinue
$Python310 = $null

if ($PythonExe) {
    $ver = (& $PythonExe.Source --version 2>&1).ToString()
    Log "Found system Python: $ver"
    if ($ver -match "3\.10\.") { $Python310 = $PythonExe.Source }
}

if (-not $Python310) {
    Log "Python 3.10 not found -- downloading installer"
    $PyInstaller = Join-Path $env:TEMP "python-3.10.11-amd64.exe"
    Download-File "https://www.python.org/ftp/python/3.10.11/python-3.10.11-amd64.exe" $PyInstaller
    Log "Running Python 3.10 silent install"
    Start-Process -FilePath $PyInstaller -ArgumentList @(
        "/quiet",
        "InstallAllUsers=1",
        "PrependPath=1",
        "Include_test=0",
        "Include_doc=0",
        "Include_launcher=1"
    ) -Wait -NoNewWindow
    # Refresh PATH for this process
    $env:Path = [Environment]::GetEnvironmentVariable("Path", "Machine") + ";" + [Environment]::GetEnvironmentVariable("Path", "User")
    $Python310 = "C:\Program Files\Python310\python.exe"
    if (-not (Test-Path $Python310)) {
        $Python310 = "$env:LOCALAPPDATA\Programs\Python\Python310\python.exe"
    }
    if (-not (Test-Path $Python310)) {
        throw "Python 3.10 install appears to have failed -- $Python310 not found"
    }
}
Log "Using Python: $Python310"

# ─────────────────────────────────────────────────────────────────────────────
# 2. Create venv
# ─────────────────────────────────────────────────────────────────────────────
$Venv = Join-Path $AppDir "venv"
if (-not (Test-Path "$Venv\Scripts\python.exe")) {
    Log "Creating venv at $Venv"
    & $Python310 -m venv $Venv
    if ($LASTEXITCODE -ne 0) { throw "venv creation failed" }
}
$VenvPy  = Join-Path $Venv "Scripts\python.exe"
$VenvPip = Join-Path $Venv "Scripts\pip.exe"
Log "venv ready: $VenvPy"

# Upgrade pip
& $VenvPy -m pip install --upgrade pip wheel setuptools 2>&1 | Tee-Object -Append -FilePath $LogPath | Out-Null

# ─────────────────────────────────────────────────────────────────────────────
# 3. PyTorch CUDA 11.8
# ─────────────────────────────────────────────────────────────────────────────
$TorchInstalled = & $VenvPy -c "import torch; print(torch.__version__)" 2>$null
if (-not $TorchInstalled -or $LASTEXITCODE -ne 0) {
    Log "Installing PyTorch 2.1.2 + CUDA 11.8 (this can take several minutes -- ~2.7 GB)"
    & $VenvPip install --no-cache-dir `
        torch==2.1.2+cu118 `
        torchvision==0.16.2+cu118 `
        --index-url https://download.pytorch.org/whl/cu118 2>&1 |
        Tee-Object -Append -FilePath $LogPath
    if ($LASTEXITCODE -ne 0) { throw "PyTorch install failed" }
} else {
    Log "PyTorch already installed: $TorchInstalled"
}

# ─────────────────────────────────────────────────────────────────────────────
# 4. nerfstudio
# ─────────────────────────────────────────────────────────────────────────────
$NSInstalled = & $VenvPy -c "import nerfstudio; print(nerfstudio.__version__)" 2>$null
if (-not $NSInstalled) {
    Log "Installing nerfstudio (this will pull tinycudann etc., ~2 GB)"
    & $VenvPip install --no-cache-dir nerfstudio==1.1.4 2>&1 | Tee-Object -Append -FilePath $LogPath
    if ($LASTEXITCODE -ne 0) { throw "nerfstudio install failed" }
} else {
    Log "nerfstudio already installed: $NSInstalled"
}

# ─────────────────────────────────────────────────────────────────────────────
# 5. FastAPI + utilities
# ─────────────────────────────────────────────────────────────────────────────
Log "Installing FastAPI server deps"
& $VenvPip install --no-cache-dir `
    "fastapi==0.115.0" `
    "uvicorn[standard]==0.30.6" `
    "sse-starlette==2.1.3" `
    "python-multipart==0.0.9" `
    "pillow" `
    "numpy" 2>&1 | Tee-Object -Append -FilePath $LogPath
if ($LASTEXITCODE -ne 0) { throw "FastAPI deps install failed" }

# ─────────────────────────────────────────────────────────────────────────────
# 6. COLMAP (Windows pre-built binary)
# ─────────────────────────────────────────────────────────────────────────────
$ColmapDir = Join-Path $AppDir "tools\colmap"
if (-not (Test-Path "$ColmapDir\bin\colmap.exe")) {
    Log "Downloading COLMAP 3.9.1 Windows CUDA build (~750 MB)"
    $ColmapZip = Join-Path $env:TEMP "colmap.zip"
    Download-File "https://github.com/colmap/colmap/releases/download/3.9.1/COLMAP-3.9.1-windows-cuda.zip" $ColmapZip
    Expand-ZipTo $ColmapZip (Join-Path $AppDir "tools\colmap-tmp")
    # Move the inner folder up one level
    $inner = Get-ChildItem (Join-Path $AppDir "tools\colmap-tmp") -Directory | Select-Object -First 1
    if ($inner) {
        if (Test-Path $ColmapDir) { Remove-Item -Recurse -Force $ColmapDir }
        Move-Item $inner.FullName $ColmapDir
    }
    Remove-Item -Recurse -Force (Join-Path $AppDir "tools\colmap-tmp") -ErrorAction SilentlyContinue
    Remove-Item -Force $ColmapZip -ErrorAction SilentlyContinue

    if (-not (Test-Path "$ColmapDir\bin\colmap.exe")) {
        # Fallback structure
        if (Test-Path "$ColmapDir\COLMAP.bat") {
            Log "COLMAP installed (alternate structure)"
        } else {
            throw "COLMAP install failed -- colmap.exe not found"
        }
    }
} else {
    Log "COLMAP already installed"
}

# ─────────────────────────────────────────────────────────────────────────────
# 7. ffmpeg (static Windows build)
# ─────────────────────────────────────────────────────────────────────────────
$FfmpegDir = Join-Path $AppDir "tools\ffmpeg"
if (-not (Test-Path "$FfmpegDir\bin\ffmpeg.exe")) {
    Log "Downloading ffmpeg (~100 MB)"
    $FfmpegZip = Join-Path $env:TEMP "ffmpeg.zip"
    Download-File "https://www.gyan.dev/ffmpeg/builds/ffmpeg-release-essentials.zip" $FfmpegZip
    Expand-ZipTo $FfmpegZip (Join-Path $AppDir "tools\ffmpeg-tmp")
    $inner = Get-ChildItem (Join-Path $AppDir "tools\ffmpeg-tmp") -Directory | Select-Object -First 1
    if ($inner) {
        if (Test-Path $FfmpegDir) { Remove-Item -Recurse -Force $FfmpegDir }
        Move-Item $inner.FullName $FfmpegDir
    }
    Remove-Item -Recurse -Force (Join-Path $AppDir "tools\ffmpeg-tmp") -ErrorAction SilentlyContinue
    Remove-Item -Force $FfmpegZip -ErrorAction SilentlyContinue

    if (-not (Test-Path "$FfmpegDir\bin\ffmpeg.exe")) {
        throw "ffmpeg install failed -- ffmpeg.exe not found"
    }
} else {
    Log "ffmpeg already installed"
}

# ─────────────────────────────────────────────────────────────────────────────
# 8. Persist tools to PATH for the backend
# ─────────────────────────────────────────────────────────────────────────────
$ToolsBin = @(
    "$ColmapDir\bin",
    "$FfmpegDir\bin"
) -join ";"

# Write a small PATH bootstrap that the launcher will source
$PathFile = Join-Path $AppDir "scripts\paths.env.cmd"
@"
@echo off
set "PATH=$ToolsBin;$Venv\Scripts;%PATH%"
"@ | Set-Content -Path $PathFile -Encoding ASCII

Log "=== install-deps COMPLETE ==="
Log "Run $AppDir\OneClickSPLAT.cmd to start."
