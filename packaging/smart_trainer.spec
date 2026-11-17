# PyInstaller spec for the macOS app. Build with:  packaging/build_macos.sh
# -*- mode: python ; coding: utf-8 -*-
from importlib.metadata import version
from pathlib import Path

import nicegui
from PyInstaller.utils.hooks import collect_submodules

ROOT = Path(SPECPATH).parent
# Dynamic version (from CHANGELOG.md via hatch), read from the installed package metadata
VERSION = version("smart-trainer")
ICON = ROOT / "packaging" / "icon.icns"

a = Analysis(
    [str(ROOT / "packaging" / "launcher.py")],
    pathex=[str(ROOT)],
    datas=[(str(Path(nicegui.__file__).parent), "nicegui")],
    hiddenimports=[
        *collect_submodules("bleak.backends.corebluetooth"),
        *collect_submodules("smart_trainer"),
        "keyring.backends.macOS",
        "webview.platforms.cocoa",
    ],
    excludes=["tkinter"],
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="Smart Trainer",
    console=False,
    target_arch=None,  # set to "universal2" only with a universal2 Python
    codesign_identity=None,  # signing is done afterwards in build_macos.sh
    entitlements_file=None,
)
coll = COLLECT(exe, a.binaries, a.datas, name="Smart Trainer")
app = BUNDLE(
    coll,
    name="Smart Trainer.app",
    icon=str(ICON) if ICON.exists() else None,
    bundle_identifier="io.github.mighelone.smarttrainer",
    version=VERSION,
    info_plist={
        "CFBundleShortVersionString": VERSION,
        "CFBundleVersion": VERSION,
        "LSMinimumSystemVersion": "12.0",
        "NSHighResolutionCapable": True,
        # Without this macOS terminates the app the first time it touches Bluetooth
        "NSBluetoothAlwaysUsageDescription": (
            "Smart Trainer connects to your smart trainer, power meter and heart-rate strap."
        ),
        "NSLocalNetworkUsageDescription": (
            "Lets you open the ride screen on an iPad or phone on the same network."
        ),
    },
)
