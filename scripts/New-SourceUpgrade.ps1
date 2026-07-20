[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [ValidatePattern('^\d+\.\d+\.\d+$')]
    [string]$Version,
    [string]$OutputPath,
    [string]$PreviousUpgradePath,
    [string]$BaselineRevision = 'HEAD'
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

$ProjectRoot = (Resolve-Path -LiteralPath (Split-Path -Parent $PSScriptRoot)).Path
if ([string]::IsNullOrWhiteSpace($OutputPath)) {
    $OutputPath = Join-Path $ProjectRoot "Upgrade-SheetPilot-$Version.ps1"
}
if ([string]::IsNullOrWhiteSpace($PreviousUpgradePath)) {
    $PreviousUpgradePath = Join-Path $ProjectRoot 'Upgrade-SheetPilot-0.1.2.ps1'
}
$OutputPath = [System.IO.Path]::GetFullPath($OutputPath)
$ProjectPrefix = $ProjectRoot.TrimEnd('\', '/') + [System.IO.Path]::DirectorySeparatorChar
if (-not $OutputPath.StartsWith($ProjectPrefix, [System.StringComparison]::OrdinalIgnoreCase)) {
    throw 'The generated upgrade script must stay inside the SheetPilot project.'
}

$Git = Get-Command 'git.exe' -CommandType Application -ErrorAction SilentlyContinue |
    Select-Object -First 1
if ($null -eq $Git) { throw 'Git is required to generate a cumulative source upgrade.' }

function Get-Sha256 {
    param([Parameter(Mandatory = $true)][string]$Path)

    return (Get-FileHash -LiteralPath $Path -Algorithm SHA256).Hash.ToLowerInvariant()
}

function Get-GitBlobSha256 {
    param([Parameter(Mandatory = $true)][string]$RelativePath)

    $Info = [System.Diagnostics.ProcessStartInfo]::new()
    $Info.FileName = $Git.Path
    $Info.Arguments = (
        '-C "' + $ProjectRoot.Replace('"', '\"') + '" cat-file blob "' +
        $ResolvedBaseline + ':' + $RelativePath.Replace('"', '\"') + '"'
    )
    $Info.UseShellExecute = $false
    $Info.CreateNoWindow = $true
    $Info.RedirectStandardOutput = $true
    $Info.RedirectStandardError = $true
    $Process = [System.Diagnostics.Process]::new()
    $Process.StartInfo = $Info
    try {
        [void]$Process.Start()
        $Hasher = [System.Security.Cryptography.SHA256]::Create()
        try {
            $Digest = $Hasher.ComputeHash($Process.StandardOutput.BaseStream)
        }
        finally {
            $Hasher.Dispose()
        }
        $ErrorText = $Process.StandardError.ReadToEnd()
        $Process.WaitForExit()
        if ($Process.ExitCode -ne 0) {
            if ($ErrorText -match 'does not exist|Not a valid object name|exists on disk, but not in') {
                return $null
            }
            throw "Git could not read the baseline for ${RelativePath}: $ErrorText"
        }
        return ([System.BitConverter]::ToString($Digest) -replace '-', '').ToLowerInvariant()
    }
    finally {
        $Process.Dispose()
    }
}

function Read-PreviousManifest {
    param([Parameter(Mandatory = $true)][string]$Path)

    $Result = @{}
    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) { return $Result }
    $Lines = Get-Content -LiteralPath $Path
    $Start = -1
    for ($Index = 0; $Index -lt $Lines.Count; $Index++) {
        if ($Lines[$Index] -eq '$ManifestJson = @''') {
            $Start = $Index + 1
            break
        }
    }
    if ($Start -lt 1) { throw 'The previous upgrade manifest start was not found.' }
    $End = -1
    for ($Index = $Start; $Index -lt $Lines.Count; $Index++) {
        if ($Lines[$Index] -eq '''@') {
            $End = $Index
            break
        }
    }
    if ($End -le $Start) { throw 'The previous upgrade manifest end was not found.' }
    $Json = $Lines[$Start..($End - 1)] -join [Environment]::NewLine
    foreach ($Entry in (ConvertFrom-Json -InputObject $Json)) {
        $Result[[string]$Entry.Path] = $Entry
    }
    return $Result
}

$ResolvedBaseline = & $Git.Path -C $ProjectRoot rev-parse --verify "$BaselineRevision^{commit}"
if ($LASTEXITCODE -ne 0 -or [string]::IsNullOrWhiteSpace($ResolvedBaseline)) {
    throw "The baseline revision is not a commit: $BaselineRevision"
}
$Tracked = @(
    & $Git.Path -C $ProjectRoot diff --name-only --diff-filter=ACMRT $ResolvedBaseline --
)
if ($LASTEXITCODE -ne 0) { throw 'Git could not list changed tracked files.' }
$Untracked = @(& $Git.Path -C $ProjectRoot ls-files --others --exclude-standard)
if ($LASTEXITCODE -ne 0) { throw 'Git could not list untracked files.' }
$RelativeOutput = $OutputPath.Substring($ProjectPrefix.Length).Replace('\', '/')
$Paths = @($Tracked + $Untracked) |
    ForEach-Object { ([string]$_).Replace('\', '/') } |
    Where-Object {
        $_ -and
        $_ -ne $RelativeOutput -and
        $_ -notlike 'Upgrade-SheetPilot-*.ps1' -and
        $_ -notmatch '(^|/)manual_beta_test(/|$)'
    } |
    Sort-Object -Unique
if ($Paths.Count -eq 0) { throw 'No changed files were found for the cumulative upgrade.' }

$PreviousManifest = Read-PreviousManifest -Path $PreviousUpgradePath
$Manifest = @()
foreach ($RelativePath in $Paths) {
    if (
        [System.IO.Path]::IsPathRooted($RelativePath) -or
        $RelativePath -match '(^|/)\.\.(/|$)' -or
        $RelativePath.IndexOf([char]0) -ge 0
    ) {
        throw "Unsafe payload path: $RelativePath"
    }
    $TargetPath = Join-Path $ProjectRoot (
        $RelativePath.Replace([char]47, [System.IO.Path]::DirectorySeparatorChar)
    )
    if (-not (Test-Path -LiteralPath $TargetPath -PathType Leaf)) {
        throw "Upgrade payload file is missing: $RelativePath"
    }
    $BaselineHash = Get-GitBlobSha256 -RelativePath $RelativePath
    $Allowed = [System.Collections.Generic.HashSet[string]]::new(
        [System.StringComparer]::OrdinalIgnoreCase
    )
    if ($null -ne $BaselineHash) { [void]$Allowed.Add($BaselineHash) }
    if ($PreviousManifest.ContainsKey($RelativePath)) {
        $Previous = $PreviousManifest[$RelativePath]
        foreach ($PropertyName in @('BaselineHash', 'ArchiveBaselineHash', 'TargetHash')) {
            $Property = $Previous.PSObject.Properties[$PropertyName]
            if ($null -ne $Property -and $null -ne $Property.Value) {
                [void]$Allowed.Add([string]$Property.Value)
            }
        }
    }
    $Manifest += [ordered]@{
        Path = $RelativePath
        BaselineHashes = @($Allowed | Sort-Object)
        AllowMissing = $null -eq $BaselineHash
        TargetHash = Get-Sha256 -Path $TargetPath
    }
}

$TemporaryRoot = Join-Path ([System.IO.Path]::GetTempPath()) (
    'SheetPilotUpgradeBuild-' + [System.Guid]::NewGuid().ToString('N')
)
$PayloadZip = Join-Path $TemporaryRoot 'payload.zip'
try {
    New-Item -ItemType Directory -Path $TemporaryRoot -Force | Out-Null
    Add-Type -AssemblyName System.IO.Compression
    Add-Type -AssemblyName System.IO.Compression.FileSystem
    $Stream = [System.IO.File]::Open(
        $PayloadZip,
        [System.IO.FileMode]::CreateNew,
        [System.IO.FileAccess]::ReadWrite,
        [System.IO.FileShare]::None
    )
    try {
        $Archive = [System.IO.Compression.ZipArchive]::new(
            $Stream,
            [System.IO.Compression.ZipArchiveMode]::Create,
            $true
        )
        try {
            foreach ($Entry in $Manifest) {
                $EntryName = [string]$Entry.Path
                $SourcePath = Join-Path $ProjectRoot (
                    $EntryName.Replace([char]47, [System.IO.Path]::DirectorySeparatorChar)
                )
                $ZipEntry = $Archive.CreateEntry(
                    $EntryName,
                    [System.IO.Compression.CompressionLevel]::Optimal
                )
                $Input = [System.IO.File]::OpenRead($SourcePath)
                $Output = $ZipEntry.Open()
                try { $Input.CopyTo($Output) }
                finally { $Output.Dispose(); $Input.Dispose() }
            }
        }
        finally {
            $Archive.Dispose()
        }
    }
    finally {
        $Stream.Dispose()
    }

    $PayloadBytes = [System.IO.File]::ReadAllBytes($PayloadZip)
    $PayloadHash = Get-Sha256 -Path $PayloadZip
    $Encoded = [System.Convert]::ToBase64String($PayloadBytes)
    $Wrapped = [System.Text.StringBuilder]::new()
    for ($Offset = 0; $Offset -lt $Encoded.Length; $Offset += 100) {
        $Length = [Math]::Min(100, $Encoded.Length - $Offset)
        [void]$Wrapped.AppendLine($Encoded.Substring($Offset, $Length))
    }
    $ManifestJson = $Manifest | ConvertTo-Json -Depth 5
    $Template = @'
[CmdletBinding()]
param(
    [string]$ProjectRoot,
    [switch]$Force,
    [switch]$SkipSetup,
    [switch]$SkipChecks,
    [switch]$InstallPythonIfMissing,
    [switch]$BuildRelease,
    [switch]$Launch
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

if ([string]::IsNullOrWhiteSpace($ProjectRoot)) { $ProjectRoot = $PSScriptRoot }
if ([System.Environment]::OSVersion.Platform -ne [System.PlatformID]::Win32NT) {
    throw 'This SheetPilot upgrade must be run on Windows.'
}

$ResolvedProjectRoot = (Resolve-Path -LiteralPath $ProjectRoot).Path
$ProjectRootItem = Get-Item -LiteralPath $ResolvedProjectRoot -Force
if (($ProjectRootItem.Attributes -band [System.IO.FileAttributes]::ReparsePoint) -ne 0) {
    throw 'Refusing to upgrade a project root stored through a reparse point.'
}
$RootPrefix = $ResolvedProjectRoot.TrimEnd('\', '/') + [System.IO.Path]::DirectorySeparatorChar

function Resolve-ProjectFile {
    param([Parameter(Mandatory = $true)][string]$RelativePath)

    if (
        [System.IO.Path]::IsPathRooted($RelativePath) -or
        $RelativePath -match '(^|/|\\)\.\.(/|\\|$)' -or
        $RelativePath.IndexOf([char]0) -ge 0
    ) { throw "Unsafe project-relative path: $RelativePath" }
    $NativePath = $RelativePath.Replace([char]47, [System.IO.Path]::DirectorySeparatorChar)
    $FullPath = [System.IO.Path]::GetFullPath((Join-Path $ResolvedProjectRoot $NativePath))
    if (-not $FullPath.StartsWith($RootPrefix, [System.StringComparison]::OrdinalIgnoreCase)) {
        throw "Project path escaped the selected root: $RelativePath"
    }
    $CurrentPath = $ResolvedProjectRoot
    foreach ($Segment in $NativePath.Split([System.IO.Path]::DirectorySeparatorChar)) {
        if (-not $Segment) { continue }
        $CurrentPath = Join-Path $CurrentPath $Segment
        if (-not (Test-Path -LiteralPath $CurrentPath)) { break }
        $Item = Get-Item -LiteralPath $CurrentPath -Force
        if (($Item.Attributes -band [System.IO.FileAttributes]::ReparsePoint) -ne 0) {
            throw "Refusing to use a project path that crosses a reparse point: $RelativePath"
        }
    }
    return $FullPath
}

foreach ($Marker in @('pyproject.toml', 'requirements.lock', 'sheetpilot/app/main.py')) {
    if (-not (Test-Path -LiteralPath (Resolve-ProjectFile $Marker) -PathType Leaf)) {
        throw "The selected folder is not an extracted SheetPilot source root: $ResolvedProjectRoot"
    }
}

$ManifestJson = @"
__MANIFEST__
"@
$Manifest = @(
    foreach ($ManifestEntry in (ConvertFrom-Json -InputObject $ManifestJson)) {
        $ManifestEntry
    }
)
if ($Manifest.Count -ne __FILE_COUNT__) {
    throw 'The embedded upgrade manifest has an unexpected file count.'
}

$PayloadBase64 = @"
__PAYLOAD__"@
$ExpectedPayloadHash = '__PAYLOAD_HASH__'

function Get-Sha256 {
    param([Parameter(Mandatory = $true)][string]$Path)

    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) { return $null }
    return (Get-FileHash -LiteralPath $Path -Algorithm SHA256).Hash.ToLowerInvariant()
}

$TempRoot = Join-Path ([System.IO.Path]::GetTempPath()) (
    'SheetPilotUpgrade-' + [System.Guid]::NewGuid().ToString('N')
)
$PayloadZip = Join-Path $TempRoot 'payload.zip'
$ExpandedPayload = Join-Path $TempRoot 'expanded'
$BackupRoot = $null
$PatchItems = @()
$PatchApplied = $false
$LockPath = Join-Path $ResolvedProjectRoot '.sheetpilot-upgrade.lock'
$LockStream = $null

function Restore-PatchedFiles {
    if (-not $PatchApplied -or $null -eq $BackupRoot) { return }
    foreach ($Item in $PatchItems) {
        if ($Item.HadOriginal) {
            $BackupPath = Join-Path $BackupRoot (
                ([string]$Item.Entry.Path).Replace(
                    [char]47, [System.IO.Path]::DirectorySeparatorChar
                )
            )
            if (Test-Path -LiteralPath $BackupPath -PathType Leaf) {
                Copy-Item -LiteralPath $BackupPath -Destination $Item.TargetPath -Force
            }
        }
        elseif (Test-Path -LiteralPath $Item.TargetPath -PathType Leaf) {
            Remove-Item -LiteralPath $Item.TargetPath -Force
        }
    }
    $script:PatchApplied = $false
}

try {
    try {
        $LockStream = [System.IO.File]::Open(
            $LockPath,
            [System.IO.FileMode]::OpenOrCreate,
            [System.IO.FileAccess]::ReadWrite,
            [System.IO.FileShare]::None
        )
    }
    catch {
        throw 'Another SheetPilot upgrade is already using this project folder.'
    }

    New-Item -ItemType Directory -Path $ExpandedPayload -Force | Out-Null
    $PayloadBytes = [System.Convert]::FromBase64String(($PayloadBase64 -replace '\s', ''))
    if ($PayloadBytes.Length -gt 10485760) {
        throw 'The embedded upgrade payload is unexpectedly large.'
    }
    [System.IO.File]::WriteAllBytes($PayloadZip, $PayloadBytes)
    if ((Get-Sha256 $PayloadZip) -ne $ExpectedPayloadHash) {
        throw 'The embedded upgrade payload failed its SHA-256 integrity check.'
    }

    Add-Type -AssemblyName System.IO.Compression
    Add-Type -AssemblyName System.IO.Compression.FileSystem
    $AllowedPaths = [System.Collections.Generic.HashSet[string]]::new(
        [System.StringComparer]::OrdinalIgnoreCase
    )
    foreach ($Entry in $Manifest) { [void]$AllowedPaths.Add([string]$Entry.Path) }
    $SeenPaths = [System.Collections.Generic.HashSet[string]]::new(
        [System.StringComparer]::OrdinalIgnoreCase
    )
    $Archive = [System.IO.Compression.ZipFile]::OpenRead($PayloadZip)
    try {
        [long]$ExpandedBytes = 0
        foreach ($ZipEntry in $Archive.Entries) {
            $EntryName = $ZipEntry.FullName.Replace('\', '/')
            if (-not $ZipEntry.Name) { continue }
            if (
                [System.IO.Path]::IsPathRooted($EntryName) -or
                $EntryName -match '(^|/)\.\.(/|$)' -or
                -not $AllowedPaths.Contains($EntryName) -or
                -not $SeenPaths.Add($EntryName)
            ) { throw "The payload contains an unexpected or unsafe entry: $EntryName" }
            $ExpandedBytes += $ZipEntry.Length
            if ($ZipEntry.Length -gt 3145728 -or $ExpandedBytes -gt 20971520) {
                throw 'The embedded payload exceeds its extraction limits.'
            }
        }
    }
    finally { $Archive.Dispose() }
    if ($SeenPaths.Count -ne $AllowedPaths.Count) {
        throw 'The embedded payload does not contain every manifest file exactly once.'
    }

    [System.IO.Compression.ZipFile]::ExtractToDirectory($PayloadZip, $ExpandedPayload)
    foreach ($Entry in $Manifest) {
        $PayloadFile = Join-Path $ExpandedPayload (
            ([string]$Entry.Path).Replace([char]47, [System.IO.Path]::DirectorySeparatorChar)
        )
        if ((Get-Sha256 $PayloadFile) -ne [string]$Entry.TargetHash) {
            throw "The extracted payload file failed verification: $($Entry.Path)"
        }
    }

    foreach ($Entry in $Manifest) {
        $TargetPath = Resolve-ProjectFile ([string]$Entry.Path)
        $CurrentHash = Get-Sha256 $TargetPath
        if ($CurrentHash -eq [string]$Entry.TargetHash) { continue }
        $AllowedBaselines = @($Entry.BaselineHashes)
        $SafeBaseline = (
            ($null -eq $CurrentHash -and [bool]$Entry.AllowMissing) -or
            ($null -ne $CurrentHash -and $AllowedBaselines -contains $CurrentHash)
        )
        if (-not $SafeBaseline -and -not $Force) {
            throw (
                "Refusing to overwrite a missing, modified, or unexpected file: $($Entry.Path). " +
                'Rerun with -Force only after reviewing it; every replaced file is backed up.'
            )
        }
        $PatchItems += [PSCustomObject]@{
            Entry = $Entry
            TargetPath = $TargetPath
            HadOriginal = $null -ne $CurrentHash
            OriginalHash = $CurrentHash
        }
    }

    if ($PatchItems.Count -gt 0) {
        $BackupName = '_sheetpilot_upgrade_backup___VERSION___' +
            (Get-Date -Format 'yyyyMMdd_HHmmss') + '_' +
            [System.Guid]::NewGuid().ToString('N').Substring(0, 8)
        $BackupRoot = Join-Path (Split-Path -Parent $ResolvedProjectRoot) $BackupName
        New-Item -ItemType Directory -Path $BackupRoot -Force | Out-Null
        foreach ($Item in $PatchItems) {
            if (-not $Item.HadOriginal) { continue }
            $BackupPath = Join-Path $BackupRoot (
                ([string]$Item.Entry.Path).Replace(
                    [char]47, [System.IO.Path]::DirectorySeparatorChar
                )
            )
            New-Item -ItemType Directory -Path (Split-Path -Parent $BackupPath) -Force |
                Out-Null
            Copy-Item -LiteralPath $Item.TargetPath -Destination $BackupPath -Force
        }
        $BackupManifest = @(
            foreach ($Item in $PatchItems) {
                [ordered]@{
                    path = [string]$Item.Entry.Path
                    had_original = [bool]$Item.HadOriginal
                    original_sha256 = $Item.OriginalHash
                }
            }
        )
        [System.IO.File]::WriteAllText(
            (Join-Path $BackupRoot 'upgrade-backup-manifest.json'),
            (($BackupManifest | ConvertTo-Json -Depth 4) + [Environment]::NewLine),
            [System.Text.UTF8Encoding]::new($false)
        )

        $PatchApplied = $true
        foreach ($Item in $PatchItems) {
            $SourcePath = Join-Path $ExpandedPayload (
                ([string]$Item.Entry.Path).Replace(
                    [char]47, [System.IO.Path]::DirectorySeparatorChar
                )
            )
            New-Item -ItemType Directory -Path (Split-Path -Parent $Item.TargetPath) -Force |
                Out-Null
            $TemporaryTarget = $Item.TargetPath + '.sheetpilot-' +
                [System.Guid]::NewGuid().ToString('N') + '.tmp'
            try {
                [System.IO.File]::WriteAllBytes(
                    $TemporaryTarget,
                    [System.IO.File]::ReadAllBytes($SourcePath)
                )
                if ($Item.HadOriginal) {
                    [System.IO.File]::SetAttributes(
                        $Item.TargetPath, [System.IO.FileAttributes]::Normal
                    )
                    try {
                        [System.IO.File]::Replace($TemporaryTarget, $Item.TargetPath, $null)
                    }
                    catch {
                        Move-Item -LiteralPath $TemporaryTarget -Destination $Item.TargetPath -Force
                    }
                }
                else {
                    [System.IO.File]::Move($TemporaryTarget, $Item.TargetPath)
                }
            }
            finally {
                if (Test-Path -LiteralPath $TemporaryTarget -PathType Leaf) {
                    Remove-Item -LiteralPath $TemporaryTarget -Force
                }
            }
            if ((Get-Sha256 $Item.TargetPath) -ne [string]$Item.Entry.TargetHash) {
                throw "Post-write verification failed: $($Item.Entry.Path)"
            }
        }
    }

    foreach ($Entry in $Manifest) {
        if ((Get-Sha256 (Resolve-ProjectFile ([string]$Entry.Path))) -ne [string]$Entry.TargetHash) {
            throw "Final source verification failed: $($Entry.Path)"
        }
    }

    if (-not $SkipSetup) {
        & (Resolve-ProjectFile 'scripts/setup.ps1') `
            -InstallPythonIfMissing:$InstallPythonIfMissing
    }
    if (-not $SkipChecks) { & (Resolve-ProjectFile 'scripts/test.ps1') }
    if ($BuildRelease) {
        & (Resolve-ProjectFile 'scripts/package.ps1') -SkipSetup -SkipChecks -AllowDirty
    }
}
catch {
    $Failure = $_
    try { Restore-PatchedFiles }
    catch {
        throw [System.InvalidOperationException]::new(
            'The upgrade failed and automatic source restoration also failed. ' +
            "Use the preserved backup at $BackupRoot. Original error: " +
            $Failure.Exception.Message,
            $Failure.Exception
        )
    }
    throw [System.InvalidOperationException]::new(
        'The upgrade failed; changed source files were restored. ' + $Failure.Exception.Message,
        $Failure.Exception
    )
}
finally {
    if ($null -ne $LockStream) { $LockStream.Dispose() }
    if (Test-Path -LiteralPath $LockPath -PathType Leaf) {
        Remove-Item -LiteralPath $LockPath -Force
    }
    if (Test-Path -LiteralPath $TempRoot -PathType Container) {
        Remove-Item -LiteralPath $TempRoot -Recurse -Force
    }
}

if ($null -ne $BackupRoot) {
    Write-Output "Original changed files were backed up outside the project: $BackupRoot"
}
else {
    Write-Output 'SheetPilot __VERSION__ source files were already current.'
}
Write-Output 'SheetPilot __VERSION__ upgrade and requested verification completed successfully.'
Write-Output "Start later with: & '$((Resolve-ProjectFile 'Start-SheetPilot.ps1'))'"

if ($Launch) {
    & (Resolve-ProjectFile 'Start-SheetPilot.ps1') `
        -InstallPythonIfMissing:$InstallPythonIfMissing
}
'@
    $Generated = $Template.Replace('__VERSION__', $Version)
    $Generated = $Generated.Replace('__MANIFEST__', $ManifestJson)
    $Generated = $Generated.Replace('__FILE_COUNT__', [string]$Manifest.Count)
    $Generated = $Generated.Replace('__PAYLOAD__', $Wrapped.ToString())
    $Generated = $Generated.Replace('__PAYLOAD_HASH__', $PayloadHash)
    [System.IO.File]::WriteAllText(
        $OutputPath,
        $Generated,
        [System.Text.UTF8Encoding]::new($false)
    )

    $Tokens = $null
    $ParseErrors = $null
    [void][System.Management.Automation.Language.Parser]::ParseFile(
        $OutputPath,
        [ref]$Tokens,
        [ref]$ParseErrors
    )
    if ($ParseErrors.Count -gt 0) {
        throw "The generated upgrade script does not parse: $($ParseErrors[0].Message)"
    }
    Write-Output "Upgrade script: $OutputPath"
    Write-Output "Payload files: $($Manifest.Count)"
    Write-Output "Upgrade SHA-256: $(Get-Sha256 -Path $OutputPath)"
}
finally {
    if (Test-Path -LiteralPath $TemporaryRoot -PathType Container) {
        Remove-Item -LiteralPath $TemporaryRoot -Recurse -Force
    }
}
