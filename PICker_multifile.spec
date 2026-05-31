# -*- mode: python ; coding: utf-8 -*-
# Onedir (multi-file) variant of PICker.spec.
# Same excludes/binary-filter logic; emits dist/PICker-<ver>/ folder
# with the EXE alongside DLLs and Python runtime.

import os, sys
sys.path.insert(0, os.path.abspath(os.path.dirname(SPEC if 'SPEC' in dir() else '.')))
from picker import __version__ as APP_VERSION

a = Analysis(
    ['main.py'],
    pathex=[],
    binaries=[],
    datas=[],
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        # QtNetwork kept for single-instance IPC.
        'PyQt6.QtQml', 'PyQt6.QtQuick', 'PyQt6.QtQuickWidgets',
        'PyQt6.QtWebEngineCore', 'PyQt6.QtWebEngineWidgets', 'PyQt6.QtWebChannel',
        'PyQt6.QtWebSockets', 'PyQt6.QtMultimedia', 'PyQt6.QtMultimediaWidgets',
        'PyQt6.QtPdf', 'PyQt6.QtPdfWidgets', 'PyQt6.QtSql', 'PyQt6.QtTest',
        'PyQt6.QtBluetooth', 'PyQt6.QtSerialPort', 'PyQt6.QtPositioning',
        'PyQt6.QtSensors', 'PyQt6.QtNfc', 'PyQt6.QtDesigner', 'PyQt6.QtCharts',
        'PyQt6.QtDataVisualization', 'PyQt6.Qt3DCore', 'PyQt6.Qt3DRender',
        'PyQt6.Qt3DInput', 'PyQt6.Qt3DLogic', 'PyQt6.Qt3DAnimation',
        'PyQt6.Qt3DExtras', 'PyQt6.QtRemoteObjects', 'PyQt6.QtHelp',
        'PyQt6.QtOpenGL', 'PyQt6.QtOpenGLWidgets', 'PyQt6.QtSpatialAudio',
        'PyQt6.QtDBus', 'PyQt6.QtXml', 'PyQt6.QtSvg', 'PyQt6.QtSvgWidgets',
        'PyQt6.QtPrintSupport', 'PyQt6.QtConcurrent', 'PyQt6.QtStateMachine',
        'PyQt6.QtTextToSpeech', 'PyQt6.QtWebView',
        'tkinter', '_tkinter', 'Tkinter', 'unittest', 'test', 'tests',
        'pydoc', 'pdb', 'doctest', 'distutils', 'setuptools', 'pip',
        'email', 'http', 'xmlrpc', 'pyexpat', 'lib2to3', 'curses',
        'scipy', 'matplotlib', 'pandas', 'sympy', 'IPython', 'jedi',
        'PIL.ImageQt', 'PIL.ImageTk',
        'cv2', 'sklearn', 'torch', 'tensorflow',
    ],
    noarchive=False,
    optimize=2,
)


def _keep_binary(entry):
    name = entry[0].lower().replace('\\', '/')
    drop = (
        'opengl32sw.dll', 'd3dcompiler_', 'qt6quick', 'qt6qml',
        # qt6network kept for single-instance IPC.
        'qt6webengine', 'qt6multimedia', 'qt6pdf', 'qt6sql', 'qt6bluetooth',
        'qt6sensors', 'qt6nfc', 'qt6charts', 'qt6datavisualization',
        'qt63d', 'qt6remoteobjects', 'qt6help', 'qt6opengl', 'qt6designer',
        'qt6test', 'qt6dbus', 'qt6serialport', 'qt6positioning',
        'qt6spatialaudio', 'qt6texttospeech', 'qt6webview', 'qt6websockets',
        'qt6webchannel', 'qt6statemachine', 'qt6concurrent', 'qt6printsupport',
        'qt6xml',
    )
    return not any(d in name for d in drop)


def _keep_data(entry):
    name = entry[0].lower().replace('\\', '/')
    if name.startswith('pyqt6/qt6/translations/'):
        return False
    if name.startswith('pyqt6/qt6/qml/'):
        return False
    if '/qtwebengine' in name:
        return False
    return True


a.binaries = [b for b in a.binaries if _keep_binary(b)]
a.datas = [d for d in a.datas if _keep_data(d)]

pyz = PYZ(a.pure)

# Onedir: EXE bundles the bootstrapper only; COLLECT lays out the runtime tree.
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name=f'PICker-{APP_VERSION}',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=['vcruntime140.dll', 'python3*.dll', 'Qt6Core.dll', 'Qt6Gui.dll', 'Qt6Widgets.dll'],
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=['E:\\Proj\\Self\\Picker\\icon.ico'],
    version='version_info.txt',
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=['vcruntime140.dll', 'python3*.dll', 'Qt6Core.dll', 'Qt6Gui.dll', 'Qt6Widgets.dll'],
    name=f'PICker-{APP_VERSION}',
)
