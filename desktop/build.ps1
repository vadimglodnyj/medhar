# Build Medhar desktop (onedir) and Install.exe (Inno Setup).
# Run from repository root:
#   .\desktop\build.ps1

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
if (-not (Test-Path (Join-Path $Root "app.py"))) {
    $Root = $PSScriptRoot
    if (-not (Test-Path (Join-Path $Root "app.py"))) {
        $Root = Resolve-Path (Join-Path $PSScriptRoot "..")
    }
}
Set-Location $Root
Write-Host "Root: $Root"

# Sync version: config.APP_VERSION → installer.iss + update_manifest.json
$ConfigPath = Join-Path $Root "config.py"
$AppVersion = $null
if (Test-Path $ConfigPath) {
    $m = Select-String -Path $ConfigPath -Pattern 'APP_VERSION\s*=\s*"([^"]+)"' | Select-Object -First 1
    if ($m) { $AppVersion = $m.Matches[0].Groups[1].Value }
}
if (-not $AppVersion) { $AppVersion = "0.0.0" }
Write-Host "App version: $AppVersion"

$IssPath = Join-Path $Root "desktop\installer.iss"
if (Test-Path $IssPath) {
    $iss = Get-Content $IssPath -Raw -Encoding UTF8
    $iss2 = [regex]::Replace($iss, '#define MyAppVersion "[^"]*"', "#define MyAppVersion `"$AppVersion`"")
    if ($iss2 -ne $iss) {
        Set-Content -Path $IssPath -Value $iss2 -Encoding UTF8 -NoNewline
        Write-Host "Updated installer.iss → $AppVersion"
    }
}
$ManifestPath = Join-Path $Root "desktop\update_manifest.json"
if (Test-Path $ManifestPath) {
    try {
        $man = Get-Content $ManifestPath -Raw -Encoding UTF8 | ConvertFrom-Json
        $man.version = $AppVersion
        if (-not $man.notes) { $man | Add-Member -NotePropertyName notes -NotePropertyValue "" -Force }
        if (-not ($man.PSObject.Properties.Name -contains "url")) {
            $man | Add-Member -NotePropertyName url -NotePropertyValue "" -Force
        }
        $man | ConvertTo-Json -Depth 4 | Set-Content -Path $ManifestPath -Encoding UTF8
        Write-Host "Updated update_manifest.json → $AppVersion (url keep: $($man.url))"
    } catch {
        Write-Warning "Could not update update_manifest.json: $_"
    }
}

$VenvPython = Join-Path $Root "venv\Scripts\python.exe"
if (-not (Test-Path $VenvPython)) {
    Write-Error "venv not found. Create it and install requirements.txt."
}

Write-Host "Installing / updating build deps..."
& $VenvPython -m pip install -q -r requirements.txt pyinstaller pywebview

# Team vault (encrypted secrets for PIN unlock)
$VaultPath = Join-Path $Root "data\team.vault"
$EnvPath = Join-Path $Root ".env"
$Pin = $env:MEDHAR_TEAM_PIN
if (-not $Pin -and (Test-Path (Join-Path $Root "desktop\.team_pin"))) {
    $Pin = (Get-Content (Join-Path $Root "desktop\.team_pin") -Raw).Trim()
}
if ($Pin -and (Test-Path $EnvPath)) {
    Write-Host "Building team.vault from .env ..."
    & $VenvPython (Join-Path $Root "scripts\build_team_vault.py") --env $EnvPath --pin $Pin --out $VaultPath
    if ($LASTEXITCODE -ne 0) {
        Write-Error "team.vault build failed"
    }
} elseif (-not (Test-Path $VaultPath)) {
    Write-Warning "data\team.vault missing. Team PIN unlock will be unavailable in Install.exe."
    Write-Warning "Create it: `$env:MEDHAR_TEAM_PIN='....'; .\desktop\build.ps1"
    Write-Warning "Or: python scripts\build_team_vault.py --env .env --pin YOUR_PIN"
}

$Dist = Join-Path $Root "dist"
$Build = Join-Path $Root "build"
if (Test-Path (Join-Path $Dist "Medhar")) {
    Remove-Item -Recurse -Force (Join-Path $Dist "Medhar")
}

Write-Host "PyInstaller (onedir)..."
& $VenvPython -m PyInstaller --noconfirm --clean (Join-Path $Root "desktop\medhar.spec")
if ($LASTEXITCODE -ne 0) {
    Write-Error "PyInstaller failed with code $LASTEXITCODE"
}

$MedharDir = Join-Path $Dist "Medhar"
if (-not (Test-Path (Join-Path $MedharDir "Medhar.exe"))) {
    Write-Error "dist\Medhar\Medhar.exe not found"
}

Write-Host "OK: $MedharDir\Medhar.exe"

# Inno Setup 6 / 7
$Iscc = @(
    "${env:ProgramFiles}\Inno Setup 7\ISCC.exe",
    "${env:ProgramFiles(x86)}\Inno Setup 7\ISCC.exe",
    "${env:LOCALAPPDATA}\Programs\Inno Setup 7\ISCC.exe",
    "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe",
    "${env:ProgramFiles}\Inno Setup 6\ISCC.exe",
    "${env:LOCALAPPDATA}\Programs\Inno Setup 6\ISCC.exe"
) | Where-Object { Test-Path $_ } | Select-Object -First 1

if (-not $Iscc) {
    Write-Warning "Inno Setup not found. Install version 6 or 7: https://jrsoftware.org/isinfo.php"
    Write-Warning "Application folder is ready: $MedharDir"
    Write-Warning "After installing Inno, run .\desktop\build.ps1 again"
    exit 0
}

Write-Host "Inno Setup: $Iscc"
& $Iscc (Join-Path $Root "desktop\installer.iss")
if ($LASTEXITCODE -ne 0) {
    Write-Error "ISCC failed with code $LASTEXITCODE"
}

$Installer = Join-Path $Dist "Install.exe"
if (Test-Path $Installer) {
    Write-Host "Build complete:"
    Write-Host "  $MedharDir\Medhar.exe"
    Write-Host "  $Installer"

    # Викласти оновлення в Dropbox App Folder: /releases/
    if ($env:MEDHAR_SKIP_UPDATE_PUBLISH -ne "1") {
        Write-Host "Publishing update to Dropbox /releases/ ..."
        $env:PYTHONPATH = $Root
        & $VenvPython -c "from utils import app_update; import sys; r=app_update.publish_release_to_dropbox(sys.argv[1], version=sys.argv[2], notes='Medhar '+sys.argv[2]); print(r); raise SystemExit(0 if r.get('ok') else 1)" $Installer $AppVersion
        if ($LASTEXITCODE -ne 0) {
            Write-Warning "Dropbox publish failed — Install.exe зібрано, але маніфест не викладено."
        } else {
            Write-Host "Dropbox /releases/Install.exe + update_manifest.json OK"
        }
    }
} else {
    Write-Warning "Install.exe was not created in dist"
}
