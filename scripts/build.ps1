[CmdletBinding()]
param(
    [switch]$SkipSetup,
    [switch]$SkipChecks
)

$ErrorActionPreference = 'Stop'
$ProjectRoot = Split-Path -Parent $PSScriptRoot
$Python = Join-Path $ProjectRoot '.venv\Scripts\python.exe'
$GeneratedRoot = Join-Path $ProjectRoot 'build\generated'
$IconPath = Join-Path $GeneratedRoot 'SheetPilot.ico'

if (-not (Test-Path -LiteralPath $Python -PathType Leaf)) {
    throw 'The repository .venv is required to build SheetPilot.'
}
$PythonVersion = & $Python -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')"
if ($LASTEXITCODE -ne 0 -or $PythonVersion.Trim() -ne '3.12') {
    throw "SheetPilot requires Python 3.12; the repository .venv reported $PythonVersion."
}
$Platform = & $Python -c "import sys; print(sys.platform)"
if ($LASTEXITCODE -ne 0 -or $Platform.Trim() -ne 'win32') {
    throw "SheetPilot Windows releases require Windows; the repository .venv reported $Platform."
}
$PythonBits = & $Python -c "import struct; print(struct.calcsize('P') * 8)"
if ($LASTEXITCODE -ne 0 -or $PythonBits.Trim() -ne '64') {
    throw "SheetPilot Windows releases require 64-bit Python; the repository .venv reported $PythonBits-bit."
}

if (-not $SkipSetup) {
    & (Join-Path $PSScriptRoot 'setup.ps1')
    if ($LASTEXITCODE -ne 0) { throw 'Environment setup failed.' }
}
if (-not $SkipChecks) {
    & (Join-Path $PSScriptRoot 'test.ps1')
    if ($LASTEXITCODE -ne 0) { throw 'Quality gate failed.' }
}

New-Item -ItemType Directory -Path $GeneratedRoot -Force | Out-Null
Add-Type -AssemblyName System.Drawing
$Bitmap = [System.Drawing.Bitmap]::new(64, 64)
$Graphics = [System.Drawing.Graphics]::FromImage($Bitmap)
$Brush = [System.Drawing.SolidBrush]::new([System.Drawing.Color]::FromArgb(32, 104, 191))
$TextBrush = [System.Drawing.SolidBrush]::new([System.Drawing.Color]::White)
$Font = [System.Drawing.Font]::new('Segoe UI', 36, [System.Drawing.FontStyle]::Bold)
$Handle = [IntPtr]::Zero
try {
    $Graphics.Clear([System.Drawing.Color]::Transparent)
    $Graphics.FillRectangle($Brush, 0, 0, 64, 64)
    $Graphics.DrawString('S', $Font, $TextBrush, 7, 4)
    $Handle = $Bitmap.GetHicon()
    $Icon = [System.Drawing.Icon]::FromHandle($Handle)
    $Stream = [System.IO.File]::Create($IconPath)
    try { $Icon.Save($Stream) } finally { $Stream.Dispose(); $Icon.Dispose() }
}
finally {
    $Font.Dispose()
    $TextBrush.Dispose()
    $Brush.Dispose()
    $Graphics.Dispose()
    $Bitmap.Dispose()
}

Push-Location $ProjectRoot
try {
    & $Python -m PyInstaller --noconfirm --clean --distpath dist --workpath build\pyinstaller SheetPilot.spec
    if ($LASTEXITCODE -ne 0) { throw 'PyInstaller one-folder build failed.' }
}
finally {
    Pop-Location
}

$Executable = Join-Path $ProjectRoot 'dist\SheetPilot\SheetPilot.exe'
if (-not (Test-Path -LiteralPath $Executable -PathType Leaf)) {
    throw "The build completed without the expected executable: $Executable"
}
Write-Output "One-folder build: $Executable"
