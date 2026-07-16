# -*- mode: python ; coding: utf-8 -*-

# Single source of truth for the version string
import os, sys
_root = os.path.abspath(os.path.dirname(SPEC if 'SPEC' in dir() else '.'))
_src = os.path.join(_root, 'src')
sys.path.insert(0, _src)
from picker import __version__ as APP_VERSION

# Bundle the whole bin/ folder (ffmpeg + ffprobe + their av*/sw* DLLs) into a
# "bin" subfolder of the app so video thumbnails / metadata work in the packaged
# build. The exes are dynamically linked, so the DLLs must ship alongside them.
# Missing bin/ → skipped; the app still runs (video features degrade gracefully).
_extra_binaries = []
_bin_dir = os.path.join(_root, 'bin')
if os.path.isdir(_bin_dir):
    for _f in sorted(os.listdir(_bin_dir)):
        _p = os.path.join(_bin_dir, _f)
        if os.path.isfile(_p) and _f.lower().endswith(('.exe', '.dll')):
            _extra_binaries.append((_p, 'bin'))

# Optional assets — tolerate their absence so the spec still builds.
_ver_file = os.path.join(_src, 'version_info.txt')
_version = _ver_file if os.path.isfile(_ver_file) else None
_icon_file = os.path.join(_root, 'icon.ico')
_icon = _icon_file if os.path.isfile(_icon_file) else None

a = Analysis(
    [os.path.join(_src, 'main.py')],
    pathex=[_src],
    binaries=_extra_binaries,
    datas=[],
    hiddenimports=[
        'PyQt6.QtMultimedia',
        'PyQt6.QtMultimediaWidgets',
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        # QtNetwork is required for single-instance IPC (QLocalServer/QLocalSocket).
        'PyQt6.QtQml', 'PyQt6.QtQuick', 'PyQt6.QtQuickWidgets',
        'PyQt6.QtWebEngineCore', 'PyQt6.QtWebEngineWidgets', 'PyQt6.QtWebChannel',
        'PyQt6.QtWebSockets',
        # QtMultimedia + QtMultimediaWidgets are required for video playback (v3.1+).
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

# Strip heavy Qt translations / unused DLLs
def _keep_binary(entry):
    name = entry[0].lower().replace('\\', '/')
    drop = (
        'opengl32sw.dll', 'd3dcompiler_', 'qt6quick', 'qt6qml',
        # qt6network is kept (single-instance IPC).
        'qt6webengine', 'qt6pdf', 'qt6sql', 'qt6bluetooth',
        # NOTE: qt6multimedia DLLs are kept (video playback). Don't add it back here.
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

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name=f'PICker-{APP_VERSION}',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=['vcruntime140.dll', 'python3*.dll', 'Qt6Core.dll', 'Qt6Gui.dll', 'Qt6Widgets.dll', 'ffmpeg.exe', 'ffprobe.exe', 'av*.dll', 'sw*.dll'],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=_icon,
    version=_version,
)
