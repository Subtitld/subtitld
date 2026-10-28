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

# libdatachannel, for live co-editing's direct links between collaborators (packaging/libdatachannel builds it):
# next to the app, where modules/rtc.py looks, with its licences. Without it the app still runs; collaborators
# just can't send each other media.
libdatachannel_dir = os.path.join('packaging', 'libdatachannel', 'dist', 'macos-arm64')
libdatachannel_datas = []
if os.path.isfile(os.path.join(libdatachannel_dir, 'libdatachannel.dylib')):
    binaries_list.append((os.path.join(libdatachannel_dir, 'libdatachannel.dylib'), '.'))
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
        # Contents/Resources/usfx.icns: the .usfx document type's icon.
        ('packaging/macos/usfx.icns', '.'),
    ] + libdatachannel_datas,
    hiddenimports=[
        'PySide6.QtCore',
        'PySide6.QtGui',
        'PySide6.QtWidgets',
        'PySide6.QtWebSockets',
        'PySide6.QtSvg',
        'PySide6.QtMultimedia',
        'PySide6.QtMultimediaWidgets',
        'qframelesswindow',
        'charset_normalizer',
        'shiboken6',
    ],
    hookspath=['packaging/pyinstaller'],
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
        # Subtitld's projects are its own; subtitles open in it too, without
        # taking them from the app that opens them now.
        'CFBundleDocumentTypes': [
            {
                'CFBundleTypeName': 'Subtitld project',
                'CFBundleTypeRole': 'Editor',
                'CFBundleTypeIconFile': 'usfx.icns',
                'LSItemContentTypes': ['org.subtitld.usfx'],
                'LSHandlerRank': 'Owner',
            },
            {
                'CFBundleTypeExtensions': ['srt', 'vtt', 'webvtt', 'ass', 'ssa', 'smi', 'sami', 'sub', 'sbv', 'ttml',
                                           'dfxp', 'scc', 'mcc', 'usf'],
                'CFBundleTypeName': 'Subtitles',
                'CFBundleTypeRole': 'Editor',
                'LSHandlerRank': 'Alternate',
            },
        ],
        # subtitld:// links, subtitld.cc's "Open in Subtitld" for a shared project:
        # macOS sends them to the app as it does files (__main__.FileOpenFilter).
        'CFBundleURLTypes': [
            {'CFBundleURLName': 'org.subtitld.link', 'CFBundleURLSchemes': ['subtitld']},
        ],
        'UTExportedTypeDeclarations': [
            {
                'UTTypeIdentifier': 'org.subtitld.usfx',
                'UTTypeDescription': 'Subtitld project',
                'UTTypeConformsTo': ['public.zip-archive', 'public.data'],
                'UTTypeIconFile': 'usfx.icns',
                'UTTypeTagSpecification': {'public.filename-extension': ['usfx'],
                                           'public.mime-type': ['application/x-usfx']},
            },
        ],
    },
)
