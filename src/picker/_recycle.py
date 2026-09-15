"""Move files to the OS trash/recycle bin.

- Windows: Recycle Bin via SHFileOperationW (no dependency).
- macOS: Finder Trash via a native ``NSFileManager.trashItemAtURL`` call
  (through ``osascript``/``Foundation`` when available), else send2trash.
- Linux: XDG trash via send2trash.

Trashing is recoverable; we NEVER silently permanent-delete as a fallback for a
"send to trash" action — if we can't move it to the trash we return False so the
caller can tell the user, rather than destroying the file.
"""
import os
import sys
import subprocess


def _recycle_windows(path: str) -> bool:
    import ctypes
    from ctypes import wintypes

    class SHFILEOPSTRUCTW(ctypes.Structure):
        _fields_ = [
            ("hwnd", wintypes.HWND),
            ("wFunc", ctypes.c_uint),
            ("pFrom", ctypes.c_wchar_p),
            ("pTo", ctypes.c_wchar_p),
            ("fFlags", ctypes.c_ushort),
            ("fAnyOperationsAborted", wintypes.BOOL),
            ("hNameMappings", ctypes.c_void_p),
            ("lpszProgressTitle", ctypes.c_wchar_p),
        ]

    FO_DELETE = 3
    FOF_ALLOWUNDO = 0x0040
    FOF_NOCONFIRMATION = 0x0010
    FOF_SILENT = 0x0004

    fileop = SHFILEOPSTRUCTW()
    fileop.hwnd = None
    fileop.wFunc = FO_DELETE
    fileop.pFrom = path + "\0"
    fileop.pTo = None
    fileop.fFlags = FOF_ALLOWUNDO | FOF_NOCONFIRMATION | FOF_SILENT
    fileop.fAnyOperationsAborted = False
    fileop.hNameMappings = None
    fileop.lpszProgressTitle = None

    result = ctypes.windll.shell32.SHFileOperationW(ctypes.byref(fileop))
    return result == 0


def _trash_macos_native(path: str) -> bool:
    """Move to Trash via Foundation (PyObjC) — present in most bundled builds."""
    try:
        from Foundation import NSFileManager, NSURL  # type: ignore
        url = NSURL.fileURLWithPath_(path)
        ok, _, _ = NSFileManager.defaultManager().trashItemAtURL_resultingItemURL_error_(
            url, None, None)
        return bool(ok)
    except Exception:
        return False


def _trash_macos_osascript(path: str) -> bool:
    """Move to Trash via Finder AppleScript — no Python dependency."""
    try:
        script = (
            'on run argv\n'
            '  set p to POSIX file (item 1 of argv)\n'
            '  tell application "Finder" to delete p\n'
            'end run'
        )
        subprocess.run(["osascript", "-e", script, path],
                       check=True, capture_output=True, timeout=15)
        return not os.path.exists(path)
    except Exception:
        return False


def _trash_send2trash(path: str) -> bool:
    try:
        from send2trash import send2trash  # type: ignore
        send2trash(path)
        return True
    except Exception:
        return False


def send_to_recycle_bin(path: str) -> bool:
    """Move ``path`` to the OS trash. Returns True on success, False if it could
    not be trashed (the file is left untouched — never permanently deleted)."""
    path = os.path.abspath(path)
    if not os.path.exists(path):
        return False

    if sys.platform == "win32":
        try:
            return _recycle_windows(path)
        except Exception:
            return _trash_send2trash(path)

    if sys.platform == "darwin":
        # Native first (fast, no subprocess), then Finder, then send2trash.
        return (_trash_macos_native(path)
                or _trash_send2trash(path)
                or _trash_macos_osascript(path))

    # Linux / other Unix — XDG trash.
    return _trash_send2trash(path)
