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

a = Analysis(
    ['src/subtitld/__main__.py'],
    pathex=[],
    binaries=binaries_list,
    datas=[
        ('src/subtitld/graphics', 'subtitld/graphics'),
        ('src/subtitld/locale', 'subtitld/locale'),
        ('src/subtitld/ftfy', 'subtitld/ftfy'),
    ],
    hiddenimports=[
        'PySide6.QtCore',
        'PySide6.QtGui', 
        'PySide6.QtWidgets',
        'qframelesswindow'
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
    name='subtitld',
    debug=True,
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
