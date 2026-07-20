[CmdletBinding()]
param(
    [switch]$InstallPythonIfMissing,
    [switch]$ForceRecreate
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

$ProjectRoot = (Resolve-Path -LiteralPath (Split-Path -Parent $PSScriptRoot)).Path
$VenvRoot = Join-Path $ProjectRoot '.venv'
$Python = Join-Path $VenvRoot 'Scripts\python.exe'
$LockFile = Join-Path $ProjectRoot 'requirements.lock'

function Test-CompatiblePython {
    param(
        [Parameter(Mandatory = $true)][string]$Path,
        [string[]]$PrefixArguments = @()
    )

    try {
        $ProbeArguments = @($PrefixArguments) + @(
            '-c',
            'import struct, sys; print(f"{sys.version_info.major}.{sys.version_info.minor}|{sys.platform}|{struct.calcsize(''P'') * 8}")'
        )
        $Probe = & $Path @ProbeArguments 2>$null
        if ($LASTEXITCODE -eq 0 -and $Probe.Trim() -eq '3.12|win32|64') {
            return [PSCustomObject]@{
                Path = $Path
                PrefixArguments = [string[]]$PrefixArguments
            }
        }
    }
    catch {
        return $null
    }
    return $null
}

function Find-CompatiblePython {
    $Launcher = Get-Command 'py.exe' -CommandType Application -ErrorAction SilentlyContinue |
        Select-Object -First 1
    if ($null -ne $Launcher) {
        $Match = Test-CompatiblePython -Path $Launcher.Path -PrefixArguments @('-3.12-64')
        if ($null -ne $Match) { return $Match }
    }

    $CandidatePaths = @()
    if ($env:LocalAppData) {
        $CandidatePaths += Join-Path $env:LocalAppData 'Programs\Python\Python312\python.exe'
    }
    foreach ($Name in @('python3.12.exe', 'python.exe')) {
        $Command = Get-Command $Name -CommandType Application -ErrorAction SilentlyContinue |
            Select-Object -First 1
        if ($null -ne $Command) { $CandidatePaths += $Command.Path }
    }
    foreach ($CandidatePath in $CandidatePaths | Select-Object -Unique) {
        if (-not (Test-Path -LiteralPath $CandidatePath -PathType Leaf)) { continue }
        $Match = Test-CompatiblePython -Path $CandidatePath
        if ($null -ne $Match) { return $Match }
    }
    return $null
}

function Install-Python312 {
    $Winget = Get-Command 'winget.exe' -CommandType Application -ErrorAction SilentlyContinue |
        Select-Object -First 1
    if ($null -eq $Winget) {
        throw 'Python 3.12 x64 was not found and winget is unavailable for automatic installation.'
    }
    & $Winget.Path install --id Python.Python.3.12 --exact --scope user `
        --accept-package-agreements --accept-source-agreements --disable-interactivity
    if ($LASTEXITCODE -ne 0) { throw 'Automatic Python 3.12 installation failed.' }
}

function Remove-ProjectEnvironment {
    param([Parameter(Mandatory = $true)][string]$Path)

    $Expected = [System.IO.Path]::GetFullPath($VenvRoot)
    $Candidate = [System.IO.Path]::GetFullPath($Path)
    if (-not $Candidate.Equals($Expected, [System.StringComparison]::OrdinalIgnoreCase)) {
        throw "Refusing to remove an unexpected environment path: $Candidate"
    }
    if (Test-Path -LiteralPath $Candidate) {
        Remove-Item -LiteralPath $Candidate -Recurse -Force
    }
}

if (-not (Test-Path -LiteralPath $LockFile -PathType Leaf)) {
    throw "The locked dependency file is required: $LockFile"
}

$Environment = if (-not $ForceRecreate -and (Test-Path -LiteralPath $Python -PathType Leaf)) {
    Test-CompatiblePython -Path $Python
}
else {
    $null
}
$StaleVenv = $null
if ($null -eq $Environment) {
    $Interpreter = Find-CompatiblePython
    if ($null -eq $Interpreter -and $InstallPythonIfMissing) {
        Install-Python312
        $Interpreter = Find-CompatiblePython
    }
    if ($null -eq $Interpreter) {
        throw (
            'SheetPilot requires 64-bit Python 3.12 on Windows. Install it or rerun ' +
            '.\scripts\setup.ps1 -InstallPythonIfMissing.'
        )
    }

    if (Test-Path -LiteralPath $VenvRoot) {
        $VenvItem = Get-Item -LiteralPath $VenvRoot -Force
        if (-not $VenvItem.PSIsContainer) {
            throw "The environment path exists but is not a directory: $VenvRoot"
        }
        if (($VenvItem.Attributes -band [System.IO.FileAttributes]::ReparsePoint) -ne 0) {
            throw 'Refusing to replace a virtual environment stored through a reparse point.'
        }
        $StaleName = '.venv.sheetpilot-stale-' + (Get-Date -Format 'yyyyMMddHHmmss') + '-' +
            [System.Guid]::NewGuid().ToString('N').Substring(0, 8)
        $StaleVenv = Join-Path $ProjectRoot $StaleName
        Move-Item -LiteralPath $VenvRoot -Destination $StaleVenv
        Write-Output "Quarantined an unusable project environment: $StaleVenv"
    }

    try {
        Write-Output "Creating isolated environment: $VenvRoot"
        $VenvArguments = @($Interpreter.PrefixArguments) + @('-m', 'venv', $VenvRoot)
        & $Interpreter.Path @VenvArguments
        if ($LASTEXITCODE -ne 0 -or -not (Test-Path -LiteralPath $Python -PathType Leaf)) {
            throw 'Creating the SheetPilot virtual environment failed.'
        }
    }
    catch {
        if (Test-Path -LiteralPath $VenvRoot) {
            Remove-ProjectEnvironment -Path $VenvRoot
        }
        if ($null -ne $StaleVenv -and (Test-Path -LiteralPath $StaleVenv)) {
            Move-Item -LiteralPath $StaleVenv -Destination $VenvRoot
        }
        throw
    }
}

try {
    $Version = & $Python -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')"
    if ($LASTEXITCODE -ne 0 -or $Version.Trim() -ne '3.12') {
        throw "SheetPilot requires Python 3.12; the project environment reported $Version."
    }
    $Platform = & $Python -c "import sys; print(sys.platform)"
    if ($LASTEXITCODE -ne 0 -or $Platform.Trim() -ne 'win32') {
        throw "The locked release environment requires Windows; the project environment reported $Platform."
    }
    $PythonBits = & $Python -c "import struct; print(struct.calcsize('P') * 8)"
    if ($LASTEXITCODE -ne 0 -or $PythonBits.Trim() -ne '64') {
        throw "The locked release environment requires 64-bit Python; the project environment reported $PythonBits-bit."
    }

    Push-Location $ProjectRoot
    try {
        & $Python -m pip install --disable-pip-version-check --requirement $LockFile
        if ($LASTEXITCODE -ne 0) { throw 'Locked dependency installation failed.' }
        & $Python -m pip install --disable-pip-version-check --no-deps --editable .
        if ($LASTEXITCODE -ne 0) { throw 'Editable SheetPilot installation failed.' }
        & $Python -m pip check
        if ($LASTEXITCODE -ne 0) {
            throw 'The locked environment contains incompatible dependencies.'
        }
    }
    finally {
        Pop-Location
    }
}
catch {
    if ($null -ne $StaleVenv -and (Test-Path -LiteralPath $StaleVenv)) {
        if (Test-Path -LiteralPath $VenvRoot) {
            Remove-ProjectEnvironment -Path $VenvRoot
        }
        Move-Item -LiteralPath $StaleVenv -Destination $VenvRoot
        Write-Warning 'Setup failed; the previous project environment was restored.'
    }
    throw
}

if ($null -ne $StaleVenv -and (Test-Path -LiteralPath $StaleVenv)) {
    Remove-Item -LiteralPath $StaleVenv -Recurse -Force
    Write-Output 'Removed the quarantined unusable environment after successful setup.'
}

Write-Output 'SheetPilot setup completed successfully.'
