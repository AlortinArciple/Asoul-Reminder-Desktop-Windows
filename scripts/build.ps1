$ErrorActionPreference = "Stop"

if (-not (Test-Path ".venv\Scripts\python.exe")) {
  py -m venv .venv
}

$Commit = "uncommitted"
$Dirty = $true
$GitCommit = & git show-ref --head --hash HEAD
if ($LASTEXITCODE -eq 0 -and $GitCommit) {
  $FullCommit = $GitCommit.Trim()
  $Commit = $FullCommit.Substring(0, [Math]::Min(7, $FullCommit.Length))
  $Dirty = [bool](& git status --porcelain)
}
$BuildInfoDir = ".\build"
New-Item -ItemType Directory -Path $BuildInfoDir -Force | Out-Null
@{
  commit = $Commit
  dirty = $Dirty
  built_at = (Get-Date).ToString("yyyy-MM-ddTHH:mm:ssK")
} | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $BuildInfoDir "version.json") -Encoding UTF8
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m pip install pyinstaller

.\.venv\Scripts\python.exe -m PyInstaller `
  --noconfirm `
  --clean `
  --noconsole `
  --onedir `
  --name AsoulReminder `
  --icon ".\assets\asoul_tray_icon.ico" `
  --add-data "assets;assets" `
  --add-data "config.example.json;." `
  --add-data "build/version.json;." `
  run.py

$PrivateConfig = ".\dist\AsoulReminder\config.json"
if (Test-Path $PrivateConfig) {
  Remove-Item -LiteralPath $PrivateConfig -Force
}

New-Item -ItemType Directory -Path ".\dist\AsoulReminder\assets\avatars" -Force | Out-Null
Copy-Item -Path ".\assets\avatars\*" -Destination ".\dist\AsoulReminder\assets\avatars" -Force

.\scripts\package-portable.ps1
