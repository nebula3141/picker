"""Toggleable console logger + rotating file logger.

Console off by default. Enable via any of:
  - env var:  PICKER_LOG=1
  - settings: {"log_enabled": true}
  - call:     log.set_enabled(True)

File logging always on — writes to %APPDATA%/PICker/logs/picker.log.

Usage:
    from picker import log
    log.info("scan started", root=path, count=n)

Output style: each line starts with a bright "▌ PICker " prefix in cyan
followed by a coloured level tag, so PICker's own messages remain readable
even when QtMultimedia / ffmpeg / other libraries spam the same console.
"""
import atexit
import os
import sys
import time
from pathlib import Path


_LOG_MAX_BYTES = 5 * 1024 * 1024  # 5 MB
_LOG_BACKUP_COUNT = 3
_log_fh = None
_log_init_done = False


def _log_dir() -> Path:
    try:
        from . import settings as settings_mod
        if settings_mod.is_portable():
            d = settings_mod.portable_dir() / "logs"
            d.mkdir(parents=True, exist_ok=True)
            return d
    except Exception:
        pass
    if os.name == "nt":
        base = Path(os.environ.get("APPDATA", Path.home()))
    else:
        base = Path.home() / ".config"
    d = base / "PICker" / "logs"
    try:
        d.mkdir(parents=True, exist_ok=True)
    except Exception:
        pass
    return d


def _log_file() -> Path:
    return _log_dir() / "picker.log"


def _open_log_file():
    global _log_fh, _log_init_done
    if _log_init_done:
        return
    _log_init_done = True
    try:
        path = _log_file()
        if path.exists() and path.stat().st_size > _LOG_MAX_BYTES:
            _rotate_logs(path)
        _log_fh = open(path, "a", encoding="utf-8", buffering=1)
    except Exception as e:
        _log_fh = None
        try:
            print(f"[PICker] log file open failed: {e}", file=sys.stderr)
        except Exception:
            pass


def _rotate_logs(path: Path):
    for i in range(_LOG_BACKUP_COUNT - 1, 0, -1):
        src = path.with_name(f"{path.stem}.{i}{path.suffix}")
        dst = path.with_name(f"{path.stem}.{i + 1}{path.suffix}")
        try:
            if dst.exists():
                dst.unlink()
            if src.exists():
                src.rename(dst)
        except OSError as e:
            try:
                print(f"[PICker] log rotation failed: {e}", file=sys.stderr)
            except Exception:
                pass
    try:
        bak = path.with_name(f"{path.stem}.1{path.suffix}")
        if bak.exists():
            bak.unlink()
        path.rename(bak)
    except OSError as e:
        try:
            print(f"[PICker] log rotation failed: {e}", file=sys.stderr)
        except Exception:
            pass


def _write_to_file(level: str, msg: str, **kv) -> None:
    _open_log_file()
    if _log_fh is None:
        return
    ts = time.strftime("%Y-%m-%d %H:%M:%S")
    parts = [f"[{ts}]", f"[{level.strip()}]", msg]
    for k, v in kv.items():
        parts.append(f"{k}={v!r}")
    try:
        _log_fh.write(" ".join(parts) + "\n")
    except Exception:
        pass


@atexit.register
def _drain_on_exit() -> None:
    """Make sure pending log lines reach the console even on hard exit
    (PyInstaller windowed builds, unhandled exceptions in worker threads)."""
    try:
        sys.stderr.flush()
    except Exception:
        pass
    try:
        if _log_fh:
            _log_fh.flush()
            _log_fh.close()
    except Exception:
        pass

_FORCE_OFF = False
_enabled: bool | None = None


def _check_settings() -> bool:
    try:
        from . import settings as settings_mod
        return bool(settings_mod.get("log_enabled"))
    except Exception:
        return False


def is_enabled() -> bool:
    """Default behaviour:
      - Frozen exe: off (user can flip in Settings or set PICKER_LOG=1).
      - `python main.py`: ON automatically — devs always want the trace.
    Explicit overrides:
      - PICKER_LOG=0 → off no matter what.
      - PICKER_LOG=1 → on no matter what.
      - settings.json: log_enabled.
    """
    global _enabled
    if _FORCE_OFF:
        return False
    if _enabled is not None:
        return _enabled
    env = os.environ.get("PICKER_LOG")
    if env is not None:
        _enabled = (env not in ("0", "false", "False", ""))
        return _enabled
    if not getattr(sys, "frozen", False):
        _enabled = True
        return True
    _enabled = _check_settings()
    return _enabled


