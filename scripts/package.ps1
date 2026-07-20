[CmdletBinding()]
param(
    [switch]$SkipSetup,
    [switch]$SkipChecks,
    [switch]$InstallPythonIfMissing,
    [switch]$AllowDirty
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
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
$ReleaseArchive = Join-Path $ReleaseRoot "SheetPilot-$Version-win64.zip"
$ReleaseArchiveHash = "$ReleaseArchive.sha256"

$GitDirectory = Join-Path $ProjectRoot '.git'
$Git = Get-Command 'git.exe' -CommandType Application -ErrorAction SilentlyContinue |
    Select-Object -First 1
if ((Test-Path -LiteralPath $GitDirectory) -and -not $AllowDirty) {
    if ($null -eq $Git) {
        throw 'Git is required to prove the release source tree is clean.'
    }
    $GitStatus = @(& $Git.Path -C $ProjectRoot status --porcelain --untracked-files=all)
    if ($LASTEXITCODE -ne 0) { throw 'Git could not inspect the release source tree.' }
    if ($GitStatus.Count -gt 0) {
        throw 'Refusing to package a dirty source tree. Commit the reviewed release first.'
    }
}

& (Join-Path $PSScriptRoot 'build.ps1') `
    -SkipSetup:$SkipSetup `
    -SkipChecks:$SkipChecks `
    -InstallPythonIfMissing:$InstallPythonIfMissing
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

$SelfTestData = Join-Path ([System.IO.Path]::GetTempPath()) (
    'SheetPilotFrozenTest-' + [System.Guid]::NewGuid().ToString('N')
)
$PreviousDataDirectory = $env:SHEETPILOT_DATA_DIR
try {
    New-Item -ItemType Directory -Path $SelfTestData -Force | Out-Null
    $env:SHEETPILOT_DATA_DIR = $SelfTestData
    Invoke-FrozenSelfTest -Argument '--smoke-test' -Label 'Frozen startup smoke test'
    Invoke-FrozenSelfTest -Argument '--workflow-self-test' -Label 'Frozen normal workflow self-test'
    Invoke-FrozenSelfTest `
        -Argument '--invalid-workflow-self-test' `
        -Label 'Frozen invalid workflow rejection self-test'
}
finally {
    if ($null -eq $PreviousDataDirectory) {
        Remove-Item Env:SHEETPILOT_DATA_DIR -ErrorAction SilentlyContinue
    }
    else {
        $env:SHEETPILOT_DATA_DIR = $PreviousDataDirectory
    }
    if (Test-Path -LiteralPath $SelfTestData -PathType Container) {
        Remove-Item -LiteralPath $SelfTestData -Recurse -Force
    }
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

Copy-Item -LiteralPath (Join-Path $ProjectRoot 'LICENSE') -Destination $CanonicalRelease
Copy-Item -LiteralPath (Join-Path $ProjectRoot 'README.md') -Destination $CanonicalRelease
$FirstRead = @(
    "SheetPilot $Version for Windows 10/11 x64",
    '',
    'Start SheetPilot.exe from this folder. Python is not required.',
    'Keep the complete folder together; do not copy the executable by itself.',
    'Verify every installed file with SHA256SUMS.txt before first use.',
    '',
    'Release classification: beta. Complete clean-machine verification is still required.'
)
[System.IO.File]::WriteAllLines(
    (Join-Path $CanonicalRelease 'README-FIRST.txt'),
    $FirstRead,
    [System.Text.UTF8Encoding]::new($false)
)
$GitCommit = $null
if ($null -ne $Git) {
    $GitCommitOutput = & $Git.Path -C $ProjectRoot rev-parse HEAD 2>$null
    if ($LASTEXITCODE -eq 0) { $GitCommit = $GitCommitOutput.Trim() }
}
$BuildManifest = [ordered]@{
    product = 'SheetPilot'
    version = $Version
    platform = 'Windows x64'
    packaging = 'PyInstaller one-folder'
    created_at_utc = [DateTime]::UtcNow.ToString('o')
    git_commit = $GitCommit
}
[System.IO.File]::WriteAllText(
    (Join-Path $CanonicalRelease 'release-manifest.json'),
    (($BuildManifest | ConvertTo-Json -Depth 3) + [Environment]::NewLine),
    [System.Text.UTF8Encoding]::new($false)
)

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

foreach ($GeneratedFile in @($ReleaseArchive, $ReleaseArchiveHash)) {
    if (Test-Path -LiteralPath $GeneratedFile -PathType Leaf) {
        Remove-Item -LiteralPath $GeneratedFile -Force
    }
}
Add-Type -AssemblyName System.IO.Compression
Add-Type -AssemblyName System.IO.Compression.FileSystem
[System.IO.Compression.ZipFile]::CreateFromDirectory(
    $CanonicalRelease,
    $ReleaseArchive,
    [System.IO.Compression.CompressionLevel]::Optimal,
    $true
)
$ArchiveDigest = (Get-FileHash -LiteralPath $ReleaseArchive -Algorithm SHA256).Hash.ToLowerInvariant()
[System.IO.File]::WriteAllText(
    $ReleaseArchiveHash,
    "$ArchiveDigest  $([System.IO.Path]::GetFileName($ReleaseArchive))$([Environment]::NewLine)",
    [System.Text.UTF8Encoding]::new($false)
)

Write-Output "Frozen smoke test: passed"
Write-Output "Frozen normal workflow self-test: passed"
Write-Output "Frozen invalid workflow rejection self-test: passed"
Write-Output "Release folder: $CanonicalRelease"
Write-Output "Executable: $ReleaseExecutable"
Write-Output "Checksums: $ChecksumPath"
Write-Output "Release archive: $ReleaseArchive"
Write-Output "Archive checksum: $ReleaseArchiveHash"
