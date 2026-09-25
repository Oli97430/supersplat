# ============================================================================
# OneClick SPLAT -- fetch the private Python runtime bundled in the installer
# Downloads the official python.org NuGet package (full CPython 3.10.11:
# venv, ensurepip, libs\python310.lib, include\) and extracts its tools\ dir.
# Relocatable, no registry entries, no PATH changes -- lives in {app}\python.
#
#   fetch-python.ps1 -Dest <dir>     (build.bat: installer\python)
# ============================================================================

param([Parameter(Mandatory = $true)][string]$Dest)

$ErrorActionPreference = "Stop"
$ProgressPreference    = "SilentlyContinue"

$PyVersion = "3.10.11"
$PyUrl     = "https://www.nuget.org/api/v2/package/python/$PyVersion"
$PySha256  = "7C6F99B160A36A7E09492DFCFF2B0A3A60BB5229CA44CDCC3ECB32871A6144D0"

$exe = Join-Path $Dest "python.exe"
if (Test-Path $exe) {
    $v = (& $exe -c "import platform; print(platform.python_version())").Trim()
    if ($v -eq $PyVersion) { Write-Host "  Python $PyVersion already present in $Dest"; exit 0 }
    Write-Host "  $Dest holds Python $v -- replacing"
}

$tmp = Join-Path $env:TEMP "ocs-python-$PyVersion"
$zip = "$tmp.zip"
Write-Host "  Downloading $PyUrl"
Invoke-WebRequest -Uri $PyUrl -OutFile $zip -UseBasicParsing -TimeoutSec 600
$hash = (Get-FileHash $zip -Algorithm SHA256).Hash
if ($hash -ne $PySha256) { throw "python nupkg SHA256 mismatch: got $hash, expected $PySha256" }

if (Test-Path $tmp)  { Remove-Item -Recurse -Force $tmp }
Expand-Archive -Path $zip -DestinationPath $tmp -Force
if (Test-Path $Dest) { Remove-Item -Recurse -Force $Dest }
New-Item -ItemType Directory -Path (Split-Path $Dest -Parent) -Force | Out-Null
Move-Item (Join-Path $tmp "tools") $Dest
Remove-Item -Recurse -Force $tmp, $zip -EA SilentlyContinue

$v = (& $exe -c "import platform; print(platform.python_version())").Trim()
if ($v -ne $PyVersion) { throw "extracted Python reports version '$v'" }
Write-Host "  Python $PyVersion ready in $Dest"