def set_enabled(on: bool) -> None:
    global _enabled
    _enabled = bool(on)


# ── Colour support ────────────────────────────────────────────────────────────
# Windows 10+ supports ANSI escapes once VT mode is enabled. We probe stderr
# isatty() and try to enable VT; on plain pipes (CI / IDE-captured output) we
# fall back to plain ASCII so the log file stays clean.

_USE_COLOR: bool | None = None


def _can_color() -> bool:
    global _USE_COLOR
    if _USE_COLOR is not None:
        return _USE_COLOR
    if os.environ.get("NO_COLOR"):
        _USE_COLOR = False
        return False
    if not sys.stderr.isatty():
        _USE_COLOR = False
        return False
    if os.name == "nt":
        try:
            import ctypes
            kernel32 = ctypes.windll.kernel32
            STD_ERROR_HANDLE = -12
            ENABLE_VIRTUAL_TERMINAL_PROCESSING = 0x0004
            handle = kernel32.GetStdHandle(STD_ERROR_HANDLE)
            mode = ctypes.c_ulong()
            if not kernel32.GetConsoleMode(handle, ctypes.byref(mode)):
                _USE_COLOR = False
                return False
            kernel32.SetConsoleMode(handle, mode.value | ENABLE_VIRTUAL_TERMINAL_PROCESSING)
        except Exception:
            _USE_COLOR = False
            return False
    _USE_COLOR = True
    return True


# ANSI colour codes
_RESET = "\033[0m"
_DIM = "\033[2m"
_BOLD = "\033[1m"
_CYAN = "\033[36m"
_GREEN = "\033[32m"
_YELLOW = "\033[33m"
_RED = "\033[31m"
_GRAY = "\033[90m"

_LEVEL_COLOR = {
    "INFO": _GREEN,
    "WARN": _YELLOW,
    "ERR ": _RED,
    "DBG ": _GRAY,
    "TIME": _CYAN,
}


def _emit(level: str, msg: str, **kv) -> None:
    _write_to_file(level, msg, **kv)
    # WARN / ERR always print — they signal real problems and the user needs
    # them even when info-level logging is off (otherwise extract_thumb
    # failures vanish silently into the void).
    is_problem = level.strip() in ("WARN", "ERR")
    if not is_problem and not is_enabled():
        return
    ts = time.strftime("%H:%M:%S")
    if _can_color():
        prefix = (
            f"{_BOLD}{_CYAN}▌ PICker{_RESET} "
            f"{_DIM}{ts}{_RESET} "
            f"{_LEVEL_COLOR.get(level, '')}{_BOLD}{level.strip()}{_RESET} "
        )
        body = msg
        if kv:
            kv_text = " ".join(f"{_DIM}{k}={_RESET}{v!r}" for k, v in kv.items())
            body = f"{body}  {kv_text}"
        line = prefix + body
    else:
        parts = [f"[PICker]", f"[{ts}]", f"[{level.strip()}]", msg]
        for k, v in kv.items():
            parts.append(f"{k}={v!r}")
        line = " ".join(parts)
    try:
        print(line, file=sys.stderr, flush=True)
    except Exception:
        pass


def info(msg: str, **kv) -> None:
    _emit("INFO", msg, **kv)


def warn(msg: str, **kv) -> None:
    _emit("WARN", msg, **kv)


def error(msg: str, **kv) -> None:
    _emit("ERR ", msg, **kv)


def debug(msg: str, **kv) -> None:
    _emit("DBG ", msg, **kv)


def log_dir() -> str:
    return str(_log_dir())


def log_file_path() -> str:
    return str(_log_file())


class timed:
    """Context manager to time a block. Prints only if logging enabled.

    with log.timed("scan_root", root=path):
        ...
    """
    def __init__(self, label: str, **kv):
        self.label = label
        self.kv = kv
        self.t0 = 0.0

    def __enter__(self):
        self.t0 = time.perf_counter()
        return self

    def __exit__(self, exc_type, exc, tb):
        ms = (time.perf_counter() - self.t0) * 1000
        _emit("TIME", self.label, ms=f"{ms:.1f}", **self.kv)
