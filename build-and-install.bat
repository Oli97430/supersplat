@echo off
:: ============================================================================
::  OneClick SPLAT — build + install (local dev shortcut)
::
::  Double-click from the repo root to:
::    1. Build the frontend (npm run build)
::    2. Compile the installer (Inno Setup)
::    3. Launch the freshly built .exe (UAC prompt comes from the installer)
::
::  Requirements:
::    - Node.js + npm on PATH
::    - Inno Setup 6 installed (https://jrsoftware.org/isdl.php)
:: ============================================================================
setlocal enabledelayedexpansion
pushd "%~dp0"

echo.
echo ================================================
echo   OneClick SPLAT -- build + install
echo ================================================
echo.

:: --- Step 1/4 : frontend build ---------------------------------------------
echo [1/4] Building frontend (npm run build)...
call npm run build
if errorlevel 1 (
    echo.
    echo ERROR: frontend build failed.
    popd
    pause
    exit /b 1
)

:: --- Step 2/4 : locate Inno Setup compiler ---------------------------------
echo.
echo [2/4] Locating Inno Setup compiler...
set "ISCC="
for %%P in (
    "%ProgramFiles(x86)%\Inno Setup 6\ISCC.exe"
    "%ProgramFiles%\Inno Setup 6\ISCC.exe"
    "%ProgramFiles(x86)%\Inno Setup 5\ISCC.exe"
) do (
    if exist %%P set "ISCC=%%~P"
)
if not defined ISCC (
    echo.
    echo ERROR: Inno Setup not found.
    echo Download from: https://jrsoftware.org/isdl.php
    popd
    pause
    exit /b 1
)
echo       Found: !ISCC!

:: --- Step 3/4 : compile installer ------------------------------------------
echo.
echo [3/4] Compiling installer...
pushd installer
"!ISCC!" setup.iss
if errorlevel 1 (
    echo.
    echo ERROR: Inno Setup compilation failed.
    popd
    popd
    pause
    exit /b 1
)
popd

:: --- Step 4/4 : find newest .exe and launch --------------------------------
echo.
echo [4/4] Locating built installer...
set "LATEST="
for /f "delims=" %%F in ('dir /b /o-d "installer\dist\OneClickSPLAT-Setup-*.exe" 2^>nul') do (
    if not defined LATEST set "LATEST=installer\dist\%%F"
)

if not defined LATEST (
    echo.
    echo ERROR: no installer .exe found in installer\dist\
    popd
    pause
    exit /b 1
)

echo       Found: !LATEST!
echo.
echo ================================================
echo   Launching installer  --  UAC prompt incoming
echo ================================================
echo.

start "" "!LATEST!"

popd
endlocal
exit /b 0
