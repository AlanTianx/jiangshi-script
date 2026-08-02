# Green portable build: deps -> PyInstaller -> zip
# Usage: powershell -ExecutionPolicy Bypass -File scripts\build.ps1
# Or double-click build.bat at repo root.

$ErrorActionPreference = "Stop"

$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root

$VenvPython = Join-Path $Root ".venv\Scripts\python.exe"
$VenvPip = Join-Path $Root ".venv\Scripts\pip.exe"
$Spec = Join-Path $Root "main.spec"
$TessExe = Join-Path $Root "tesseract\tesseract.exe"
$ChiData = Join-Path $Root "tesseract\tessdata\chi_sim.traineddata"

# 从 main.spec 读取 EXE/COLLECT 名称，避免脚本编码问题
$AppName = $null
foreach ($line in Get-Content -LiteralPath $Spec -Encoding UTF8) {
    if ($line -match '^\s*name\s*=\s*"(.+)"\s*,?\s*$') {
        $AppName = $Matches[1]
        break
    }
}
if (-not $AppName) {
    throw "cannot parse app name from main.spec"
}

Write-Host "==> workdir: $Root"
Write-Host "==> app name: $AppName"

if (-not (Test-Path $Spec)) {
    throw "main.spec not found: $Spec"
}

if (-not (Test-Path $VenvPython)) {
    Write-Host "==> create .venv ..."
    python -m venv (Join-Path $Root ".venv")
}

Write-Host "==> install requirements ..."
& $VenvPip install -r (Join-Path $Root "requirements.txt") | Out-Host

if (-not (Test-Path $TessExe) -or -not (Test-Path $ChiData)) {
    Write-Host "==> WARN: portable Tesseract incomplete (missing exe or chi_sim.traineddata)"
    Write-Host "    build continues; OCR may fail. Run scripts\setup_tesseract.ps1 first."
} else {
    Write-Host "==> portable Tesseract found, will be bundled"
}

Write-Host "==> PyInstaller --clean ..."
& $VenvPython -m PyInstaller --noconfirm --clean $Spec
if ($LASTEXITCODE -ne 0) {
    throw "PyInstaller failed, exit=$LASTEXITCODE"
}

# 以 dist 下实际目录为准（兼容编码差异）
$DistDir = Join-Path $Root ("dist\" + $AppName)
if (-not (Test-Path -LiteralPath $DistDir)) {
    $found = Get-ChildItem -LiteralPath (Join-Path $Root "dist") -Directory -ErrorAction SilentlyContinue |
        Where-Object { Test-Path -LiteralPath (Join-Path $_.FullName ($_.Name + ".exe")) } |
        Select-Object -First 1
    if (-not $found) {
        throw "dist app folder not found under dist\"
    }
    $DistDir = $found.FullName
    $AppName = $found.Name
}

$ExePath = Join-Path $DistDir ($AppName + ".exe")
if (-not (Test-Path -LiteralPath $ExePath)) {
    $exeHit = Get-ChildItem -LiteralPath $DistDir -Filter "*.exe" | Select-Object -First 1
    if (-not $exeHit) {
        throw "exe not found in: $DistDir"
    }
    $ExePath = $exeHit.FullName
}

$ZipPath = Join-Path $Root ("dist\" + $AppName + "-green.zip")
Write-Host "==> zip for distribution ..."
New-Item -ItemType Directory -Force -Path (Join-Path $Root "dist") | Out-Null
if (Test-Path -LiteralPath $ZipPath) {
    Remove-Item -LiteralPath $ZipPath -Force
}
Compress-Archive -LiteralPath $DistDir -DestinationPath $ZipPath -CompressionLevel Optimal

$exe = Get-Item -LiteralPath $ExePath
$zip = Get-Item -LiteralPath $ZipPath
$dirSize = (Get-ChildItem -LiteralPath $DistDir -Recurse -File | Measure-Object Length -Sum).Sum
$dirMb = [math]::Round($dirSize / 1MB, 1)
$exeMb = [math]::Round($exe.Length / 1MB, 1)
$zipMb = [math]::Round($zip.Length / 1MB, 1)

Write-Host ""
Write-Host "BUILD OK"
Write-Host ("  dir : {0}  ({1} MB)" -f $DistDir, $dirMb)
Write-Host ("  exe : {0}  ({1} MB)" -f $exe.FullName, $exeMb)
Write-Host ("  zip : {0}  ({1} MB)" -f $zip.FullName, $zipMb)
Write-Host "Send the zip to others; do not send exe alone."
