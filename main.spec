# -*- mode: python ; coding: utf-8 -*-
# PyInstaller 打包配置：目录模式，模板图与 exe 同级，便于常见 Windows 直接运行。

import sys
from PyInstaller.utils.hooks import collect_data_files, collect_dynamic_libs

block_cipher = None

# customtkinter 主题/资源；opencv 可能带的动态库
datas = [("image", "image")]
# 便携 Tesseract（若存在则打进绿色包，用户无需再安装）
import os as _os

if _os.path.isdir("tesseract"):
    datas += [("tesseract", "tesseract")]
datas += collect_data_files("customtkinter")
binaries = collect_dynamic_libs("cv2")

hiddenimports = [
    "customtkinter",
    "darkdetect",
    "PIL",
    "PIL.Image",
    "PIL.ImageGrab",
    "cv2",
    "numpy",
    "pytesseract",
    "win32api",
    "win32con",
    "win32gui",
    "win32process",
    "win32com",
    "pythoncom",
    "pywintypes",
    "base",
    "base.Base",
    "base.Controller",
    "base.paths",
    "base.click_probe",
    "base.ocr",
    "ui",
]

a = Analysis(
    ["main.py"],
    pathex=[],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="僵尸助手",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="僵尸助手",
)
