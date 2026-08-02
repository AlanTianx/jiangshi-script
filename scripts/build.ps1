# Green portable build: deps -> PyInstaller -> zip
# Usage: powershell -ExecutionPolicy Bypass -File scripts\build.ps1
# Or double-click build.bat at repo root.

$ErrorActionPreference = "Stop"

$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root

# Keep same name as main.spec EXE/COLLECT name
$AppName = [string]([char]0x50EB) + [string]([char]0x5C38) + [string]([char]0x52A9) + [string]([char]0x624B)  # 僵尸助手
$ZipSuffix = [string]([char]0x7EFF) + [string]([char]0x8272) + [string]([char]0x7248)  # 绿色版
$VenvPython = Join-Path $Root ".venv\Scripts\python.exe"
$VenvPip = Join-Path $Root ".venv\Scripts\pip.exe"
$Spec = Join-Path $Root "main.spec"
$DistDir = Join-Path $Root ("dist\" + $AppName)
$ZipPath = Join-Path $Root ("dist\" + $AppName + "-" + $ZipSuffix + ".zip")
$TessExe = Join-Path $Root "tesseract\tesseract.exe"
$ChiData = Join-Path $Root "tesseract\tessdata\chi_sim.traineddata"

Write-Host "==> workdir: $Root"

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

$ExePath = Join-Path $DistDir ($AppName + ".exe")
if (-not (Test-Path -LiteralPath $ExePath)) {
    throw "exe not found: $ExePath"
}

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
Write-Host "Send the zip folder to others; do not send exe alone."
