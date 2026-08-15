"""Build PICker as a multi-file (onedir) bundle via PyInstaller.

Output: dist/PICker-<version>/  containing PICker-<version>.exe + DLLs.
Faster startup than --onefile (no temp extraction), easier antivirus
whitelisting, ship as a zipped folder.
"""
import os
import subprocess
import sys

from PyQt6.QtWidgets import QApplication

# Qt must init before pixmap ops
_app = QApplication.instance() or QApplication(sys.argv)

from picker import __version__, __version_info__
from picker.icon import export_ico

ROOT = os.path.dirname(os.path.abspath(__file__))
_REPO = os.path.dirname(ROOT)
ICO = os.path.join(ROOT, "icon.ico")
VER_FILE = os.path.join(ROOT, "version_info.txt")
# Canonical spec lives at the repo root (bundles pillow-heif's native decoder,
# the coach-mark assets, and names the exe "PICker.exe" to match the installer).
SPEC = os.path.join(_REPO, "PICker_multifile.spec")

export_ico(ICO)
print(f"wrote {ICO}")

# Regenerate version resource so the exe metadata matches picker.__version__.
filevers = ", ".join(str(n) for n in __version_info__)
exe_name = f"PICker-{__version__}.exe"
version_block = f"""VSVersionInfo(
  ffi=FixedFileInfo(
    filevers=({filevers}),
    prodvers=({filevers}),
    mask=0x3f, flags=0x0, OS=0x40004, fileType=0x1, subtype=0x0, date=(0, 0),
  ),
  kids=[
    StringFileInfo([
      StringTable('040904B0', [
        StringStruct('CompanyName', 'PICker'),
        StringStruct('FileDescription', 'PICker {__version__} - photo library & sorter'),
        StringStruct('FileVersion', '{__version__}'),
        StringStruct('InternalName', 'PICker'),
        StringStruct('OriginalFilename', '{exe_name}'),
        StringStruct('ProductName', 'PICker'),
        StringStruct('ProductVersion', '{__version__}'),
      ])
    ]),
    VarFileInfo([VarStruct('Translation', [1033, 1200])])
  ]
)
"""
with open(VER_FILE, "w", encoding="utf-8") as f:
    f.write(version_block)
print(f"wrote {VER_FILE}  (version {__version__})")

cmd = [
    sys.executable, "-m", "PyInstaller",
    SPEC, "--clean", "--noconfirm",
]
print(">", " ".join(cmd))
subprocess.check_call(cmd, cwd=ROOT)

print()
print(f"Output folder: dist/PICker-{__version__}/")
print(f"Run:           dist/PICker-{__version__}/{exe_name}")
