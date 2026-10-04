# Fabricator setup for Windows 11. Safe to run again at any time.
$ErrorActionPreference = 'Continue'
$ToolDir = Split-Path -Parent $PSScriptRoot
Set-Location $ToolDir
$Total = 10
$script:Step = 0
$script:Notes = @()

function Step($text) {
    $script:Step++
    Write-Host ""
    Write-Host ("[{0}/{1}] {2}" -f $script:Step, $Total, $text) -ForegroundColor Cyan
}
function Say($text)  { Write-Host "      $text" }
function Good($text) { Write-Host "      OK: $text" -ForegroundColor Green }
function Warn($text) { Write-Host "      NOTE: $text" -ForegroundColor Yellow; $script:Notes += $text }

function Refresh-Path {
    $machine = [Environment]::GetEnvironmentVariable('Path', 'Machine')
    $user = [Environment]::GetEnvironmentVariable('Path', 'User')
    $extra = Join-Path $env:USERPROFILE '.local\bin'
    $env:Path = "$extra;$machine;$user"
}
function Have($cmd) { return [bool](Get-Command $cmd -ErrorAction SilentlyContinue) }

function Install-WithWinget($id, $label) {
    if (-not (Have 'winget')) {
        Warn "$label is missing and this PC has no 'winget'. Please install $label by hand, then run setup again."
        return
    }
    Say "Installing $label (this can take a few minutes)..."
    winget install --id $id -e --silent --accept-package-agreements --accept-source-agreements
    Refresh-Path
}

function Find-Code {
    if (Have 'code') { return (Get-Command code).Source }
    $candidates = @(
        (Join-Path $env:LOCALAPPDATA 'Programs\Microsoft VS Code\bin\code.cmd'),
        (Join-Path $env:ProgramFiles 'Microsoft VS Code\bin\code.cmd'),
        (Join-Path ${env:ProgramFiles(x86)} 'Microsoft VS Code\bin\code.cmd')
    )
    foreach ($c in $candidates) { if ($c -and (Test-Path $c)) { return $c } }
    return $null
}
function Find-CodeExe {
    $candidates = @(
        (Join-Path $env:LOCALAPPDATA 'Programs\Microsoft VS Code\Code.exe'),
        (Join-Path $env:ProgramFiles 'Microsoft VS Code\Code.exe')
    )
    foreach ($c in $candidates) { if ($c -and (Test-Path $c)) { return $c } }
    return $null
}

Write-Host "=== Fabricator setup ===" -ForegroundColor Green
Write-Host "This installs what Fabricator needs. It is safe to run again."
Refresh-Path

# 1 Git
Step "Git (keeps a history of your designs)"
if (Have 'git') { Good "Git is already installed." }
else {
    Install-WithWinget 'Git.Git' 'Git'
    if (Have 'git') { Good "Git installed." } else { Warn "Git could not be installed automatically. Get it from git-scm.com, then run setup again." }
}

# 2 VS Code
Step "Visual Studio Code (where you talk to Claude)"
if ((Find-Code) -or (Find-CodeExe)) { Good "VS Code is already installed." }
else {
    Install-WithWinget 'Microsoft.VisualStudioCode' 'Visual Studio Code'
    if (Find-Code) { Good "VS Code installed." } else { Warn "VS Code could not be installed automatically. Get it from code.visualstudio.com, then run setup again." }
}

# 3 uv
Step "uv (installs the right Python for you)"
if (Have 'uv') { Good "uv is already installed." }
else {
    try {
        Say "Downloading uv..."
        Invoke-RestMethod https://astral.sh/uv/install.ps1 | Invoke-Expression
        Refresh-Path
    } catch { Warn "Could not download uv: $($_.Exception.Message)" }
    if (Have 'uv') { Good "uv installed." } else { Warn "uv is not available. Check your internet connection and run setup again." }
}

# 4 uv sync
Step "Fabricator's own pieces (first time takes a few minutes)"
if (Have 'uv') {
    uv sync
    if ($LASTEXITCODE -eq 0) { Good "Everything Fabricator needs is installed." }
    else { Warn "Installing Fabricator's pieces failed. Check your internet connection and run setup again." }
} else { Warn "Skipped, because uv is missing." }

# 5 Projects folder
Step "Your projects folder"
$Docs = [Environment]::GetFolderPath('MyDocuments')
$Projects = Join-Path $Docs 'Fabricator Projects'
try {
    New-Item -ItemType Directory -Force -Path $Projects | Out-Null
    Good "Your designs will be kept in: $Projects"
} catch { Warn "Could not create $Projects" }

