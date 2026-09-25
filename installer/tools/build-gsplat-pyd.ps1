# ============================================================================
# OneClick SPLAT -- build the prebuilt gsplat CUDA extension (.pyd)
# The installer ships this so users never need MSVC / the CUDA Toolkit.
#
# Needs (build machine only): VS 2022 Build Tools (C++), the CUDA Toolkit
# matching the venv's torch (12.8 for torch+cu128), and a venv with torch +
# gsplat 1.0.0 installed.
#
#   build-gsplat-pyd.ps1 -Venv F:\bw2\venv -CudaHome "...\CUDA\v12.8" `
#       -ArchList "7.5;8.6;8.9;12.0+PTX" `
#       -Out ..\dist\gsplat_cuda-py310-torch271-cu128-multiarch.pyd
# ============================================================================

param(
    [Parameter(Mandatory = $true)][string]$Venv,
    [Parameter(Mandatory = $true)][string]$CudaHome,
    [Parameter(Mandatory = $true)][string]$ArchList,
    [Parameter(Mandatory = $true)][string]$Out
)

$ErrorActionPreference = "Stop"

$vswhere = "${env:ProgramFiles(x86)}\Microsoft Visual Studio\Installer\vswhere.exe"
$vsPath  = & $vswhere -latest -products * -requires Microsoft.VisualStudio.Component.VC.Tools.x86.x64 -property installationPath
if (-not $vsPath) { throw "VS Build Tools with the C++ workload not found" }
$vcvars = Join-Path $vsPath "VC\Auxiliary\Build\vcvars64.bat"

# Import the vcvars64 environment into this process. vcvars itself calls
# vswhere.exe, so its folder must be on PATH; its stderr chatter is harmless.
$env:PATH = "$(Split-Path $vswhere -Parent);$env:PATH"
$ErrorActionPreference = "Continue"
cmd /c "`"$vcvars`" >nul 2>&1 && set" | ForEach-Object {
    if ($_ -match '^([^=]+)=(.*)$') { Set-Item -Path "env:$($Matches[1])" -Value $Matches[2] }
}
$ErrorActionPreference = "Stop"
if (-not (Get-Command cl.exe -ErrorAction SilentlyContinue)) { throw "vcvars64 did not put cl.exe on PATH" }

if (-not (Test-Path "$CudaHome\bin\nvcc.exe")) { throw "nvcc not found under $CudaHome" }
$env:CUDA_HOME = $CudaHome
$env:CUDA_PATH = $CudaHome
$env:PATH      = "$CudaHome\bin;$env:PATH"
$env:TORCH_CUDA_ARCH_LIST = $ArchList
$env:DISTUTILS_USE_SDK = "1"

$buildDir = Join-Path $env:TEMP "ocs-gsplat-build"
if (Test-Path $buildDir) { Remove-Item -Recurse -Force $buildDir }

# nvcc / ninja log to stderr: don't let PowerShell turn that into a throw.
$ErrorActionPreference = "Continue"
& (Join-Path $Venv "Scripts\python.exe") (Join-Path $PSScriptRoot "build_gsplat_pyd.py") $buildDir
$rc = $LASTEXITCODE
$ErrorActionPreference = "Stop"
if ($rc -ne 0) { throw "gsplat build failed (exit $rc)" }

$pyd = Join-Path $buildDir "gsplat_cuda.pyd"
if (-not (Test-Path $pyd)) { throw "build finished but $pyd is missing" }
New-Item -ItemType Directory -Path (Split-Path $Out -Parent) -Force | Out-Null
Copy-Item $pyd $Out -Force
Write-Host "  prebuilt written to $Out ($([math]::Round((Get-Item $Out).Length/1MB,1)) MB)"
