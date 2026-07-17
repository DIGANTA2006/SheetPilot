[CmdletBinding()]
param(
    [switch]$SkipSetup,
    [switch]$SkipChecks
)

$ErrorActionPreference = 'Stop'
$ProjectRoot = (Resolve-Path -LiteralPath (Split-Path -Parent $PSScriptRoot)).Path
$Python = Join-Path $ProjectRoot '.venv\Scripts\python.exe'
$VersionSource = Get-Content -LiteralPath (Join-Path $ProjectRoot 'sheetpilot\app\version.py') -Raw
$VersionMatch = [regex]::Match($VersionSource, '__version__\s*=\s*"(?<version>\d+\.\d+\.\d+)"')
if (-not $VersionMatch.Success) {
    throw 'Could not read the application version from sheetpilot\app\version.py.'
}
$Version = $VersionMatch.Groups['version'].Value
$BuiltFolder = Join-Path $ProjectRoot 'dist\SheetPilot'
$ReleaseRoot = Join-Path $ProjectRoot 'release'
$ReleaseFolder = Join-Path $ReleaseRoot "SheetPilot-$Version-win64"

& (Join-Path $PSScriptRoot 'build.ps1') -SkipSetup:$SkipSetup -SkipChecks:$SkipChecks
if ($LASTEXITCODE -ne 0) { throw 'Build failed.' }

$Executable = Join-Path $BuiltFolder 'SheetPilot.exe'
function Invoke-FrozenSelfTest {
    param(
        [Parameter(Mandatory = $true)][string]$Argument,
        [Parameter(Mandatory = $true)][string]$Label,
        [int]$TimeoutMilliseconds = 60000
    )
    $Process = Start-Process -FilePath $Executable -ArgumentList $Argument -PassThru -WindowStyle Hidden
    try {
        if (-not $Process.WaitForExit($TimeoutMilliseconds)) {
            $Process.Kill()
            $Process.WaitForExit()
            throw "$Label exceeded the $TimeoutMilliseconds millisecond timeout."
        }
        $ExitCode = $Process.ExitCode
    }
    finally {
        $Process.Dispose()
    }
    if ($ExitCode -ne 0) {
        throw "$Label failed with exit code $ExitCode."
    }
}

Invoke-FrozenSelfTest -Argument '--smoke-test' -Label 'Frozen startup smoke test'
Invoke-FrozenSelfTest -Argument '--workflow-self-test' -Label 'Frozen normal workflow self-test'
Invoke-FrozenSelfTest `
    -Argument '--invalid-workflow-self-test' `
    -Label 'Frozen invalid workflow rejection self-test'

New-Item -ItemType Directory -Path $ReleaseRoot -Force | Out-Null
$ResolvedReleaseRoot = (Resolve-Path -LiteralPath $ReleaseRoot).Path
$CanonicalRelease = [System.IO.Path]::GetFullPath($ReleaseFolder)
$ReleasePrefix = $ResolvedReleaseRoot + [System.IO.Path]::DirectorySeparatorChar
if (-not $CanonicalRelease.StartsWith($ReleasePrefix, [System.StringComparison]::OrdinalIgnoreCase)) {
    throw 'Release path escaped the project release directory.'
}
if (Test-Path -LiteralPath $CanonicalRelease) {
    Remove-Item -LiteralPath $CanonicalRelease -Recurse -Force
}
Copy-Item -LiteralPath $BuiltFolder -Destination $CanonicalRelease -Recurse

$ChecksumPath = Join-Path $CanonicalRelease 'SHA256SUMS.txt'
$ChecksumLines = Get-ChildItem -LiteralPath $CanonicalRelease -Recurse -File |
    Where-Object { $_.FullName -ne $ChecksumPath } |
    Sort-Object FullName |
    ForEach-Object {
        $Relative = $_.FullName.Substring($CanonicalRelease.Length + 1).Replace('\', '/')
        $Hash = (Get-FileHash -LiteralPath $_.FullName -Algorithm SHA256).Hash.ToLowerInvariant()
        "$Hash  $Relative"
    }
[System.IO.File]::WriteAllLines($ChecksumPath, $ChecksumLines, [System.Text.UTF8Encoding]::new($false))

$ReleaseExecutable = Join-Path $CanonicalRelease 'SheetPilot.exe'
$PreviousFrozenExecutable = $env:SHEETPILOT_FROZEN_EXE
try {
    $env:SHEETPILOT_FROZEN_EXE = $ReleaseExecutable
    & $Python -m pytest tests\packaging\test_frozen_smoke.py -q
    if ($LASTEXITCODE -ne 0) { throw 'Frozen release regression tests failed.' }
}
finally {
    if ($null -eq $PreviousFrozenExecutable) {
        Remove-Item Env:SHEETPILOT_FROZEN_EXE -ErrorAction SilentlyContinue
    }
    else {
        $env:SHEETPILOT_FROZEN_EXE = $PreviousFrozenExecutable
    }
}

Write-Output "Frozen smoke test: passed"
Write-Output "Frozen normal workflow self-test: passed"
Write-Output "Frozen invalid workflow rejection self-test: passed"
Write-Output "Release folder: $CanonicalRelease"
Write-Output "Executable: $ReleaseExecutable"
Write-Output "Checksums: $ChecksumPath"