# 6 Claude settings
Step "Letting Claude work in your projects folder"
try {
    $dir = Join-Path $ToolDir '.claude'
    New-Item -ItemType Directory -Force -Path $dir | Out-Null
    $file = Join-Path $dir 'settings.local.json'
    $cfg = [ordered]@{}
    if (Test-Path $file) {
        try {
            $existing = Get-Content $file -Raw | ConvertFrom-Json
            foreach ($p in $existing.PSObject.Properties) { $cfg[$p.Name] = $p.Value }
        } catch { Say "Existing settings file wasn't readable; keeping a copy as settings.local.json.bak"; Copy-Item $file "$file.bak" -Force }
    }
    $perm = [ordered]@{}
    if ($cfg.Contains('permissions') -and $cfg['permissions']) {
        foreach ($p in $cfg['permissions'].PSObject.Properties) { $perm[$p.Name] = $p.Value }
    }
    $dirs = @()
    if ($perm.Contains('additionalDirectories') -and $perm['additionalDirectories']) { $dirs = @($perm['additionalDirectories']) }
    if ($dirs -notcontains $Projects) { $dirs += $Projects }
    $perm['additionalDirectories'] = @($dirs)
    $cfg['permissions'] = $perm
    $cfg | ConvertTo-Json -Depth 10 | Set-Content -Path $file -Encoding UTF8
    Good "Saved."
} catch { Warn "Could not write the Claude settings: $($_.Exception.Message)" }

# 7 Claude Code extension
Step "Claude Code extension for VS Code"
$code = Find-Code
if ($code) {
    & $code --install-extension anthropic.claude-code --force 2>&1 | ForEach-Object { Say $_ }
    if ($LASTEXITCODE -eq 0) { Good "Claude Code extension is installed." } else { Warn "Could not install the extension. In VS Code, open Extensions and search for 'Claude Code'." }
} else { Warn "VS Code not found, so the extension was not installed." }

# 8 Bambu Studio
Step "Bambu Studio (the program that sends the file to your printer)"
$bambu = $null
foreach ($p in @((Join-Path $env:ProgramFiles 'Bambu Studio\bambu-studio.exe'),
                 (Join-Path ${env:ProgramFiles(x86)} 'Bambu Studio\bambu-studio.exe'))) {
    if ($p -and (Test-Path $p)) { $bambu = $p }
}
if (-not $bambu) {
    foreach ($key in @('HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\*',
                       'HKLM:\SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall\*',
                       'HKCU:\SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\*')) {
        $hit = Get-ItemProperty $key -ErrorAction SilentlyContinue | Where-Object { $_.DisplayName -like '*Bambu Studio*' } | Select-Object -First 1
        if ($hit) { $bambu = if ($hit.InstallLocation) { $hit.InstallLocation } else { $hit.DisplayName }; break }
    }
}
if ($bambu) { Good "Bambu Studio found: $bambu" }
else { Warn "Bambu Studio is not installed. Download it free from bambulab.com (Support > Download), install it, open it once, and sign in or skip. Fabricator needs it to prepare print files." }

# 9 Shortcut
Step "Desktop shortcut"
try {
    $target = $code
    if (-not $target) { $target = Find-CodeExe }
    if ($target) {
        $exe = Find-CodeExe
        $desktop = [Environment]::GetFolderPath('Desktop')
        $shell = New-Object -ComObject WScript.Shell
        $lnk = $shell.CreateShortcut((Join-Path $desktop 'Fabricator.lnk'))
        if ($exe) { $lnk.TargetPath = $exe; $lnk.IconLocation = $exe } else { $lnk.TargetPath = $target }
        $lnk.Arguments = '"' + $ToolDir + '"'
        $lnk.WorkingDirectory = $ToolDir
        $lnk.Description = 'Open Fabricator in VS Code'
        $lnk.Save()
        Good "Added 'Fabricator' to your desktop."
    } else { Warn "No shortcut made, because VS Code was not found." }
} catch { Warn "Could not create the shortcut: $($_.Exception.Message)" }

# 10 Check
Step "Checking everything"
if (Have 'uv') { uv run fabricator doctor } else { Warn "Skipped, because uv is missing." }

Write-Host ""
Write-Host "=== Setup finished ===" -ForegroundColor Green
if ($script:Notes.Count -gt 0) {
    Write-Host "Things to take care of:" -ForegroundColor Yellow
    foreach ($n in $script:Notes) { Write-Host "  - $n" }
    Write-Host "Then double-click setup.bat again to re-check."
}
Write-Host ""
Write-Host "Next steps:"
Write-Host "  1. VS Code opens on Fabricator now (or use the 'Fabricator' icon on your desktop)."
Write-Host "  2. Click the Claude icon, sign in, and say hello."
Write-Host "  3. Tell Claude what you want to make."
if ($code) { & $code $ToolDir } elseif (Find-CodeExe) { Start-Process (Find-CodeExe) -ArgumentList ('"' + $ToolDir + '"') }
