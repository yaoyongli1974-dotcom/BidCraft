# PyInstaller 打包配置：Linux / Windows / macOS 通用。
#
#   pyinstaller --noconfirm --clean packaging/bidcraft.spec
#
# 产物：
#   Linux   dist/BidCraft/BidCraft
#   Windows dist/BidCraft/BidCraft.exe
#   macOS   dist/BidCraft.app（配合 packaging/build_macos_app.sh）

import sys
from pathlib import Path

ROOT = Path(SPECPATH).resolve().parent

a = Analysis(
    [str(ROOT / "packaging" / "entry.py")],
    pathex=[str(ROOT)],
    binaries=[],
    datas=[(str(ROOT / "bidcraft" / "web_dist"), "bidcraft/web_dist")],
    hiddenimports=[
        "bidcraft.client",
        "bidcraft.desktop",
        "bidcraft.offline_cache",
        "webview",
        "webview.platforms.winforms",
        "webview.platforms.cocoa",
        "webview.platforms.qt",
        "qtpy",
    ],
    hookspath=[],
    runtime_hooks=[],
    excludes=["pytest", "ruff", "tests"],
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="BidCraft",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=True,
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
    name="BidCraft",
)
