<#
.SYNOPSIS
    產生 Windows 測試用便攜包
.DESCRIPTION
    排除 .git / __pycache__ / runs / 本地測試 artifacts，
    打包成 dist/betguard-assistant-v0.5.7-wife-test.zip
#>

$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot\..

$version = "v0.5.7-wife-test"
$outDir = "dist"
$zipName = "betguard-assistant-$version.zip"
$zipPath = "$outDir\$zipName"

# Clean
if (Test-Path $zipPath) { Remove-Item $zipPath -Force }

# Exclude patterns
$exclude = @(
    ".git",
    "__pycache__",
    ".pytest_cache",
    ".mypy_cache",
    "*.pyc",
    "runs",
    "demo_out",
    "selector_report*.json",
    "site_profile*.json",
    "queue_*.json",
    "dist",
    "node_modules",
    ".vscode",
    ".egg-info",
    ".venv",
    "venv"
)

Write-Host "建立 $zipName ..."

# Build exclude args for Compress-Archive (limited support)
# Use 7zip if available, otherwise use .NET
$files = Get-ChildItem -Recurse -File | Where-Object {
    $rel = $_.FullName.Substring((Get-Location).Path.Length + 1)
    foreach ($pat in $exclude) {
        if ($rel -like "*$pat*") { return $false }
    }
    return $true
}

# Create temp dir with only wanted files
$tempDir = "$outDir\temp_pkg"
if (Test-Path $tempDir) { Remove-Item $tempDir -Recurse -Force }
New-Item -ItemType Directory -Path $tempDir -Force | Out-Null

foreach ($f in $files) {
    $rel = $f.FullName.Substring((Get-Location).Path.Length + 1)
    $dest = Join-Path $tempDir $rel
    $destDir = Split-Path $dest -Parent
    if (!(Test-Path $destDir)) { New-Item -ItemType Directory -Path $destDir -Force | Out-Null }
    Copy-Item $f.FullName $dest -Force
}

# Zip
if (Test-Path $zipPath) { Remove-Item $zipPath -Force }
Compress-Archive -Path "$tempDir\*" -DestinationPath $zipPath -Force

# Clean temp
Remove-Item $tempDir -Recurse -Force

$size = (Get-Item $zipPath).Length
Write-Host "✅ $zipPath  ($([math]::Round($size/1MB, 1)) MB)"
Write-Host "解壓縮後請執行 scripts\start_betguard.bat"
