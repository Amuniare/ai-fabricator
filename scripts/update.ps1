# Updates Fabricator. Safe to run any time; your projects are kept elsewhere and are not touched.
$ErrorActionPreference = 'Continue'
$ToolDir = Split-Path -Parent $PSScriptRoot
Set-Location $ToolDir

$uvDir = Join-Path $env:USERPROFILE '.local\bin'
if (Test-Path $uvDir) { $env:Path = "$uvDir;$env:Path" }

if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
    Write-Host "Fabricator isn't set up yet. Double-click setup.bat first." -ForegroundColor Yellow
    exit 1
}
Write-Host "Checking for a newer version of Fabricator..."
uv run fabricator update
