# -*- mode: python ; coding: utf-8 -*-

block_cipher = None

import os
import shutil
from glob import glob

# Vosk used to be bundled here (libvosk.dll at `vosk/`). It's now shipped
# as an external add-on, so the host bundle no longer carries it.
ffmpeg_path = shutil.which('ffmpeg')
ffprobe_path = shutil.which('ffprobe')

binaries_list = []

if ffmpeg_path:
    binaries_list.append((ffmpeg_path, '.'))
if ffprobe_path:
    binaries_list.append((ffprobe_path, '.'))

# libdatachannel, for live co-editing's direct links between collaborators (packaging/libdatachannel builds it):
# next to the app, where modules/rtc.py looks, with its licences. Without it the app still runs; collaborators
# just can't send each other media.
libdatachannel_dir = os.path.join('packaging', 'libdatachannel', 'dist', 'windows-x64')
libdatachannel_datas = []
if os.path.isfile(os.path.join(libdatachannel_dir, 'datachannel.dll')):
    binaries_list.append((os.path.join(libdatachannel_dir, 'datachannel.dll'), '.'))
    libdatachannel_datas.append((os.path.join(libdatachannel_dir, 'licenses'), 'licenses/libdatachannel'))
else:
    print('WARNING: no libdatachannel in', libdatachannel_dir, '- live co-editing will not send media')

datas_list = [
    ('src/subtitld/graphics', 'subtitld/graphics'),
    ('src/subtitld/locale', 'subtitld/locale'),
    ('src/subtitld/ftfy', 'subtitld/ftfy'),
    ('src/subtitld/models', 'subtitld/models'),
]

a = Analysis(
    ['src/subtitld/__main__.py'],
    pathex=[],
    binaries=binaries_list,
    datas=datas_list + libdatachannel_datas,
    hiddenimports=[
        'PySide6.QtCore',
        'PySide6.QtGui', 
        'PySide6.QtWidgets',
        'PySide6.QtWebSockets',
        'PySide6.QtSvg',
        'qframelesswindow',
        'charset_normalizer',
        'shiboken6',
    ],
    hookspath=['packaging/pyinstaller'],
    hooksconfig={},
    runtime_hooks=['packaging/pyinstaller/rthook_stdio.py'],
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
