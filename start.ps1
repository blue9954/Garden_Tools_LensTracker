$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
$launcherExe = Join-Path $PSScriptRoot 'Garden_Tools_LensTracker.exe'
if (Test-Path -LiteralPath $launcherExe) {
    & $launcherExe
    exit $LASTEXITCODE
}
$desktopExe = Join-Path $PSScriptRoot 'dist\LensTracker\LensTracker.exe'
if (Test-Path -LiteralPath $desktopExe) {
    & $desktopExe
    exit $LASTEXITCODE
}
$pythonExe = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $pythonExe)) {
    python -m venv .venv
    if ($LASTEXITCODE -ne 0) { throw 'Python 3.11 or newer is required.' }
}
& $pythonExe -c "import importlib.util,sys; sys.exit(0 if all(importlib.util.find_spec(name) for name in ('flask','PIL','PySide6')) else 1)"
if ($LASTEXITCODE -ne 0) {
    & $pythonExe -m pip install -r requirements-desktop.txt --disable-pip-version-check
    if ($LASTEXITCODE -ne 0) { throw 'Could not install dependencies.' }
}
& $pythonExe desktop.py
