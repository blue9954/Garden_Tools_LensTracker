# -*- mode: python ; coding: utf-8 -*-
from pathlib import Path
import runpy

from PyInstaller.utils.win32.versioninfo import (
    FixedFileInfo, StringFileInfo, StringStruct, StringTable, VarFileInfo, VarStruct, VSVersionInfo,
)

root = Path(SPECPATH)
version = runpy.run_path(str(root / 'lenstracker' / 'version.py'))['VERSION']
version_parts = tuple(int(part) for part in version.split('.'))
if len(version_parts) != 3 or any(not 0 <= part <= 65535 for part in version_parts):
    raise ValueError('VERSION must contain three Windows-compatible numeric components.')
windows_version = version_parts + (0,)
version_info = VSVersionInfo(
    ffi=FixedFileInfo(filevers=windows_version, prodvers=windows_version),
    kids=[
        StringFileInfo([StringTable('040904B0', [
            StringStruct('FileDescription', 'LensTracker'),
            StringStruct('FileVersion', version),
            StringStruct('InternalName', 'LensTracker'),
            StringStruct('OriginalFilename', 'LensTracker.exe'),
            StringStruct('ProductName', 'LensTracker'),
            StringStruct('ProductVersion', version),
        ])]),
        VarFileInfo([VarStruct('Translation', [1033, 1200])]),
    ],
)
a = Analysis(
    ['desktop.py'],
    pathex=[str(root)],
    binaries=[],
    datas=[('templates', 'templates'), ('static', 'static'),
           ('lenstracker/lens_prices.json', 'lenstracker'),
           ('README.md', '.'), ('DESKTOP.md', '.'), ('THIRD_PARTY_NOTICES.md', '.'), ('CHANGELOG.md', '.'),
           ('build/licenses', 'licenses')],
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=['tkinter', 'pytest', 'PySide6.QtQuick', 'PySide6.QtQml',
              'PySide6.Qt3DCore', 'PySide6.QtMultimedia', 'PySide6.QtPdf'],
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name='LensTracker',
          debug=False, bootloader_ignore_signals=False, strip=False, upx=False,
          console=False, icon=str(root / 'static' / 'app.ico'), version=version_info)
coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name='LensTracker')
