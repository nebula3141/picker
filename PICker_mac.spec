# -*- mode: python ; coding: utf-8 -*-
# macOS .app bundle. Build on macOS:
#     pyinstaller --noconfirm PICker_mac.spec        # -> dist/PICker.app
# Windows-only binaries (bin/*.exe) are intentionally NOT bundled; on macOS video
# uses a Homebrew/PATH ffmpeg (degrades gracefully if absent).

import os, sys
_root = os.path.abspath(os.path.dirname(SPEC if 'SPEC' in dir() else '.'))
_src = os.path.join(_root, 'src')
sys.path.insert(0, _src)
from picker import __version__ as APP_VERSION

# pillow-heif ships a native HEIF/AVIF decoder as a TOP-LEVEL extension module
# (_pillow_heif.<abi>.so), a sibling of the pillow_heif package that collect_all
# misses — so it is named explicitly in hiddenimports below.
_heif_datas, _heif_binaries, _heif_hidden = [], [], []
try:
    from PyInstaller.utils.hooks import collect_all as _collect_all
    _heif_datas, _heif_binaries, _heif_hidden = _collect_all('pillow_heif')
except Exception:
    pass

# Bundle first-run coach-mark assets under picker/assets/.
_assets_dir = os.path.join(_src, 'picker', 'assets')
_asset_datas = []
if os.path.isdir(_assets_dir):
    for _dp, _dn, _fn in os.walk(_assets_dir):
        for _f in _fn:
            _full = os.path.join(_dp, _f)
            _rel = os.path.relpath(os.path.dirname(_full), _src)
            _asset_datas.append((_full, _rel))

_icon = os.path.join(_root, 'icon.icns')
_icon = _icon if os.path.isfile(_icon) else None

a = Analysis(
    [os.path.join(_src, 'main.py')],
    pathex=[_src],
    binaries=_heif_binaries,
    datas=_heif_datas + _asset_datas,
    hiddenimports=[
        'PyQt6.QtMultimedia',
        'PyQt6.QtMultimediaWidgets',
        '_pillow_heif',          # native HEIF/AVIF decoder (see note above)
        *_heif_hidden,
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        'PyQt6.QtQml', 'PyQt6.QtQuick', 'PyQt6.QtQuickWidgets',
        'PyQt6.QtWebEngineCore', 'PyQt6.QtWebEngineWidgets', 'PyQt6.QtWebChannel',
        'PyQt6.QtPdf', 'PyQt6.QtPdfWidgets', 'PyQt6.QtSql', 'PyQt6.QtTest',
        'PyQt6.QtBluetooth', 'PyQt6.QtSerialPort', 'PyQt6.QtPositioning',
        'PyQt6.QtSensors', 'PyQt6.QtNfc', 'PyQt6.QtCharts',
        'PyQt6.QtDataVisualization', 'PyQt6.QtDesigner', 'PyQt6.QtHelp',
        'tkinter', '_tkinter', 'unittest', 'test', 'tests',
        'pydoc', 'pdb', 'doctest', 'setuptools', 'pip',
        'scipy', 'matplotlib', 'pandas', 'sympy', 'IPython',
        'PIL.ImageQt', 'PIL.ImageTk', 'cv2', 'sklearn', 'torch', 'tensorflow',
    ],
    noarchive=False,
    optimize=2,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='PICker',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=True,      # let macOS "open file with" pass the path as argv
    target_arch=None,         # native arch of the CI runner
    codesign_identity=None,
    entitlements_file=None,
    icon=_icon,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name='PICker',
)

app = BUNDLE(
    coll,
    name='PICker.app',
    icon=_icon,
    bundle_identifier='com.nebula3141.picker',
    version=APP_VERSION,
    info_plist={
        'CFBundleName': 'PICker',
        'CFBundleDisplayName': 'PICker',
        'CFBundleShortVersionString': APP_VERSION,
        'CFBundleVersion': APP_VERSION,
        'NSHighResolutionCapable': True,
        'LSMinimumSystemVersion': '12.0',
        'NSHumanReadableCopyright': 'GPL-3.0 · nebula3141',
        # Register as a viewer for common image types so "Open With" lists PICker.
        'CFBundleDocumentTypes': [{
            'CFBundleTypeName': 'Image',
            'CFBundleTypeRole': 'Viewer',
            'LSItemContentTypes': [
                'public.image', 'public.jpeg', 'public.png', 'public.tiff',
                'public.heic', 'public.heif', 'public.avif', 'public.camera-raw-image',
            ],
        }],
    },
)
