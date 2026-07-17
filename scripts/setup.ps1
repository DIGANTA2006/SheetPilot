[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'
$ProjectRoot = Split-Path -Parent $PSScriptRoot
$Python = Join-Path $ProjectRoot '.venv\Scripts\python.exe'
$LockFile = Join-Path $ProjectRoot 'requirements.lock'

if (-not (Test-Path -LiteralPath $Python -PathType Leaf)) {
    throw 'The repository .venv is required. Create it with Python 3.12 before setup.'
}

$Version = & $Python -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')"
if ($LASTEXITCODE -ne 0 -or $Version.Trim() -ne '3.12') {
    throw "SheetPilot requires Python 3.12; the repository .venv reported $Version."
}
if (-not (Test-Path -LiteralPath $LockFile -PathType Leaf)) {
    throw "The locked dependency file is required: $LockFile"
}

Push-Location $ProjectRoot
try {
    & $Python -m pip install --disable-pip-version-check --requirement $LockFile
    if ($LASTEXITCODE -ne 0) { throw 'Locked dependency installation failed.' }
    & $Python -m pip install --disable-pip-version-check --no-deps --editable .
    if ($LASTEXITCODE -ne 0) { throw 'Editable SheetPilot installation failed.' }
}
finally {
    Pop-Location
}
