$ErrorActionPreference = "Stop"

$Source = Split-Path -Parent $MyInvocation.MyCommand.Path
$InstallRoot = Join-Path $env:LOCALAPPDATA "AsoulReminder"
$Target = Join-Path $InstallRoot "App"

New-Item -ItemType Directory -Path $Target -Force | Out-Null
Copy-Item -Path (Join-Path $Source "*") -Destination $Target -Recurse -Force

$Exe = Join-Path $Target "AsoulReminder.exe"
$Desktop = [Environment]::GetFolderPath("Desktop")
$Shortcut = Join-Path $Desktop "Asoul Reminder.lnk"
$Shell = New-Object -ComObject WScript.Shell
$Link = $Shell.CreateShortcut($Shortcut)
$Link.TargetPath = $Exe
$Link.WorkingDirectory = $Target
$Link.IconLocation = $Exe
$Link.Save()

Write-Host "Installed to: $Target"
Write-Host "Desktop shortcut created: $Shortcut"
Write-Host "Run AsoulReminder.exe to open the window, or run AsoulReminder.exe --tray to start minimized."
