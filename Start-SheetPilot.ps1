[CmdletBinding()]
param(
    [switch]$InstallPythonIfMissing
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

$ProjectRoot = (Resolve-Path -LiteralPath $PSScriptRoot).Path
$Python = Join-Path $ProjectRoot '.venv\Scripts\python.exe'
$EnvironmentReady = $false
if (Test-Path -LiteralPath $Python -PathType Leaf) {
    try {
        & $Python -c (
            'import struct, sys; ' +
            "assert sys.version_info[:2] == (3, 12) and sys.platform == 'win32'; " +
            'assert struct.calcsize(chr(80)) * 8 == 64; ' +
            'import PySide6, polars, pydantic, sheetpilot'
        ) 2>$null
        $EnvironmentReady = $LASTEXITCODE -eq 0
    }
    catch {
        $EnvironmentReady = $false
    }
}
if (-not $EnvironmentReady) {
    Write-Output 'Preparing or repairing the local SheetPilot environment...'
    & (Join-Path $ProjectRoot 'scripts\setup.ps1') `
        -InstallPythonIfMissing:$InstallPythonIfMissing
}
if (-not (Test-Path -LiteralPath $Python -PathType Leaf)) {
    throw 'SheetPilot setup did not create the expected Python environment.'
}

Push-Location $ProjectRoot
try {
    & $Python -m sheetpilot.app.main
    if ($LASTEXITCODE -ne 0) { throw "SheetPilot exited with code $LASTEXITCODE." }
}
finally {
    Pop-Location
}
