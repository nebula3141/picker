"""Detect and launch external editors (Photoshop, Lightroom)."""
import glob
import os
import re
import shlex
import subprocess
import sys

from . import settings as settings_mod

_cache: dict[str, str | None] = {}

try:
    import winreg  # type: ignore
    _HAVE_WINREG = sys.platform == "win32"
except ImportError:
    _HAVE_WINREG = False


def invalidate_cache():
    _cache.clear()


def _ver_key(v: str) -> list[int]:
    return [int(x) for x in re.findall(r"\d+", v)] or [0]


def _registry_adobe_exe(product: str, exe_name: str) -> str | None:
    """Scan HKLM\\SOFTWARE\\Adobe\\<product>\\<ver> for install dir; return path to exe_name."""
    if not _HAVE_WINREG:
        return None
    for hive, view in (
        (winreg.HKEY_LOCAL_MACHINE, winreg.KEY_WOW64_64KEY),
        (winreg.HKEY_LOCAL_MACHINE, winreg.KEY_WOW64_32KEY),
        (winreg.HKEY_CURRENT_USER, 0),
    ):
        try:
            root = winreg.OpenKey(hive, f"SOFTWARE\\Adobe\\{product}",
                                  0, winreg.KEY_READ | view)
        except OSError:
            continue
        versions = []
        i = 0
        while True:
            try:
                versions.append(winreg.EnumKey(root, i))
                i += 1
            except OSError:
                break
        versions.sort(key=_ver_key, reverse=True)
        for v in versions:
            try:
                k = winreg.OpenKey(root, v, 0, winreg.KEY_READ | view)
            except OSError:
                continue
            for name in ("ApplicationPath", "InstallLocation", "InstallPath"):
                try:
                    val, _ = winreg.QueryValueEx(k, name)
                except OSError:
                    continue
                if not val:
                    continue
                cand = val if val.lower().endswith(".exe") else os.path.join(val, exe_name)
                if os.path.isfile(cand):
                    return cand
    return None


