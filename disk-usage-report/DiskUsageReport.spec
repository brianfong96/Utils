# -*- mode: python ; coding: utf-8 -*-

from pathlib import Path


project_directory = Path(SPECPATH)

analysis = Analysis(
    [str(project_directory / "desktop_app.py")],
    pathex=[str(project_directory)],
    binaries=[],
    datas=[(str(project_directory / "web"), "web")],
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=1,
)
python_archive = PYZ(analysis.pure)

executable = EXE(
    python_archive,
    analysis.scripts,
    analysis.binaries,
    analysis.datas,
    [],
    name="DiskUsageReport",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=str(project_directory / "assets" / "app-icon.ico"),
    version=str(project_directory / "version_info.txt"),
)
