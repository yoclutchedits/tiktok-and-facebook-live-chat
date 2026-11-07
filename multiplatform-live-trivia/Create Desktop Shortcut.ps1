$ErrorActionPreference = 'Stop'
$projectDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$launcher = Join-Path $projectDir 'Start Trivia.bat'
if (-not (Test-Path -LiteralPath $launcher)) {
    throw "Start Trivia.bat was not found in $projectDir"
}
$desktop = [Environment]::GetFolderPath('Desktop')
$shortcutPath = Join-Path $desktop 'Multiplatform Live Trivia.lnk'
$wsh = New-Object -ComObject WScript.Shell
$shortcut = $wsh.CreateShortcut($shortcutPath)
$shortcut.TargetPath = $launcher
$shortcut.WorkingDirectory = $projectDir
$shortcut.Description = 'Start the local Multiplatform LIVE Trivia dashboard'
$shortcut.IconLocation = "$env:SystemRoot\System32\SHELL32.dll, 13"
$shortcut.Save()
Write-Host "Desktop shortcut created: $shortcutPath" -ForegroundColor Green
Write-Host 'Double-click it whenever you want to start the trivia app.'
