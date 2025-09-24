@echo off
setlocal

REM Launcher for MedChar on Windows
REM - Ensures PowerShell policy and runs setup/run script

set SCRIPT_DIR=%~dp0
powershell -NoProfile -ExecutionPolicy Bypass -NoExit -File "%SCRIPT_DIR%run_medchar.ps1"

endlocal
