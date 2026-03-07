# -*- mode: python ; coding: utf-8 -*-

block_cipher = None

import os
import vosk
import shutil
from glob import glob

vosk_path = os.path.dirname(vosk.__file__)
ffmpeg_path = shutil.which('ffmpeg')

# Get PySide6 path and collect all DLLs
try:
    import PySide6
    pyside6_path = os.path.dirname(PySide6.__file__)
    pyside6_dlls = glob(os.path.join(pyside6_path, '*.dll'))
    # Also get ICU DLLs from Qt bin directory
    qt_bin_path = os.path.join(pyside6_path, 'Qt', 'bin')
    if os.path.exists(qt_bin_path):
        pyside6_dlls.extend(glob(os.path.join(qt_bin_path, '*.dll')))
except:
    pyside6_dlls = []

binaries_list = [
    (os.path.join(vosk_path, 'libvosk.dll'), 'vosk'),
]

for dll in pyside6_dlls:
    binaries_list.append((dll, 'PySide6'))

# Add ICU DLLs from Wine system32
icu_dlls = glob('/root/.wine/drive_c/windows/system32/icu*.dll')
if not icu_dlls:
    icu_dlls = glob('icu/bin64/icu*.dll')
for dll in icu_dlls:
    binaries_list.append((dll, '.'))

if ffmpeg_path:
    binaries_list.append((ffmpeg_path, '.'))

a = Analysis(
    ['subtitld/__main__.py'],
    pathex=[],
    binaries=binaries_list,
    datas=[
        ('subtitld/graphics', 'subtitld/graphics'),
        ('subtitld/locale', 'subtitld/locale'),
        ('subtitld/ftfy', 'subtitld/ftfy'),
    ],
    hiddenimports=[
        'PySide6.QtCore',
        'PySide6.QtGui', 
        'PySide6.QtWidgets',
        'PySide6.QtSvg',
        'qframelesswindow',
        'charset_normalizer',
        'shiboken6',
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=['FixTk', 'tcl', 'tk', '_tkinter', 'tkinter', 'Tkinter'],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='Subtitld',
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
    icon='snap/gui/icon.png',
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='Subtitld',
)
