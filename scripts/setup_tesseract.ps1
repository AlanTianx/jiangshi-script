# 下载便携 Tesseract + 中文语言包到项目 tesseract/ 目录（绿色分发用）
# 用法（在项目根目录）: powershell -ExecutionPolicy Bypass -File scripts\setup_tesseract.ps1

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
$TessRoot = Join-Path $Root "tesseract"
$TessData = Join-Path $TessRoot "tessdata"
New-Item -ItemType Directory -Force -Path $TessData | Out-Null

Write-Host "==> 下载中文/英文语言包..."
$chi = "https://github.com/tesseract-ocr/tessdata/raw/main/chi_sim.traineddata"
$eng = "https://github.com/tesseract-ocr/tessdata/raw/main/eng.traineddata"
Invoke-WebRequest -Uri $chi -OutFile (Join-Path $TessData "chi_sim.traineddata") -UseBasicParsing
Invoke-WebRequest -Uri $eng -OutFile (Join-Path $TessData "eng.traineddata") -UseBasicParsing

# 官方 Windows 安装包（静默装到本地目录较难）；优先检测系统是否已安装并复制
$sys = @(
    "C:\Program Files\Tesseract-OCR",
    "C:\Program Files (x86)\Tesseract-OCR"
) | Where-Object { Test-Path (Join-Path $_ "tesseract.exe") } | Select-Object -First 1

if ($sys) {
    Write-Host "==> 从系统安装复制引擎: $sys"
    Copy-Item -Force (Join-Path $sys "tesseract.exe") $TessRoot
    Get-ChildItem $sys -Filter "*.dll" | Copy-Item -Destination $TessRoot -Force
    if (Test-Path (Join-Path $sys "tessdata\chi_sim.traineddata")) {
        Copy-Item -Force (Join-Path $sys "tessdata\chi_sim.traineddata") $TessData
    }
    Write-Host "完成: $TessRoot"
    exit 0
}

Write-Host @"

语言包已就绪: $TessData

未检测到系统 Tesseract。请任选其一：
1) 安装 https://github.com/UB-Mannheim/tesseract/wiki 后重新运行本脚本（会自动复制到 tesseract/）
2) 手动把 tesseract.exe 与依赖 DLL 放到: $TessRoot

绿色打包时请把整个 tesseract/ 目录与 exe 一起分发。
"@
exit 1
