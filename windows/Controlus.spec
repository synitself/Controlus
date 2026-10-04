# -*- mode: python ; coding: utf-8 -*-
# PyInstaller spec for Controlus (Windows).
#
# One-folder build on purpose: a one-file exe re-extracts ~50 MB to %TEMP% on
# every launch and runs as two processes, which matters for an app that sits
# in the tray from logon.

import os

block_cipher = None

a = Analysis(
    ['main.py'],
    pathex=['.', '../common'],
    binaries=[],
    datas=[],
    hiddenimports=['controlus_backend', 'hid', 'openrgb', 'openrgb.utils', 'PySide6.QtNetwork'],
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

# Qt drags in modules a widgets-only tray app never loads; dropping them takes
# the folder from ~120 MB to well under half that.
_UNUSED = ('opengl32sw', 'qt6quick', 'qt6qml', 'qt6pdf', 'qt6opengl', 'qt6virtualkeyboard',
           'qpdf', 'qtvirtualkeyboard', 'libcrypto', 'libssl', '_ssl', 'qtquick', 'qtqml')
a.binaries = [b for b in a.binaries
              if not any(tag in os.path.basename(b[0]).lower() for tag in _UNUSED)]
a.datas = [d for d in a.datas if 'translations' not in d[0].lower()]

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
