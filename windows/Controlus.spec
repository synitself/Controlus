# -*- mode: python ; coding: utf-8 -*-
# PyInstaller spec for Controlus (Windows).
#
# One-folder build on purpose: a one-file exe re-extracts ~50 MB to %TEMP% on
# every launch and runs as two processes, which matters for an app that sits
# in the tray from logon.

block_cipher = None

a = Analysis(
    ['main.py'],
    pathex=['.'],
    binaries=[],
    datas=[],
    hiddenimports=['hid', 'openrgb', 'openrgb.utils', 'PySide6.QtNetwork'],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=['tkinter', 'unittest', 'pydoc', 'PySide6.QtQml', 'PySide6.QtQuick',
              'PySide6.QtPdf', 'PySide6.QtOpenGL', 'PySide6.QtSvg'],
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
    name='Controlus',
    icon='assets/controlus.ico',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=False,
    name='Controlus',
)
