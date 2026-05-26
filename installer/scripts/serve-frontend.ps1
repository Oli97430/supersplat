# OneClick SPLAT -- minimal static file server (no external dependencies).
# Serves the prebuilt frontend bundle on http://127.0.0.1:3000.

param(
    [Parameter(Mandatory = $true)]
    [string]$FrontendDir,
    [int]$Port = 3000
)

$ErrorActionPreference = "Continue"

if (-not (Test-Path $FrontendDir -PathType Container)) {
    Write-Error "Frontend dir not found: $FrontendDir"
    exit 1
}

$mimeMap = @{
    '.html'  = 'text/html; charset=utf-8'
    '.htm'   = 'text/html; charset=utf-8'
    '.js'    = 'application/javascript; charset=utf-8'
    '.mjs'   = 'application/javascript; charset=utf-8'
    '.css'   = 'text/css; charset=utf-8'
    '.json'  = 'application/json; charset=utf-8'
    '.svg'   = 'image/svg+xml'
    '.png'   = 'image/png'
    '.jpg'   = 'image/jpeg'
    '.jpeg'  = 'image/jpeg'
    '.gif'   = 'image/gif'
    '.webp'  = 'image/webp'
    '.ico'   = 'image/x-icon'
    '.wasm'  = 'application/wasm'
    '.woff'  = 'font/woff'
    '.woff2' = 'font/woff2'
    '.ttf'   = 'font/ttf'
    '.map'   = 'application/json'
}

function Get-Mime([string]$Path) {
    $ext = [IO.Path]::GetExtension($Path).ToLowerInvariant()
    if ($mimeMap.ContainsKey($ext)) { return $mimeMap[$ext] }
    return 'application/octet-stream'
}

$listener = [System.Net.HttpListener]::new()
$listener.Prefixes.Add("http://127.0.0.1:$Port/")
try {
    $listener.Start()
} catch {
    Write-Error "Failed to bind port $Port : $_"
    exit 1
}

Write-Host "Frontend server listening on http://127.0.0.1:$Port"

while ($listener.IsListening) {
    try {
        $ctx = $listener.GetContext()
        $rel = $ctx.Request.Url.AbsolutePath.TrimStart('/')
        if (-not $rel) { $rel = 'index.html' }
        # Normalise + reject path traversal
        $full = [IO.Path]::GetFullPath((Join-Path $FrontendDir $rel))
        $root = [IO.Path]::GetFullPath($FrontendDir)
        if (-not $full.StartsWith($root, [System.StringComparison]::OrdinalIgnoreCase)) {
            $ctx.Response.StatusCode = 403
            $ctx.Response.Close()
            continue
        }
        if (Test-Path $full -PathType Leaf) {
            $bytes = [IO.File]::ReadAllBytes($full)
            $ctx.Response.ContentType = (Get-Mime $full)
            $ctx.Response.ContentLength64 = $bytes.Length
            $ctx.Response.Headers.Add('Cache-Control', 'no-cache')
            $ctx.Response.OutputStream.Write($bytes, 0, $bytes.Length)
        } else {
            $ctx.Response.StatusCode = 404
            $msg = [Text.Encoding]::UTF8.GetBytes("Not Found: $rel")
            $ctx.Response.OutputStream.Write($msg, 0, $msg.Length)
        }
        $ctx.Response.Close()
    } catch {
        # Listener might have been stopped; swallow and exit on next loop iteration.
    }
}
