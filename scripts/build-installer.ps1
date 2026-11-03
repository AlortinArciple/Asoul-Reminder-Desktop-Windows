param(
  [switch]$IncludePrivateConfig
)

$ErrorActionPreference = "Stop"

$Root = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$Compiler = Join-Path $Root "build-tools\InnoSetup7\ISCC.exe"
$Script = Join-Path $Root "installer\AsoulReminder.iss"
$DistApp = Join-Path $Root "dist\AsoulReminder"
$OutputDir = Join-Path $Root "dist\installer"
$VersionPath = Join-Path $Root "build\version.json"

if (-not (Test-Path -LiteralPath $Compiler)) {
  throw "Missing Inno Setup compiler: $Compiler"
}
if (-not (Test-Path -LiteralPath (Join-Path $DistApp "AsoulReminder.exe"))) {
  throw "Missing packaged application. Run scripts\build.ps1 first."
}
if (-not (Test-Path -LiteralPath $VersionPath)) {
  throw "Missing build\version.json. Run scripts\build.ps1 first."
}

$VersionInfo = Get-Content -LiteralPath $VersionPath -Encoding UTF8 -Raw | ConvertFrom-Json
$BuiltAt = [DateTimeOffset]::Parse([string]$VersionInfo.built_at)
$AppVersion = $BuiltAt.ToString("yyyy.MM.dd")
$VersionInfoVersion = $BuiltAt.ToString("yyyy.M.d.0")
$OutputBaseName = if ($IncludePrivateConfig) {
  "AsoulReminder-Setup-with-config"
} else {
  "AsoulReminder-Setup"
}

New-Item -ItemType Directory -Path $OutputDir -Force | Out-Null
$OutputPath = Join-Path $OutputDir "$OutputBaseName.exe"
if (Test-Path -LiteralPath $OutputPath) {
  Remove-Item -LiteralPath $OutputPath -Force
}

$CompilerArgs = @(
  "/Qp",
  "/DMyAppVersion=$AppVersion",
  "/DMyVersionInfoVersion=$VersionInfoVersion",
  "/DProjectRoot=$Root",
  "/DSourceDir=$DistApp",
  "/O$OutputDir",
  "/F$OutputBaseName"
)

if ($IncludePrivateConfig) {
  $PrivateConfig = Join-Path $Root "config.json"
  if (-not (Test-Path -LiteralPath $PrivateConfig)) {
    throw "Missing private config: $PrivateConfig"
  }
  $CompilerArgs += "/DIncludePrivateConfig=1"
  $CompilerArgs += "/DPrivateConfigPath=$PrivateConfig"
}

& $Compiler @CompilerArgs $Script
if ($LASTEXITCODE -ne 0) {
  throw "Inno Setup compiler exited with code $LASTEXITCODE"
}
if (-not (Test-Path -LiteralPath $OutputPath)) {
  throw "Installer was not created: $OutputPath"
}

$Hash = Get-FileHash -LiteralPath $OutputPath -Algorithm SHA256
$Signature = Get-AuthenticodeSignature -LiteralPath $OutputPath

Write-Host "Installer created: $OutputPath"
Write-Host "SHA256: $($Hash.Hash)"
Write-Host "Signature: $($Signature.Status)"
if ($IncludePrivateConfig) {
  Write-Warning "This installer contains config.json and may contain a Bilibili Cookie. Do not share it."
}
