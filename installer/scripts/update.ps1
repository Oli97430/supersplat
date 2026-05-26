# OneClick SPLAT — in-place updater (pulls latest from GitHub)
# Replaces server/ and frontend/ from the latest GitHub release.

param([string]$AppDir = (Split-Path $PSScriptRoot -Parent))

$ErrorActionPreference = "Stop"

Write-Host "Checking for updates…"

try {
    $latest = Invoke-RestMethod -Uri "https://api.github.com/repos/Oli97430/supersplat/releases/latest"
    $tag    = $latest.tag_name
    Write-Host "Latest release: $tag"
} catch {
    Write-Host "Failed to reach GitHub: $_" -ForegroundColor Red
    exit 1
}

$current = "v2.27.3-train"
if (Test-Path "$AppDir\VERSION") { $current = (Get-Content "$AppDir\VERSION" -Raw).Trim() }

if ($current -eq $tag) {
    Write-Host "Already on $current — nothing to update." -ForegroundColor Green
    exit 0
}

Write-Host "Update from $current → $tag ?"
$ans = Read-Host "Continue [y/N]"
if ($ans -ne "y") { exit 0 }

# Stop running backend
Get-Process python -EA SilentlyContinue | Where-Object CommandLine -match uvicorn | Stop-Process -Force -EA SilentlyContinue

# Download sources tarball
$src = Join-Path $env:TEMP "ocs-update.zip"
Invoke-WebRequest -Uri "https://github.com/Oli97430/supersplat/archive/refs/tags/$tag.zip" -OutFile $src -UseBasicParsing
$tmp = Join-Path $env:TEMP "ocs-update"
if (Test-Path $tmp) { Remove-Item -Recurse -Force $tmp }
Expand-Archive -Path $src -DestinationPath $tmp -Force
$root = Get-ChildItem $tmp -Directory | Select-Object -First 1

# Replace server/ and frontend/
Copy-Item "$($root.FullName)\server\main.py"     "$AppDir\server\main.py" -Force
Copy-Item "$($root.FullName)\server\pipeline.py" "$AppDir\server\pipeline.py" -Force

# Frontend needs to be built — skip if no node, or download a prebuilt artifact
# (For now, point user to manual update of frontend if they want it.)

Set-Content -Path "$AppDir\VERSION" -Value $tag

Write-Host "Updated to $tag." -ForegroundColor Green
Write-Host "Note: frontend dist/ was NOT updated automatically. To refresh:" -ForegroundColor Yellow
Write-Host "      cd $AppDir; .\scripts\update-frontend.ps1" -ForegroundColor Yellow

Remove-Item $src -Force -EA SilentlyContinue
Remove-Item $tmp -Recurse -Force -EA SilentlyContinue
