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

Write-Host ""
Write-Host "================================================" -ForegroundColor Yellow
Write-Host "  OneClick SPLAT -- dependency installer        " -ForegroundColor Yellow
Write-Host "  6 steps total -- DO NOT close until you see   " -ForegroundColor Yellow
Write-Host "  the GREEN banner saying ALL DEPENDENCIES OK   " -ForegroundColor Yellow
Write-Host "================================================" -ForegroundColor Yellow
Write-Host ""

# Wrap the whole install in try/catch so any error stays visible -- without
# this, a throw closes the window before the user can read the message.
try {

# ─────────────────────────────────────────────────────────────────────────────
# STEP 1/6 -- Python 3.10
# ─────────────────────────────────────────────────────────────────────────────
Write-Host "[STEP 1/6] Verifying Python 3.10" -ForegroundColor Cyan
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

# Wrapper that runs pip with direct invocation so output streams live to the
# console (user sees download progress).
#
# IMPORTANT: we previously used `Start-Process -NoNewWindow -Wait -PassThru`
# but that has a known PS 5.1 deadlock: after pip exits, WaitForExit can hang
# for many minutes waiting for inherited console handles to release. Observed
# in v2.27.15: pip nerfstudio finished but Start-Process didn't return for
# ~18 min, with no CPU activity, blocking the whole install.
#
# Direct call (& $VenvPip @Args) avoids that. The temporary
# $ErrorActionPreference = "Continue" prevents PS 5.1 from wrapping pip's
# stderr lines in NativeCommandError (which would otherwise trip the script
# under $ErrorActionPreference = "Stop").
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
    $prevEAP = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    try {
        # | Out-Host  =>  pipe pip's stdout to the console so the user sees
        # download progress, AND consume the success stream so it does NOT
        # pollute the function's return value. Without the pipe, pip's stdout
        # lines bubble up as the function's output and get concatenated with
        # $rc, turning the int return into a string array that breaks every
        # subsequent `if ($rc -ne 0)` check.
        & $VenvPip @Args | Out-Host
        $rc = $LASTEXITCODE
    } finally {
        $ErrorActionPreference = $prevEAP
    }
    Log "[END]   $Label  (exit $rc)"
    return $rc
}

# Upgrade pip first (using $VenvPy directly since $VenvPip will be replaced)
Write-Host ""
Write-Host "================================================" -ForegroundColor Cyan
Write-Host "  [STEP 2/6] Upgrading pip / wheel / setuptools" -ForegroundColor Cyan
Write-Host "================================================" -ForegroundColor Cyan
Log "[BEGIN] pip upgrade"
# Pin setuptools < 70 -- setuptools 80+ removed pkg_resources, which torch
# 2.1.2's torch/utils/cpp_extension.py imports at runtime when gsplat loads
# its CUDA extensions. Without this pin, training fails on the first iter
# with "ModuleNotFoundError: No module named 'pkg_resources'".
$prevEAP = $ErrorActionPreference
$ErrorActionPreference = "Continue"
try {
    & $VenvPy -m pip install --upgrade pip wheel "setuptools<70" | Out-Host
    $rc = $LASTEXITCODE
} finally {
    $ErrorActionPreference = $prevEAP
}
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
    $rc = Invoke-Pip -Label "[STEP 3/6] Installing PyTorch 2.1.2 + CUDA 11.8  (~2.7 GB, 3-6 min)" -Args @(
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
    $rc = Invoke-Pip -Label "[STEP 4/6] Installing nerfstudio  (~2 GB, 8-15 min -- DO NOT close, more steps after this!)" -Args @(
        'install', '--no-cache-dir', 'nerfstudio==1.1.4'
    )
    if ($rc -ne 0) { throw "nerfstudio install failed (exit $rc)" }
} else {
    Log "nerfstudio already installed: $NSInstalled"
}

