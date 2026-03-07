# -*- mode: python ; coding: utf-8 -*-

block_cipher = None

import os
import vosk
import shutil

vosk_path = os.path.dirname(vosk.__file__)
ffmpeg_path = shutil.which('ffmpeg')

a = Analysis(
    ['subtitld/__main__.py'],
    pathex=[],
    binaries=[
        (os.path.join(vosk_path, 'libvosk.dll'), 'vosk'),
        (ffmpeg_path, '.'),
    ],
    datas=[
        ('subtitld/graphics', 'subtitld/graphics'),
        ('subtitld/locale', 'subtitld/locale'),
        ('subtitld/ftfy', 'subtitld/ftfy'),
    ],
    hiddenimports=[
        'PySide6.QtCore',
        'PySide6.QtGui', 
        'PySide6.QtWidgets',
        'qframelesswindow',
        'charset_normalizer',
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
