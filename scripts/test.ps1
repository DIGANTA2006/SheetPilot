[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'
$ProjectRoot = Split-Path -Parent $PSScriptRoot
$Python = Join-Path $ProjectRoot '.venv\Scripts\python.exe'

if (-not (Test-Path -LiteralPath $Python -PathType Leaf)) {
    throw 'The repository .venv is required to run the quality gate.'
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
