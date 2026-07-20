[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
$ProjectRoot = Split-Path -Parent $PSScriptRoot
$Python = Join-Path $ProjectRoot '.venv\Scripts\python.exe'

if (-not (Test-Path -LiteralPath $Python -PathType Leaf)) {
    throw 'The repository .venv is required to run the quality gate.'
}
try {
    & $Python -c (
        'import struct, sys; ' +
        "assert sys.version_info[:2] == (3, 12) and sys.platform == 'win32'; " +
        'assert struct.calcsize(chr(80)) * 8 == 64'
    ) 2>$null
}
catch {
    throw 'The project .venv is unusable; run scripts\setup.ps1 to repair it.'
}
if ($LASTEXITCODE -ne 0) {
    throw 'The project .venv is unusable; run scripts\setup.ps1 to repair it.'
}

Push-Location $ProjectRoot
try {
    & $Python -m ruff format --check .
    if ($LASTEXITCODE -ne 0) { throw 'Ruff formatting check failed.' }
    & $Python -m ruff check .
    if ($LASTEXITCODE -ne 0) { throw 'Ruff lint failed.' }
    & $Python -m mypy sheetpilot
    if ($LASTEXITCODE -ne 0) { throw 'Static type checking failed.' }
    & $Python -m pytest
    if ($LASTEXITCODE -ne 0) { throw 'Tests failed.' }
    & $Python -m bandit -c pyproject.toml -r sheetpilot
    if ($LASTEXITCODE -ne 0) { throw 'Security scanning failed.' }
}
finally {
    Pop-Location
}
