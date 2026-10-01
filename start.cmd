@echo off
if exist "%~dp0Garden_Tools_LensTracker.exe" (
    start "" "%~dp0Garden_Tools_LensTracker.exe"
    exit /b
)
if exist "%~dp0dist\LensTracker\LensTracker.exe" (
    start "" "%~dp0dist\LensTracker\LensTracker.exe"
    exit /b
)
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0start.ps1"
if errorlevel 1 pause
