@echo off
title OneClick SPLAT
cd /d "%~dp0"
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\launch.ps1" -AppDir "%~dp0."
pause
