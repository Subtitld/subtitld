# -*- mode: python ; coding: utf-8 -*-

block_cipher = None

import os
import shutil

# Vosk used to be bundled here as a built-in ASR provider — `libvosk.so`
# would ship inside the binary at `vosk/`. It's now an external add-on,
# so the host binary no longer carries it. Users who want offline
# transcription install it via the AddonsPanel.
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
libdatachannel_dir = os.path.join('packaging', 'libdatachannel', 'dist', 'linux-x86_64')
libdatachannel_datas = []
if os.path.isfile(os.path.join(libdatachannel_dir, 'libdatachannel.so')):
    binaries_list.append((os.path.join(libdatachannel_dir, 'libdatachannel.so'), '.'))
    libdatachannel_datas.append((os.path.join(libdatachannel_dir, 'licenses'), 'licenses/libdatachannel'))
else:
    print('WARNING: no libdatachannel in', libdatachannel_dir, '- live co-editing will not send media')

a = Analysis(
    ['src/subtitld/__main__.py'],
    pathex=[],
    binaries=binaries_list,
    datas=[
        ('src/subtitld/graphics', 'subtitld/graphics'),
        ('src/subtitld/locale', 'subtitld/locale'),
        ('src/subtitld/subtitle_styles', 'subtitld/subtitle_styles'),
        ('src/subtitld/ftfy', 'subtitld/ftfy'),
        ('src/subtitld/models', 'subtitld/models'),
    ] + libdatachannel_datas,
    hiddenimports=[
        'PySide6.QtCore',
        'PySide6.QtGui', 
        'PySide6.QtWidgets',
        'PySide6.QtWebSockets',
        'qframelesswindow'
    ],
    hookspath=['packaging/pyinstaller'],
    hooksconfig={},
    runtime_hooks=['packaging/pyinstaller/rthook_portaudio.py'],
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
    name='subtitld',
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
    name='subtitld',
)
