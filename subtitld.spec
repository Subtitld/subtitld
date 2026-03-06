# -*- mode: python ; coding: utf-8 -*-
from PyInstaller.utils.hooks import collect_data_files, collect_submodules

block_cipher = None

# Collect all PySide6 data and submodules
pyside6_datas = collect_data_files('PySide6')
pyside6_hiddenimports = collect_submodules('PySide6')

a = Analysis(
    ['subtitld/__main__.py'],
    pathex=[],
    binaries=[],
    datas=[
        ('subtitld/graphics', 'subtitld/graphics'),
        ('subtitld/locale', 'subtitld/locale'),
        ('subtitld/ftfy', 'subtitld/ftfy'),
    ] + pyside6_datas,
    hiddenimports=[
        'qframelesswindow',
        'subtitld',
        'subtitld.interface',
        'subtitld.interface.top_bar',
        'subtitld.interface.startscreen',
        'subtitld.interface.productionscreen',
        'subtitld.interface.actionmanager',
        'subtitld.interface.utils',
        'subtitld.interface.translation',
        'subtitld.modules',
        'subtitld.modules.session',
        'subtitld.modules.config',
        'subtitld.modules.file_io',
        'subtitld.modules.shortcuts',
        'subtitld.autosub',
    ] + pyside6_hiddenimports,
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
