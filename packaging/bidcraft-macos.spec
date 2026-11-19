# macOS 专用 spec：生成 BidCraft.app（无控制台窗口）。
#
#   pyinstaller --noconfirm --clean packaging/bidcraft-macos.spec

import os
from pathlib import Path

ROOT = Path(SPECPATH).resolve().parent

a = Analysis(
    [str(ROOT / "packaging" / "entry.py")],
    pathex=[str(ROOT)],
    binaries=[],
    datas=[(str(ROOT / "bidcraft" / "web_dist"), "bidcraft/web_dist")],
    hiddenimports=["bidcraft.client", "bidcraft.desktop"],
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
    name="BidCraft",
)
app = BUNDLE(
    coll,
    name="BidCraft.app",
    icon=None,
    bundle_identifier="com.bidcraft.desktop",
    info_plist={
        "CFBundleName": "BidCraft",
        "CFBundleDisplayName": "BidCraft 投标工作台",
        "CFBundleShortVersionString": os.environ.get("VERSION", "0.6.1"),
        "NSHighResolutionCapable": True,
    },
)