# ─────────────────────────────────────────────────────────────────────────────
# 5. FastAPI + utilities
# ─────────────────────────────────────────────────────────────────────────────
$rc = Invoke-Pip -Label "[STEP 5/6] Installing FastAPI server deps  (~50 MB, 30 s)" -Args @(
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
Write-Host ""
Write-Host "================================================" -ForegroundColor Cyan
Write-Host "  [STEP 6/6] Downloading COLMAP + ffmpeg  (~850 MB, 1-2 min)" -ForegroundColor Cyan
Write-Host "================================================" -ForegroundColor Cyan
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
$ColmapLib = "$ColmapDir\lib"
@"
@echo off
rem ColmapLib FIRST so DLL search finds boost/ceres/cudart/Qt before bin/.
set "PATH=$ColmapLib;$ToolsBin;$Venv\Scripts;%PATH%"
set "QT_PLUGIN_PATH=$ColmapLib\plugins;%QT_PLUGIN_PATH%"
"@ | Set-Content -Path $PathFile -Encoding ASCII

# ─────────────────────────────────────────────────────────────────────────────
# 9. Post-install patches needed for gsplat JIT compile on modern toolchains
# ─────────────────────────────────────────────────────────────────────────────
# 9a. python310.lib lives in the BASE Python install's libs/ dir, not in the
# venv. torch's cpp_extension link rule expects to find it via venv\Scripts\libs
# OR venv\libs. We junction both to the real libs/ so the link step succeeds.
Log "[POST] Linking python310.lib into venv"
$basePyPrefix = (& $VenvPy -c "import sys; print(sys.base_prefix)" 2>$null).Trim()
if ($basePyPrefix -and (Test-Path (Join-Path $basePyPrefix "libs\python310.lib"))) {
    $basePyLibs = Join-Path $basePyPrefix "libs"
    foreach ($jPath in (Join-Path $Venv "libs"), (Join-Path $Venv "Scripts\libs")) {
        if (Test-Path $jPath) {
            $item = Get-Item $jPath -EA SilentlyContinue
            if (-not ($item -and $item.LinkType -eq "Junction")) {
                Remove-Item -Recurse -Force $jPath -EA SilentlyContinue
            }
        }
        if (-not (Test-Path $jPath)) {
            try {
                New-Item -ItemType Junction -Path $jPath -Target $basePyLibs -EA Stop | Out-Null
                Log "  junction $jPath -> $basePyLibs"
            } catch {
                Log "  WARN: failed to create junction $jPath -- $($_.ToString())"
            }
        }
    }
} else {
    Log "  WARN: base_prefix python310.lib not found, gsplat link will fail"
}

# 9b. Patch gsplat's _backend.py to pass `/Zc:preprocessor` and the
# CCCL_IGNORE macro to cl.exe. CUDA 13.x CCCL headers refuse to compile
# without the conforming preprocessor. We also strip the old `-ccbin`
# injection because nvcc 13.x finds cl.exe on PATH and a `-ccbin` pointing
# at an NTFS junction breaks cudafe++'s relative path navigation.
$bp = Join-Path $Venv "Lib\site-packages\gsplat\cuda\_backend.py"
if (Test-Path $bp) {
    Log "[POST] Patching gsplat _backend.py for CUDA 13.x compatibility"
    $content = [System.IO.File]::ReadAllText($bp, [System.Text.UTF8Encoding]::new($false))

    # Add /Zc:preprocessor + CCCL macro to extra_cuda_cflags (both branches)
    $old1 = 'extra_cuda_cflags = ["-O3", "--use_fast_math"]'
    $new1 = 'extra_cuda_cflags = ["-O3", "--use_fast_math", "-Xcompiler", "/Zc:preprocessor", "-DCCCL_IGNORE_MSVC_TRADITIONAL_PREPROCESSOR_WARNING"]'
    if ($content.Contains($old1) -and -not $content.Contains('/Zc:preprocessor')) {
        $content = $content.Replace($old1, $new1)
        $old2 = 'extra_cuda_cflags = ["-O3"]'
        $new2 = 'extra_cuda_cflags = ["-O3", "-Xcompiler", "/Zc:preprocessor", "-DCCCL_IGNORE_MSVC_TRADITIONAL_PREPROCESSOR_WARNING"]'
        if ($content.Contains($old2)) { $content = $content.Replace($old2, $new2) }
        [System.IO.File]::WriteAllText($bp, $content, [System.Text.UTF8Encoding]::new($false))
        Log "  patched extra_cuda_cflags with /Zc:preprocessor"
    } else {
        Log "  already patched or marker missing -- skipping"
    }
}

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

} catch {
    # Any error during install lands here -- print a prominent banner and
    # PAUSE so the user can read the error before the window closes.
    Write-Host ""
    Write-Host "================================================" -ForegroundColor Red
    Write-Host "                                                " -ForegroundColor Red
    Write-Host "             ERROR DURING INSTALL               " -ForegroundColor Red
    Write-Host "                                                " -ForegroundColor Red
    Write-Host "================================================" -ForegroundColor Red
    Write-Host ""
    Write-Host "  $_" -ForegroundColor Red
    Write-Host ""
    Write-Host "  Log:  $LogPath" -ForegroundColor Gray
    Write-Host ""
    Log "[FATAL] $_"
    Read-Host "Press Enter to close this window"
    exit 1
}
