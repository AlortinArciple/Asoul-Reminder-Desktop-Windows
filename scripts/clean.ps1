param(
  [switch]$Build,
  [switch]$Dist,
  [switch]$Caches,
  [switch]$All
)

$ErrorActionPreference = "Stop"

$Root = [System.IO.Path]::GetFullPath((Resolve-Path (Join-Path $PSScriptRoot "..")))

function Remove-UnderRoot {
  param([string]$Path)

  $FullPath = [System.IO.Path]::GetFullPath((Join-Path $Root $Path))
  if (-not $FullPath.StartsWith($Root, [System.StringComparison]::OrdinalIgnoreCase)) {
    throw "Refusing to remove outside project root: $FullPath"
  }
  if (Test-Path -LiteralPath $FullPath) {
    Remove-Item -LiteralPath $FullPath -Recurse -Force
    Write-Host "Removed $Path"
  }
}

if (-not ($Build -or $Dist -or $Caches -or $All)) {
  Write-Host "Nothing selected. Use -Caches, -Build, -Dist, or -All."
  exit 0
}

if ($All -or $Caches) {
  Remove-UnderRoot "__pycache__"
  foreach ($CacheRoot in @("asoul_reminder", "tests")) {
    $CacheRootPath = Join-Path $Root $CacheRoot
    if (Test-Path -LiteralPath $CacheRootPath) {
      Get-ChildItem -LiteralPath $CacheRootPath -Recurse -Force -Directory -Filter "__pycache__" |
        ForEach-Object { Remove-Item -LiteralPath $_.FullName -Recurse -Force }
    }
  }
  Remove-UnderRoot ".pytest_cache"
  Remove-UnderRoot ".mypy_cache"
}

if ($All -or $Build) {
  Remove-UnderRoot "build"
  Remove-UnderRoot "AsoulReminder.spec"
  Remove-UnderRoot "ASOULReminder.spec"
}

if ($All -or $Dist) {
  Remove-UnderRoot "dist"
}
