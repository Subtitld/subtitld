# -*- mode: python ; coding: utf-8 -*-

block_cipher = None

import os
import vosk
import shutil
from glob import glob

vosk_path = os.path.dirname(vosk.__file__)
ffmpeg_path = shutil.which('ffmpeg')

# Get PySide6 path and collect all DLLs
#try:
#    import PySide6
#    pyside6_path = os.path.dirname(PySide6.__file__)
#    pyside6_dlls = glob(os.path.join(pyside6_path, '*.dll'))
#    # Also get ICU DLLs from Qt bin directory
#    qt_bin_path = os.path.join(pyside6_path, 'Qt', 'bin')
#    if os.path.exists(qt_bin_path):
#        pyside6_dlls.extend(glob(os.path.join(qt_bin_path, '*.dll')))
#    # Get Qt plugins
#    qt_plugins_path = os.path.join(pyside6_path, 'Qt', 'plugins')
#except:
#    pyside6_dlls = []
#    qt_plugins_path = None

binaries_list = [
    (os.path.join(vosk_path, 'libvosk.dll'), 'vosk'),
]

#for dll in pyside6_dlls:
#    binaries_list.append((dll, 'PySide6'))

# Add ICU DLLs
#if os.path.exists('icuuc.dll'):
#    binaries_list.append(('icuuc.dll', '.'))

#if os.path.exists('icudt73.dll'):
#    binaries_list.append(('icudt73.dll', '.'))
    
if ffmpeg_path:
    binaries_list.append((ffmpeg_path, '.'))

datas_list = [
    ('subtitld/graphics', 'subtitld/graphics'),
    ('subtitld/locale', 'subtitld/locale'),
    ('subtitld/ftfy', 'subtitld/ftfy'),
]

if qt_plugins_path and os.path.exists(qt_plugins_path):
    datas_list.append((os.path.join(qt_plugins_path, 'platforms'), 'PySide6/Qt/plugins/platforms'))

a = Analysis(
    ['subtitld/__main__.py'],
    pathex=[],
    binaries=binaries_list,
    datas=datas_list,
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
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name='Subtitld-Portable',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon='snap/gui/icon.png',
)
