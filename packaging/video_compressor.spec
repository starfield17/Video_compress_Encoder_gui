# -*- mode: python ; coding: utf-8 -*-

from pathlib import Path

from PyInstaller.utils.hooks import collect_submodules


project_root = Path(SPECPATH).parent
assets_root = project_root / "packaging" / "assets"
version_file = project_root / "packaging" / "windows_version_info.txt"


def _default_icon() -> str | None:
    for candidate in (
        assets_root / "app.ico",
        assets_root / "icon.ico",
        assets_root / "app.icns",
        assets_root / "icon.icns",
        assets_root / "app.png",
        assets_root / "icon.png",
    ):
        if candidate.exists():
            return str(candidate)
    return None


datas = [
    (str(project_root / "config"), "config"),
]
hiddenimports = collect_submodules("cli") + collect_submodules("core") + collect_submodules("gui")


a = Analysis(
    [str(project_root / "main.py")],
    pathex=[str(project_root)],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="video-compressor",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=True,
    icon=_default_icon(),
    version=str(version_file) if version_file.exists() else None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name="video-compressor",
)
