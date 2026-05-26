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
    # Probe known Python 3.10 install locations before downloading anything.
    $candidates = @(
        "C:\Program Files\Python310\python.exe",
        "C:\Python310\python.exe",
        "$env:LOCALAPPDATA\Programs\Python\Python310\python.exe",
        "$env:ProgramW6432\Python310\python.exe"
    )
    foreach ($c in $candidates) {
        if (Test-Path $c) {
            $verCheck = (& $c --version 2>&1).ToString()
            if ($verCheck -match "3\.10\.") {
                $Python310 = $c
                Log "Found existing Python 3.10 at $Python310 ($verCheck)"
                break
            }
        }
    }
}

if (-not $Python310) {
    Log "Python 3.10 not found -- downloading installer"
    $PyInstaller = Join-Path $env:TEMP "python-3.10.11-amd64.exe"
    Download-File "https://www.python.org/ftp/python/3.10.11/python-3.10.11-amd64.exe" $PyInstaller

    # Use PER-USER install (no AllUsers) to avoid conflicts with any existing
    # Python (e.g. 3.12 already on the system, registry collisions, AppX).
    # PER-USER installs to %LOCALAPPDATA%\Programs\Python\Python310 and is more
    # forgiving than all-users when another Python build is present.
    Log "Running Python 3.10 silent install (per-user)"
    $pyLog = Join-Path $env:TEMP "python-3.10-install.log"
    $proc = Start-Process -FilePath $PyInstaller -ArgumentList @(
        "/quiet",
        "/log", "`"$pyLog`"",
        "InstallAllUsers=0",
        "PrependPath=1",
        "Include_test=0",
        "Include_doc=0",
        "Include_launcher=1",
        "TargetDir=`"$env:LOCALAPPDATA\Programs\Python\Python310`""
    ) -Wait -NoNewWindow -PassThru
    $rc = if ($proc) { $proc.ExitCode } else { -1 }
    Log "Python 3.10 installer exited with code $rc"
    if (Test-Path $pyLog) {
        Log "Python installer log (last 20 lines):"
        Get-Content $pyLog -Tail 20 -ErrorAction SilentlyContinue | ForEach-Object { Log "  $_" }
    }

    # Refresh PATH for this process
    $env:Path = [Environment]::GetEnvironmentVariable("Path", "Machine") + ";" + [Environment]::GetEnvironmentVariable("Path", "User")

    # Check both per-user and all-users locations
    $found = @(
        "$env:LOCALAPPDATA\Programs\Python\Python310\python.exe",
        "C:\Program Files\Python310\python.exe",
        "C:\Python310\python.exe"
    ) | Where-Object { Test-Path $_ } | Select-Object -First 1

    if ($found) {
        $Python310 = $found
        Log "Python 3.10 installed at $Python310"
    } else {
        throw "Python 3.10 install failed (exit $rc). See $pyLog for details. As a workaround, install Python 3.10 manually from https://www.python.org/downloads/release/python-31011/ then re-run this script."
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

# Wrapper that runs pip via Start-Process so its output streams live to the
# console (user sees download progress) instead of being silently captured.
# Avoids the Tee-Object / NativeCommandError pipeline pitfalls of PS 5.1.
function Invoke-Pip {
    param(
        [Parameter(Mandatory = $true)] [string]$Label,
        [Parameter(Mandatory = $true)] [string[]]$Args
    )
    Write-Host ""
    Write-Host "================================================" -ForegroundColor Cyan
    Write-Host "  $Label" -ForegroundColor Cyan
    Write-Host "================================================" -ForegroundColor Cyan
    Log "[BEGIN] $Label"
    $proc = Start-Process -FilePath $VenvPip `
        -ArgumentList $Args `
        -NoNewWindow -Wait -PassThru
    $rc = if ($proc) { $proc.ExitCode } else { -1 }
    Log "[END]   $Label  (exit $rc)"
    return $rc
}

# Upgrade pip first (using $VenvPy directly since $VenvPip will be replaced)
Write-Host ""
Write-Host "================================================" -ForegroundColor Cyan
Write-Host "  Upgrading pip / wheel / setuptools" -ForegroundColor Cyan
Write-Host "================================================" -ForegroundColor Cyan
Log "[BEGIN] pip upgrade"
$proc = Start-Process -FilePath $VenvPy `
    -ArgumentList @('-m', 'pip', 'install', '--upgrade', 'pip', 'wheel', 'setuptools') `
    -NoNewWindow -Wait -PassThru
$rc = if ($proc) { $proc.ExitCode } else { -1 }
Log "[END]   pip upgrade  (exit $rc)"
if ($rc -ne 0) { throw "pip upgrade failed (exit $rc)" }

# ─────────────────────────────────────────────────────────────────────────────
# 3. PyTorch CUDA 11.8
# ─────────────────────────────────────────────────────────────────────────────
# IMPORTANT: do NOT use `& python -c "import torch" 2>$null` to probe -- when
# the import fails, Python writes to stderr; PowerShell 5.1 wraps each stderr
# line in a NativeCommandError record; with $ErrorActionPreference = "Stop"
# the script dies silently on this line. Probe via filesystem instead.
Log "Checking for existing PyTorch install..."
$TorchInstalled = $null
$TorchSitePath  = Join-Path $Venv "Lib\site-packages\torch\version.py"
if (Test-Path $TorchSitePath) {
    try {
        $verLine = Select-String -Path $TorchSitePath -Pattern "__version__\s*=" -ErrorAction SilentlyContinue | Select-Object -First 1
        if ($verLine) { $TorchInstalled = ($verLine.Line -split "'|""")[1] }
    } catch { $TorchInstalled = "unknown" }
}
Log "Torch probe -> $(if ($TorchInstalled) { $TorchInstalled } else { 'not found' })"

if (-not $TorchInstalled) {
    $rc = Invoke-Pip -Label "Installing PyTorch 2.1.2 + CUDA 11.8  (~2.7 GB, 3-6 min)" -Args @(
        'install', '--no-cache-dir',
        'torch==2.1.2+cu118',
        'torchvision==0.16.2+cu118',
        '--index-url', 'https://download.pytorch.org/whl/cu118'
    )
    if ($rc -ne 0) { throw "PyTorch install failed (exit $rc)" }
} else {
    Log "PyTorch already installed: $TorchInstalled"
}

# ─────────────────────────────────────────────────────────────────────────────
# 4. nerfstudio
# ─────────────────────────────────────────────────────────────────────────────
Log "Checking for existing nerfstudio install..."
$NSInstalled = $null
$NSInitPath  = Join-Path $Venv "Lib\site-packages\nerfstudio\__init__.py"
if (Test-Path $NSInitPath) {
    $NSInstalled = "present"
}
Log "nerfstudio probe -> $(if ($NSInstalled) { 'present' } else { 'not found' })"

if (-not $NSInstalled) {
    $rc = Invoke-Pip -Label "Installing nerfstudio  (~2 GB, 8-15 min -- DO NOT close this window)" -Args @(
        'install', '--no-cache-dir', 'nerfstudio==1.1.4'
    )
    if ($rc -ne 0) { throw "nerfstudio install failed (exit $rc)" }
} else {
    Log "nerfstudio already installed: $NSInstalled"
}

# ─────────────────────────────────────────────────────────────────────────────
# 5. FastAPI + utilities
# ─────────────────────────────────────────────────────────────────────────────
$rc = Invoke-Pip -Label "Installing FastAPI server deps  (~50 MB, 30 s)" -Args @(
    'install', '--no-cache-dir',
    'fastapi==0.115.0',
    'uvicorn[standard]==0.30.6',
    'sse-starlette==2.1.3',
    'python-multipart==0.0.9',
    'pillow',
    'numpy'
)
if ($rc -ne 0) { throw "FastAPI deps install failed (exit $rc)" }

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

Write-Host ""
Write-Host "================================================" -ForegroundColor Green
Write-Host "                                                " -ForegroundColor Green
Write-Host "         DONE -- ALL DEPENDENCIES INSTALLED     " -ForegroundColor Green
Write-Host "                                                " -ForegroundColor Green
Write-Host "  You can now close this window and launch      " -ForegroundColor Green
Write-Host "  OneClick SPLAT from the desktop / Start menu. " -ForegroundColor Green
Write-Host "                                                " -ForegroundColor Green
Write-Host "================================================" -ForegroundColor Green
Write-Host ""
Write-Host "This window will close automatically in 10 seconds..." -ForegroundColor Gray
Start-Sleep -Seconds 10
exit 0
