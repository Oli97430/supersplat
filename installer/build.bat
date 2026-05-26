@echo off
:: ============================================================================
:: OneClick SPLAT — installer build script
:: Runs the frontend build, then invokes Inno Setup to package the installer.
:: ============================================================================
setlocal enabledelayedexpansion

echo.
echo === OneClick SPLAT installer build ===
echo.

:: Step 1 — Build the frontend
echo [1/3] Building frontend (npm run build)...
pushd ..
call npm run build
if errorlevel 1 (
    echo ERROR: frontend build failed
    popd
    exit /b 1
)
popd

:: Step 2 — Verify Inno Setup is installed
echo.
echo [2/3] Locating Inno Setup compiler...
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
    echo ERROR: Inno Setup is not installed.
    echo Download it from: https://jrsoftware.org/isdl.php
    exit /b 1
)
echo       Found: %ISCC%

:: Step 3 — Compile installer
echo.
echo [3/3] Compiling installer...
"%ISCC%" setup.iss
if errorlevel 1 (
    echo ERROR: Inno Setup compilation failed
    exit /b 1
)

echo.
echo === DONE ===
echo.
echo Installer written to: dist\OneClickSPLAT-Setup-*.exe
echo.
pause
