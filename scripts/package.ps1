[CmdletBinding()]
param(
    [switch]$SkipSetup,
    [switch]$SkipChecks
)

$ErrorActionPreference = 'Stop'
$ProjectRoot = (Resolve-Path -LiteralPath (Split-Path -Parent $PSScriptRoot)).Path
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
$Smoke = Start-Process -FilePath $Executable -ArgumentList '--smoke-test' -PassThru -WindowStyle Hidden
try {
    if (-not $Smoke.WaitForExit(30000)) {
        $Smoke.Kill()
        $Smoke.WaitForExit()
        throw 'Frozen startup smoke test exceeded the 30-second timeout.'
    }
    $SmokeExitCode = $Smoke.ExitCode
}
finally {
    $Smoke.Dispose()
}
if ($SmokeExitCode -ne 0) {
    throw "Frozen startup smoke test failed with exit code $SmokeExitCode."
}

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

Write-Output "Frozen smoke test: passed"
Write-Output "Release folder: $CanonicalRelease"
Write-Output "Executable: $(Join-Path $CanonicalRelease 'SheetPilot.exe')"
Write-Output "Checksums: $ChecksumPath"