def _registry_uninstall_exe(display_prefix: str, exe_name: str) -> str | None:
    """Scan Uninstall keys for DisplayName starting with display_prefix."""
    if not _HAVE_WINREG:
        return None
    uninstall_keys = [
        (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall", winreg.KEY_WOW64_64KEY),
        (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall", winreg.KEY_WOW64_32KEY),
        (winreg.HKEY_CURRENT_USER, r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall", 0),
    ]
    hits: list[tuple[str, str]] = []
    for hive, path, view in uninstall_keys:
        try:
            root = winreg.OpenKey(hive, path, 0, winreg.KEY_READ | view)
        except OSError:
            continue
        i = 0
        while True:
            try:
                sub = winreg.EnumKey(root, i)
                i += 1
            except OSError:
                break
            try:
                k = winreg.OpenKey(root, sub, 0, winreg.KEY_READ | view)
                name, _ = winreg.QueryValueEx(k, "DisplayName")
            except OSError:
                continue
            if not name.lower().startswith(display_prefix.lower()):
                continue
            loc = None
            for n in ("InstallLocation", "DisplayIcon"):
                try:
                    v, _ = winreg.QueryValueEx(k, n)
                    if v:
                        loc = v.split(",")[0].strip('"')
                        break
                except OSError:
                    continue
            if not loc:
                continue
            cand = loc if loc.lower().endswith(".exe") else os.path.join(loc, exe_name)
            if os.path.isfile(cand):
                hits.append((name, cand))
    if not hits:
        return None
    hits.sort(key=lambda h: _ver_key(h[0]), reverse=True)
    return hits[0][1]


def _registry_hkcr_command(progid: str) -> str | None:
    """HKCR\\<progid>\\shell\\open\\command default → parse exe path. Also tries versioned progids (e.g. Photoshop.Image.15)."""
    if not _HAVE_WINREG:
        return None
    candidates = [progid]
    # Enumerate versioned variants: HKCR\Photoshop.Image.NN
    try:
        hkcr = winreg.OpenKey(winreg.HKEY_CLASSES_ROOT, "")
        i = 0
        while True:
            try:
                sub = winreg.EnumKey(hkcr, i)
                i += 1
            except OSError:
                break
            if sub.startswith(progid + "."):
                candidates.append(sub)
    except OSError:
        pass
    candidates.sort(key=_ver_key, reverse=True)
    for pid in candidates:
        try:
            k = winreg.OpenKey(winreg.HKEY_CLASSES_ROOT, f"{pid}\\shell\\open\\command")
            val, _ = winreg.QueryValueEx(k, "")
        except OSError:
            continue
        try:
            parts = shlex.split(val, posix=False)
        except ValueError:
            continue
        if not parts:
            continue
        exe = parts[0].strip('"')
        if os.path.isfile(exe):
            return exe
    return None


def _registry_app_paths(exe_name: str) -> str | None:
    """HKLM/HKCU\\...\\App Paths\\<exe_name> — canonical Windows exe registration."""
    if not _HAVE_WINREG:
        return None
    subkey = f"SOFTWARE\\Microsoft\\Windows\\CurrentVersion\\App Paths\\{exe_name}"
    for hive, view in (
        (winreg.HKEY_LOCAL_MACHINE, winreg.KEY_WOW64_64KEY),
        (winreg.HKEY_LOCAL_MACHINE, winreg.KEY_WOW64_32KEY),
        (winreg.HKEY_CURRENT_USER, 0),
    ):
        try:
            k = winreg.OpenKey(hive, subkey, 0, winreg.KEY_READ | view)
        except OSError:
            continue
        # Default value = full exe path; "Path" value = dir (fallback)
        for name in ("", "Path"):
            try:
                val, _ = winreg.QueryValueEx(k, name)
            except OSError:
                continue
            if not val:
                continue
            cand = val.strip('"')
            if not cand.lower().endswith(".exe"):
                cand = os.path.join(cand, exe_name)
            if os.path.isfile(cand):
                return cand
    return None


def _glob_programfiles(patterns: list[str]) -> str | None:
    roots = [
        os.environ.get("ProgramFiles", r"C:\Program Files"),
        os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)"),
    ]
    for root in roots:
        if not root or not os.path.isdir(root):
            continue
        for pat in patterns:
            matches = sorted(glob.glob(os.path.join(root, pat)), reverse=True)
            if matches:
                return matches[0]
    return None


def _resolve(key: str, finders) -> str | None:
    if key in _cache:
        return _cache[key]
    for fn in finders:
        try:
            p = fn()
        except Exception:
            p = None
        if p and os.path.isfile(p):
            _cache[key] = p
            return p
    _cache[key] = None
    return None


def photoshop_path() -> str | None:
    manual = settings_mod.get("photoshop_path").strip()
    if manual and os.path.isfile(manual):
        return manual
    return _resolve("ps", [
        lambda: _registry_app_paths("Photoshop.exe"),
        lambda: _registry_adobe_exe("Photoshop", "Photoshop.exe"),
        lambda: _registry_uninstall_exe("Adobe Photoshop", "Photoshop.exe"),
        lambda: _registry_hkcr_command("Photoshop.Image"),
        lambda: _registry_hkcr_command("Photoshop.Application"),
        lambda: _glob_programfiles([
            r"Adobe\Adobe Photoshop*\Photoshop.exe",
            r"Adobe\Photoshop*\Photoshop.exe",
        ]),
    ])


def lightroom_path() -> str | None:
    manual = settings_mod.get("lightroom_path").strip()
    if manual and os.path.isfile(manual):
        return manual
    return _resolve("lr", [
        lambda: _registry_app_paths("Lightroom.exe"),
        lambda: _registry_adobe_exe("Lightroom", "Lightroom.exe"),
        lambda: _registry_adobe_exe("Lightroom CC", "Lightroom.exe"),
        lambda: _registry_uninstall_exe("Adobe Lightroom", "Lightroom.exe"),
        lambda: _registry_uninstall_exe("Adobe Photoshop Lightroom", "Lightroom.exe"),
        lambda: _registry_hkcr_command("Lightroom.Catalog"),
        lambda: _glob_programfiles([
            r"Adobe\Adobe Lightroom Classic\Lightroom.exe",
            r"Adobe\Adobe Lightroom CC\Lightroom.exe",
            r"Adobe\Adobe Lightroom*\Lightroom.exe",
        ]),
    ])


def manual_set(which: str, exe_path: str) -> None:
    """Persist a user-chosen editor path and drop the resolve cache."""
    key = "photoshop_path" if which == "photoshop" else "lightroom_path"
    settings_mod.set_value(key, exe_path)
    invalidate_cache()


def resolve_or_prompt(which: str, parent=None) -> str | None:
    """Return the editor exe, auto-detecting first. If detection fails, ask the
    user to locate it once (file picker) and remember the choice. `which` is
    'photoshop' or 'lightroom'. Returns None if unresolved / user cancelled."""
    path = photoshop_path() if which == "photoshop" else lightroom_path()
    if path:
        return path
    # GUI import is lazy so this module stays usable headless.
    from PyQt6.QtWidgets import QFileDialog
    name = "Photoshop" if which == "photoshop" else "Lightroom"
    default_exe = f"{name}.exe"
    if sys.platform == "win32":
        filt = f"{name} ({default_exe});;Programs (*.exe);;All files (*)"
    else:
        filt = "All files (*)"
    exe, _ = QFileDialog.getOpenFileName(
        parent, f"Locate {name} — automatic detection failed", "", filt)
    if not exe or not os.path.isfile(exe):
        return None
    manual_set(which, exe)
    return exe


def open_with(app_path: str, image_path: str) -> str | None:
    try:
        subprocess.Popen([app_path, image_path], close_fds=True)
        return None
    except Exception as e:
        return str(e)


def open_default(image_path: str) -> str | None:
    try:
        if sys.platform == "win32":
            os.startfile(image_path)
        elif sys.platform == "darwin":
            subprocess.Popen(["open", image_path])
        else:
            subprocess.Popen(["xdg-open", image_path])
        return None
    except Exception as e:
        return str(e)
