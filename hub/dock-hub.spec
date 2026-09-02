# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for Shoreting Hub (windowed + tray)."""

from __future__ import annotations

from pathlib import Path

from PyInstaller.utils.hooks import collect_all, collect_data_files

ROOT = Path(SPECPATH)
ASSETS = ROOT / "dock_hub" / "assets"

hm_datas, hm_binaries, hm_hidden = collect_all("HardwareMonitor")
pn_datas, pn_binaries, pn_hidden = collect_all("clr_loader")
pystray_datas, pystray_binaries, pystray_hidden = collect_all("pystray")

datas = [
    *hm_datas,
    *pn_datas,
    *pystray_datas,
    *collect_data_files("HardwareMonitor"),
    *collect_data_files("certifi"),
    (str(ASSETS / "icon.png"), "."),
    (str(ASSETS / "icon.ico"), "."),
    (str(ASSETS / "setup.html"), "."),
    (str(ASSETS / "chats.html"), "."),
    (str(ROOT / "hub.yaml.example"), "."),
]
binaries = [*hm_binaries, *pn_binaries, *pystray_binaries]
hiddenimports = [
    *hm_hidden,
    *pn_hidden,
    *pystray_hidden,
    "certifi",
    "pythonnet",
    "clr",
    "PIL",
    "PIL.Image",
    "pystray._win32",
    "mijiaAPI",
    "yaml",
    "psutil",
    "pynvml",
    "dock_hub.media",
    "dock_hub.reporter",
]

a = Analysis(
    [str(ROOT / "dock_hub_entry.py")],
    pathex=[str(ROOT)],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[str(ROOT / "pyi_rth_certifi.py")],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="DockHub",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=str(ASSETS / "icon.ico"),
    uac_admin=False,
)
