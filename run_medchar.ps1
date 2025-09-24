# PowerShell bootstrapper for MedChar (Windows)
# - Creates/activates venv
# - Sets execution policy for current user (RemoteSigned)
# - Installs dependencies
# - Starts the Flask app and opens the browser

param(
    [switch]$NoBrowser
)

$ErrorActionPreference = 'Stop'

function Write-Info($msg) { Write-Host "[INFO] $msg" -ForegroundColor Cyan }
function Write-Warn($msg) { Write-Host "[WARN] $msg" -ForegroundColor Yellow }
function Write-Err($msg)  { Write-Host "[ERROR] $msg" -ForegroundColor Red }

# Move to script directory (project root)
$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $scriptDir

Write-Info "Working directory: $scriptDir"

# Ensure ExecutionPolicy for current user allows running local scripts
try {
    $currentPolicy = (Get-ExecutionPolicy -Scope CurrentUser -ErrorAction SilentlyContinue)
    if (-not $currentPolicy -or $currentPolicy -eq 'Undefined' -or $currentPolicy -eq 'Restricted') {
        Write-Info "Setting ExecutionPolicy RemoteSigned for CurrentUser"
        Set-ExecutionPolicy -ExecutionPolicy RemoteSigned -Scope CurrentUser -Force -ErrorAction Stop
    }
} catch {
    Write-Warn "Could not set ExecutionPolicy (continuing): $($_.Exception.Message)"
}

# Find python
function Resolve-Python {
    try {
        $py = (Get-Command python -ErrorAction Stop).Source
        return $py
    } catch {
        try {
            $py = (Get-Command py -ErrorAction Stop).Source
            return "$py -3"
        } catch {
            throw "Python not found. Please install Python 3.x and try again."
        }
    }
}

$pythonCmd = Resolve-Python
Write-Info "Using Python: $pythonCmd"

# Create virtual environment if missing
if (-not (Test-Path -Path "$scriptDir/venv")) {
    Write-Info "Creating virtual environment..."
    & $pythonCmd -m venv venv
}

# Activate venv
$activatePath = Join-Path $scriptDir 'venv\Scripts\Activate.ps1'
if (-not (Test-Path $activatePath)) { throw "Activation script not found: $activatePath" }
Write-Info "Activating virtual environment"
. $activatePath

# Upgrade pip (best effort)
try {
    Write-Info "Upgrading pip"
    python -m pip install --upgrade pip
} catch { Write-Warn "Failed to upgrade pip: $($_.Exception.Message)" }

# Install dependencies: prefer requirements.txt, fallback to explicit list
$depsInstalled = $false
if (Test-Path -Path (Join-Path $scriptDir 'requirements.txt')) {
    Write-Info "Installing dependencies from requirements.txt"
    pip install -r requirements.txt
    if ($LASTEXITCODE -eq 0) {
        $depsInstalled = $true
    } else {
        Write-Warn "pip install -r requirements.txt exited with code $LASTEXITCODE; will try explicit install"
    }
}

if (-not $depsInstalled) {
    Write-Info "Installing core dependencies explicitly"
    pip install pandas flask docxtpl openpyxl python-docx
    if ($LASTEXITCODE -ne 0) {
        throw "Failed to install core dependencies (exit code $LASTEXITCODE)"
    }
}

# Determine app URL
$appHost = '127.0.0.1'
$appPort = 5000
$url  = "http://${appHost}:${appPort}/"

# Optionally open browser (after server is ready)
if (-not $NoBrowser) {
    Write-Info "Will open browser when server is ready at $url"
    Start-Job -Name 'OpenBrowserWhenReady' -ScriptBlock {
        param($targetUrl)
        for ($i = 0; $i -lt 60; $i++) {
            try {
                $resp = Invoke-WebRequest -Uri $targetUrl -UseBasicParsing -TimeoutSec 2 -ErrorAction Stop
                if ($resp.StatusCode -ge 200 -and $resp.StatusCode -lt 500) {
                    Start-Process $targetUrl | Out-Null
                    break
                }
            } catch { }
            Start-Sleep -Seconds 1
        }
    } -ArgumentList $url | Out-Null
}

# Start the app
Write-Info "Starting Flask app (Ctrl+C to stop)"
python app.py


