"""Windows file association registration and removal.

Registers PICker as a handler for image file types via per-user registry keys
(HKCU\\Software\\Classes). No admin elevation required.
"""
import os
import sys

_HAVE_WINREG = False
try:
    import winreg
    _HAVE_WINREG = sys.platform == "win32"
except ImportError:
    pass

PROG_ID = "PICker.Image"
PROG_DESCRIPTION = "PICker Image File"

IMAGE_EXTENSIONS = [
    ".jpg", ".jpeg", ".png", ".tiff", ".tif", ".webp", ".bmp",
    ".cr2", ".cr3", ".nef", ".arw", ".dng", ".raf", ".orf", ".rw2", ".pef", ".srw",
]

_CLASSES_ROOT = r"Software\Classes"


def _exe_path() -> str:
    if getattr(sys, "frozen", False):
        return sys.executable
    return f'"{sys.executable}" "{os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "main.py"))}"'


def _icon_path() -> str:
    if getattr(sys, "frozen", False):
        return f"{sys.executable},0"
    ico = os.path.join(os.path.dirname(__file__), "..", "..", "icon.ico")
    if os.path.exists(ico):
        return os.path.abspath(ico)
    return f"{sys.executable},0"


def register() -> str | None:
    if not _HAVE_WINREG:
        return "Windows registry not available"
    try:
        exe = _exe_path()
        icon = _icon_path()
        command = f'{exe} "%1"'

        key = winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER,
                                 rf"{_CLASSES_ROOT}\{PROG_ID}", 0,
                                 winreg.KEY_WRITE)
        winreg.SetValueEx(key, "", 0, winreg.REG_SZ, PROG_DESCRIPTION)
        winreg.CloseKey(key)

        key = winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER,
                                 rf"{_CLASSES_ROOT}\{PROG_ID}\DefaultIcon", 0,
                                 winreg.KEY_WRITE)
        winreg.SetValueEx(key, "", 0, winreg.REG_SZ, icon)
        winreg.CloseKey(key)

        key = winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER,
                                 rf"{_CLASSES_ROOT}\{PROG_ID}\shell\open\command", 0,
                                 winreg.KEY_WRITE)
        winreg.SetValueEx(key, "", 0, winreg.REG_SZ, command)
        winreg.CloseKey(key)

        for ext in IMAGE_EXTENSIONS:
            ext_key = winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER,
                                         rf"{_CLASSES_ROOT}\{ext}\OpenWithProgids", 0,
                                         winreg.KEY_WRITE)
            winreg.SetValueEx(ext_key, PROG_ID, 0, winreg.REG_NONE, b"")
            winreg.CloseKey(ext_key)

        _dir_key = winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER,
                                      rf"{_CLASSES_ROOT}\Directory\shell\PICker", 0,
                                      winreg.KEY_WRITE)
        winreg.SetValueEx(_dir_key, "", 0, winreg.REG_SZ, "Browse with PICker")
        winreg.SetValueEx(_dir_key, "Icon", 0, winreg.REG_SZ, icon)
        winreg.CloseKey(_dir_key)

        _dir_cmd = winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER,
                                      rf"{_CLASSES_ROOT}\Directory\shell\PICker\command", 0,
                                      winreg.KEY_WRITE)
        winreg.SetValueEx(_dir_cmd, "", 0, winreg.REG_SZ, f'{exe} "%V"')
        winreg.CloseKey(_dir_cmd)

        bg_key = winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER,
                                    rf"{_CLASSES_ROOT}\Directory\Background\shell\PICker", 0,
                                    winreg.KEY_WRITE)
        winreg.SetValueEx(bg_key, "", 0, winreg.REG_SZ, "Browse with PICker")
        winreg.SetValueEx(bg_key, "Icon", 0, winreg.REG_SZ, icon)
        winreg.CloseKey(bg_key)

        bg_cmd = winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER,
                                    rf"{_CLASSES_ROOT}\Directory\Background\shell\PICker\command", 0,
                                    winreg.KEY_WRITE)
        winreg.SetValueEx(bg_cmd, "", 0, winreg.REG_SZ, f'{exe} "%V"')
        winreg.CloseKey(bg_cmd)

        sfa_key = winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER,
                                     rf"{_CLASSES_ROOT}\SystemFileAssociations\image\shell\PICker", 0,
                                     winreg.KEY_WRITE)
        winreg.SetValueEx(sfa_key, "", 0, winreg.REG_SZ, "Open with PICker")
        winreg.SetValueEx(sfa_key, "Icon", 0, winreg.REG_SZ, icon)
        winreg.CloseKey(sfa_key)

        sfa_cmd = winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER,
                                     rf"{_CLASSES_ROOT}\SystemFileAssociations\image\shell\PICker\command", 0,
                                     winreg.KEY_WRITE)
        winreg.SetValueEx(sfa_cmd, "", 0, winreg.REG_SZ, f'{exe} "%1"')
        winreg.CloseKey(sfa_cmd)

        _notify_shell()
        return None
    except OSError as e:
        return str(e)


def unregister() -> str | None:
    if not _HAVE_WINREG:
        return "Windows registry not available"
    try:
        _delete_tree(winreg.HKEY_CURRENT_USER, rf"{_CLASSES_ROOT}\{PROG_ID}")

        for ext in IMAGE_EXTENSIONS:
            try:
                key = winreg.OpenKeyEx(winreg.HKEY_CURRENT_USER,
                                       rf"{_CLASSES_ROOT}\{ext}\OpenWithProgids", 0,
                                       winreg.KEY_WRITE)
                try:
                    winreg.DeleteValue(key, PROG_ID)
                except FileNotFoundError:
                    pass
                winreg.CloseKey(key)
            except OSError:
                pass

        _delete_tree(winreg.HKEY_CURRENT_USER,
                     rf"{_CLASSES_ROOT}\Directory\shell\PICker")
        _delete_tree(winreg.HKEY_CURRENT_USER,
                     rf"{_CLASSES_ROOT}\Directory\Background\shell\PICker")
        _delete_tree(winreg.HKEY_CURRENT_USER,
                     rf"{_CLASSES_ROOT}\SystemFileAssociations\image\shell\PICker")

        _notify_shell()
        return None
    except OSError as e:
        return str(e)


def is_registered() -> bool:
    if not _HAVE_WINREG:
        return False
    try:
        key = winreg.OpenKeyEx(winreg.HKEY_CURRENT_USER,
                               rf"{_CLASSES_ROOT}\{PROG_ID}\shell\open\command",
                               0, winreg.KEY_READ)
        winreg.CloseKey(key)
        return True
    except OSError:
        return False


def _delete_tree(hive, subkey: str):
    try:
        key = winreg.OpenKeyEx(hive, subkey, 0,
                               winreg.KEY_READ | winreg.KEY_WRITE)
    except OSError:
        return
    while True:
        try:
            child = winreg.EnumKey(key, 0)
            _delete_tree(hive, rf"{subkey}\{child}")
        except OSError:
            break
    winreg.CloseKey(key)
    try:
        winreg.DeleteKey(hive, subkey)
    except OSError:
        pass


def _notify_shell():
    try:
        import ctypes
        SHCNE_ASSOCCHANGED = 0x08000000
        SHCNF_IDLIST = 0x0000
        ctypes.windll.shell32.SHChangeNotify(
            SHCNE_ASSOCCHANGED, SHCNF_IDLIST, None, None
        )
    except Exception:
        pass
