# Run Medhar from source code - no rebuild, no reinstall.
#
#   .\desktop\dev.ps1            browser + auto-restart on code changes
#   .\desktop\dev.ps1 -Window    same, but inside the app window
#
# Uses the same settings and data as the installed app
# (%LOCALAPPDATA%\Medhar\.env), but a separate port, so the installed
# Medhar can keep running at the same time.

param(
    [switch]$Window
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
if (-not (Test-Path (Join-Path $Root "app.py"))) {
    $Root = Resolve-Path (Join-Path $PSScriptRoot "..")
}
Set-Location $Root

$VenvPython = Join-Path $Root "venv\Scripts\python.exe"
if (-not (Test-Path $VenvPython)) {
    Write-Error "venv not found at $VenvPython. Create it and install requirements.txt."
}

$env:MEDHAR_DESKTOP = "1"
$env:MEDHAR_DEV = "1"
if ($Window) {
    $env:MEDHAR_DEV_WINDOW = "1"
} else {
    Remove-Item Env:\MEDHAR_DEV_WINDOW -ErrorAction SilentlyContinue
}

Write-Host "Medhar DEV - http://127.0.0.1:17655/"
if ($Window) {
    Write-Host "App window. Restart the script after editing .py files."
} else {
    Write-Host "Browser. Editing .py restarts the server; refresh the page after template changes."
}
Write-Host "Ctrl+C to stop."

& $VenvPython -m desktop.run_desktop
exit $LASTEXITCODE
