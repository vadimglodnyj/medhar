@echo off
setlocal

REM Launcher for MedChar on Windows
REM - Ensures PowerShell policy and runs setup/run script

REM Ensure Poppler (for pdf2image) is installed and on PATH
where pdfinfo >nul 2>nul
if %ERRORLEVEL% NEQ 0 (
  echo [INFO] Poppler not found. Trying to install...
  where winget >nul 2>nul
  if %ERRORLEVEL% EQU 0 (
    winget install --id=org.poppler.Poppler -e --source winget --accept-source-agreements --accept-package-agreements
  ) else (
    where choco >nul 2>nul
    if %ERRORLEVEL% EQU 0 (
      choco install poppler -y
    ) else (
      echo [WARN] winget/choco not found. Please install Poppler manually and ensure pdfinfo.exe is on PATH.
    )
  )
)

REM Try to locate Poppler bin and add to PATH for this session
set "POPPLER_BIN="
if exist "C:\\Program Files\\poppler\\bin\\pdfinfo.exe" set "POPPLER_BIN=C:\\Program Files\\poppler\\bin"
if exist "C:\\poppler\\bin\\pdfinfo.exe" set "POPPLER_BIN=C:\\poppler\\bin"
if defined POPPLER_BIN (
  echo [INFO] Using Poppler at %POPPLER_BIN%
  set "PATH=%PATH%;%POPPLER_BIN%"
) else (
  where pdfinfo >nul 2>nul
  if %ERRORLEVEL% NEQ 0 echo [WARN] Poppler bin not found. OCR for PDFs may fail.
)

set SCRIPT_DIR=%~dp0
powershell -NoProfile -ExecutionPolicy Bypass -NoExit -File "%SCRIPT_DIR%run_medchar.ps1"

endlocal
