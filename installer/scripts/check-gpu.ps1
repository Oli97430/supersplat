# OneClick SPLAT — GPU prerequisite check
# Logs a warning if no NVIDIA GPU is found, but doesn't block install
# (user might still want to install for editor-only use).

$ErrorActionPreference = "SilentlyContinue"

try {
    $nvidiaSmi = Get-Command nvidia-smi -ErrorAction Stop
    $output = & nvidia-smi --query-gpu=name,memory.total,driver_version --format=csv,noheader,nounits 2>$null
    if ($output) {
        Write-Host "GPU detected: $output" -ForegroundColor Green
        exit 0
    }
} catch { }

# Fallback: WMI
$gpu = Get-CimInstance Win32_VideoController | Where-Object { $_.Name -match "NVIDIA" } | Select-Object -First 1
if ($gpu) {
    Write-Host "GPU detected (WMI): $($gpu.Name)" -ForegroundColor Yellow
    exit 0
}

Write-Host "WARNING: No NVIDIA GPU detected. Training will not work." -ForegroundColor Yellow
Write-Host "         OneClick SPLAT will still install for editor-only use." -ForegroundColor Yellow
exit 0  # don't block install
