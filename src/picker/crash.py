"""Crash reporting — persist tracebacks to disk, detect on next launch."""
import os
import platform
import sys
import time
import traceback
from pathlib import Path


_MAX_CRASH_FILES = 20


def _crash_dir() -> Path:
    try:
        from . import settings as settings_mod
        if settings_mod.is_portable():
            d = settings_mod.portable_dir() / "crash-logs"
            d.mkdir(parents=True, exist_ok=True)
            return d
    except Exception:
        pass
    if os.name == "nt":
        base = Path(os.environ.get("APPDATA", Path.home()))
    else:
        base = Path.home() / ".config"
    d = base / "PICker" / "crash-logs"
    try:
        d.mkdir(parents=True, exist_ok=True)
    except Exception:
        pass
    return d


def write_crash(exc_type, exc_value, tb) -> str | None:
    try:
        d = _crash_dir()
        ts = time.strftime("%Y%m%d-%H%M%S")
        path = d / f"crash-{ts}.txt"
        text = _format_report(exc_type, exc_value, tb)
        path.write_text(text, encoding="utf-8")
        _evict_old(d)
        return str(path)
    except Exception as e:
        try:
            print(f"[PICker] crash report write failed: {e}", file=sys.stderr)
        except Exception:
            pass
        return None


def _format_report(exc_type, exc_value, tb) -> str:
    from picker import __version__
    lines = [
        f"PICker crash report",
        f"{'=' * 60}",
        f"Time:     {time.strftime('%Y-%m-%d %H:%M:%S')}",
        f"Version:  {__version__}",
        f"Python:   {sys.version}",
        f"Platform: {platform.platform()}",
        f"Frozen:   {getattr(sys, 'frozen', False)}",
    ]
    try:
        from PyQt6.QtCore import qVersion
        lines.append(f"Qt:       {qVersion()}")
    except Exception:
        pass
    lines.append("")
    lines.append("Traceback:")
    lines.append("-" * 60)
    lines.extend(traceback.format_exception(exc_type, exc_value, tb))
    return "\n".join(lines)


def _evict_old(d: Path):
    try:
        files = sorted(d.glob("crash-*.txt"), key=lambda p: p.stat().st_mtime)
        while len(files) > _MAX_CRASH_FILES:
            files.pop(0).unlink()
    except Exception:
        pass


def last_crash() -> str | None:
    try:
        d = _crash_dir()
        files = sorted(d.glob("crash-*.txt"), key=lambda p: p.stat().st_mtime)
        if files:
            return files[-1].read_text(encoding="utf-8")
    except Exception:
        pass
    return None


def last_crash_path() -> Path | None:
    try:
        d = _crash_dir()
        files = sorted(d.glob("crash-*.txt"), key=lambda p: p.stat().st_mtime)
        if files:
            return files[-1]
    except Exception:
        pass
    return None


def clear_last_crash():
    p = last_crash_path()
    if p:
        try:
            p.unlink()
        except OSError:
            pass


def diagnostics() -> str:
    from picker import __version__
    lines = [
        f"PICker {__version__}",
        f"Python {sys.version}",
        f"Platform: {platform.platform()}",
        f"Frozen: {getattr(sys, 'frozen', False)}",
    ]
    try:
        from PyQt6.QtCore import qVersion
        lines.append(f"Qt: {qVersion()}")
    except Exception:
        pass
    try:
        import numpy
        lines.append(f"NumPy: {numpy.__version__}")
    except Exception:
        pass
    try:
        import PIL
        lines.append(f"Pillow: {PIL.__version__}")
    except Exception:
        pass
    try:
        from . import log
        lines.append(f"Log dir: {log.log_dir()}")
    except Exception:
        pass
    lines.append(f"Config: {_crash_dir().parent}")
    return "\n".join(lines)
