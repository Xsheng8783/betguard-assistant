# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for Betguard Assistant one-folder bundle.

Bundles Playwright Chromium (excluding headless_shell to avoid MAX_PATH).
"""
import os
import glob as _glob

from PyInstaller.utils.hooks import collect_data_files, collect_dynamic_libs

ROOT = os.path.abspath(".")
block_cipher = None

# Bundled Chromium — exclude headless_shell (deep paths cause MAX_PATH errors)
_pw = os.path.join(ROOT, "build", "playwright-browsers")
_datas = [(os.path.join(ROOT, "src", "betguard"), "betguard")]

# OCR: bundle RapidOCR ONNX models + onnxruntime native libs
_datas += collect_data_files("rapidocr_onnxruntime", includes=["**/*.onnx", "**/*.yaml", "**/*.yml"])
_binaries = collect_dynamic_libs("onnxruntime")

# Manually add only the directories we need
for _entry in sorted(os.listdir(_pw)):
    _src = os.path.join(_pw, _entry)
    if "headless" in _entry.lower():
        continue  # skip chromium_headless_shell
    if os.path.isdir(_src):
        _datas.append((_src, os.path.join("playwright-browsers", _entry)))

a = Analysis(
    [os.path.join(ROOT, "src", "betguard", "launcher.py")],
    pathex=[os.path.join(ROOT, "src")],
    binaries=_binaries,
    datas=_datas,
    hiddenimports=[
        "betguard.webui.app",
        "betguard.ocr",
        "betguard.parser",
        "betguard.validator",
        "betguard.webfill.web_assist_session",
        "betguard.webfill.real_site_assisted_fill",
        "betguard.webfill.review_console",
        "betguard.webfill.batch_mock_queue",
        "betguard.webfill.manual_reparse",
        "betguard.webfill.web_assisted_fill_executor",
        "betguard.webfill.zhu_peng_fill",
        "betguard.webfill.zhu_peng_pipeline",
        "betguard.webfill.zhu_peng_session",
        "betguard.review",
        "betguard.models",
        "betguard.user_data",
        "betguard.license",
        "betguard.build_info",
        "playwright",
        "playwright.sync_api",
    ],
    hookspath=[],
    runtime_hooks=[],
    excludes=["tests", "test", "pytest", "pip", "setuptools", "tkinter", "unittest",
              "playwright.async_api", "playwright.driver"],
    win_no_prefer_redirects=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="BetguardAssistant",
    debug=False,
    strip=False,
    upx=True,
    console=False,
             icon=os.path.join(ROOT, 'installer', 'Betguard.ico'),
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=["*.pak", "*.dat"],
    name="BetguardAssistant",
)