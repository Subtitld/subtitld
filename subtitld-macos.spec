# -*- mode: python ; coding: utf-8 -*-

block_cipher = None

import os
import shutil

# Vosk used to be bundled here (libvosk.dyld at `vosk/`). It's now shipped
# as an external add-on, so the host bundle no longer carries it.

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
        'PySide6.QtSvg',
        'PySide6.QtMultimedia',
        'PySide6.QtMultimediaWidgets',
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
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=True,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon='src/subtitld/graphics/subtitld.png',
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name='Subtitld',
)

app = BUNDLE(
    coll,
    name='Subtitld.app',
    icon='src/subtitld/graphics/subtitld.png',
    bundle_identifier='org.subtitld.Subtitld',
    version='26.03.0.0',
    info_plist={
        'NSHighResolutionCapable': 'True',
        'NSPrincipalClass': 'NSApplication',
        'CFBundleName': 'Subtitld',
        'CFBundleDisplayName': 'Subtitld',
        'CFBundleShortVersionString': '26.03.0.0',
        'CFBundleVersion': '26.03.0.0',
        'LSApplicationCategoryType': 'public.app-category.video',
        'CFBundleDocumentTypes': [
            {
                'CFBundleTypeExtensions': ['srt', 'vtt', 'ass', 'ssa', 'subtitld'],
                'CFBundleTypeName': 'Subtitle',
                'CFBundleTypeRole': 'Editor',
                'LSHandlerRank': 'Owner',
            }
        ],
    },
)
