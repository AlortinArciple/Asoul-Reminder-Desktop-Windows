param(
  [switch]$IncludePrivateConfig
)

$ErrorActionPreference = "Stop"

$Root = Resolve-Path (Join-Path $PSScriptRoot "..")
$DistApp = Join-Path $Root "dist\AsoulReminder"
$Packages = Join-Path $Root "dist\packages"
$PackageName = if ($IncludePrivateConfig) { "AsoulReminder-portable-with-config" } else { "AsoulReminder-portable" }
$PackageDir = Join-Path $Packages $PackageName
$ZipPath = Join-Path $Packages "$PackageName.zip"

if (-not (Test-Path (Join-Path $DistApp "AsoulReminder.exe"))) {
  throw "Missing dist\AsoulReminder\AsoulReminder.exe. Run scripts\build.ps1 first."
}

$PackagesFull = [System.IO.Path]::GetFullPath($Packages)
$PackageDirFull = [System.IO.Path]::GetFullPath($PackageDir)
$ZipPathFull = [System.IO.Path]::GetFullPath($ZipPath)
if (-not $PackageDirFull.StartsWith($PackagesFull, [System.StringComparison]::OrdinalIgnoreCase)) {
  throw "Refusing to package outside dist\packages."
}
if (-not $ZipPathFull.StartsWith($PackagesFull, [System.StringComparison]::OrdinalIgnoreCase)) {
  throw "Refusing to write zip outside dist\packages."
}

New-Item -ItemType Directory -Path $Packages -Force | Out-Null
if (Test-Path $PackageDir) {
  Remove-Item -LiteralPath $PackageDir -Recurse -Force
}
if (Test-Path $ZipPath) {
  Remove-Item -LiteralPath $ZipPath -Force
}

New-Item -ItemType Directory -Path $PackageDir -Force | Out-Null
Copy-Item -LiteralPath (Join-Path $DistApp "AsoulReminder.exe") -Destination $PackageDir -Force
Copy-Item -LiteralPath (Join-Path $DistApp "_internal") -Destination $PackageDir -Recurse -Force
Copy-Item -LiteralPath (Join-Path $Root "README.md") -Destination $PackageDir -Force
Copy-Item -LiteralPath (Join-Path $Root "config.example.json") -Destination (Join-Path $PackageDir "config.example.json") -Force
$VersionPath = Join-Path $Root "build\version.json"
if (Test-Path $VersionPath) {
  Copy-Item -LiteralPath $VersionPath -Destination (Join-Path $PackageDir "version.json") -Force
}
Copy-Item -LiteralPath (Join-Path $Root "scripts\portable\start-window.bat") -Destination $PackageDir -Force
Copy-Item -LiteralPath (Join-Path $Root "scripts\portable\start-tray.bat") -Destination $PackageDir -Force
Copy-Item -LiteralPath (Join-Path $Root "scripts\portable\install-current-user.ps1") -Destination $PackageDir -Force
Copy-Item -LiteralPath (Join-Path $Root "scripts\portable\README-portable.txt") -Destination $PackageDir -Force
New-Item -ItemType Directory -Path (Join-Path $PackageDir "assets\avatars") -Force | Out-Null
Copy-Item -Path (Join-Path $Root "assets\avatars\*") -Destination (Join-Path $PackageDir "assets\avatars") -Force

if ($IncludePrivateConfig) {
  Copy-Item -LiteralPath (Join-Path $Root "config.json") -Destination (Join-Path $PackageDir "config.json") -Force
} else {
  $ConfigPath = Join-Path $PackageDir "config.json"
  if (Test-Path $ConfigPath) {
    Remove-Item -LiteralPath $ConfigPath -Force
  }
}

Compress-Archive -LiteralPath $PackageDir -DestinationPath $ZipPath -Force

if (-not $IncludePrivateConfig) {
  Add-Type -AssemblyName System.IO.Compression.FileSystem
  $Archive = [System.IO.Compression.ZipFile]::OpenRead($ZipPath)
  try {
    $PrivateEntry = $Archive.Entries | Where-Object {
      $_.FullName -match '(^|[\\/])config\.json$'
    }
    if ($PrivateEntry) {
      throw "Standard portable package unexpectedly contains config.json."
    }
  } finally {
    $Archive.Dispose()
  }
}

Write-Host "Portable package created: $ZipPath"
