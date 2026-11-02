"""User settings — persisted JSON in %APPDATA%/PICker/settings.json."""
import json
import os
import sys
from pathlib import Path

# ── Portable mode ─────────────────────────────────────────────────────────────
# When set, ALL PICker modules use this directory instead of %APPDATA%/PICker.
# Set by main.py when --portable flag or portable.txt marker detected.
_portable_dir: Path | None = None


def enable_portable(base: Path | None = None):
    global _portable_dir
    if base is None:
        if getattr(sys, "frozen", False):
            base = Path(sys.executable).parent / "data"
        else:
            base = Path(__file__).resolve().parent.parent / "data"
    base.mkdir(parents=True, exist_ok=True)
    _portable_dir = base
    invalidate()  # config dir changed — drop any cache built from %APPDATA%


def is_portable() -> bool:
    return _portable_dir is not None


def portable_dir() -> Path | None:
    return _portable_dir

SETTINGS_VERSION = 4

DEFAULTS = {
    # ── Internal
    "settings_version": SETTINGS_VERSION,

    # ── Defaults (moved from StartupDialog)
    "default_mode": "copy",              # "copy" | "move"
    "default_resolution_pct": 50,        # 10 | 25 | 50 | 100 (smart: small images never downscaled)
    "display_full_resolution": False,    # if True, override pct and decode at native (RAM-heavy on 60MP RAW)

    # ── Gallery
    "thumbnail_row_height": 170,         # 120 | 160 | 200 | 240 (actually row target)
    "thumb_cache_mb": 1024,

    # ── Slideshow
    "preload_count": 2,                  # 1..5 neighbors each side
    "auto_advance_on_send": True,
    "show_filmstrip": True,
    "explorer_escape_action": "mosaic",  # "mosaic" | "close" — Esc from a photo opened via Explorer/file-association
    "conflict_default": "ask",           # "ask"|"rename"|"replace"|"skip"
    "zoom_factor": 1.18,                 # 1.05..1.5
    "peaking_threshold": 28,             # 5..80
    "histogram_style": "additive",       # "additive"|"luminance"
    "raw_preference": "embedded",        # "embedded"|"full"

    # ── Source scan
    "include_subfolders": True,
    "exclude_hidden": True,
    "file_types": ["jpeg", "png", "tiff", "webp", "bmp", "heif", "svg", "raw"],
    # Every type you've been offered — a type added later than this list is
    # switched on for you; one you've seen and turned off stays off.
    "file_types_known": ["jpeg", "png", "tiff", "webp", "bmp", "heif", "svg", "raw"],
    "include_videos": True,              # show .mp4/.mov/etc alongside images

    # ── Appearance
    "theme": "dark",                     # "dark" | "light"

    # ── External editors (auto-detect if empty)
    "photoshop_path": "",
    "lightroom_path": "",

    # ── Overlay corners: "tl"|"tr"|"bl"|"br"
    "exif_position": "tr",
    "histogram_position": "br",

    # ── Library / index (Phase 1)
    "auto_scan_on_launch": False,        # rescan only when user clicks Rescan; folders cached in-memory after first visit
    "scan_recursive": True,              # walk subfolders under each root
    "last_folder": "",                   # remember last viewed folder in gallery
    "seen_welcome": False,               # first-run coach marks shown once
    "window_geometry": "",               # hex-encoded QMainWindow.saveGeometry() — restored on launch
    "recent_target_folders": [],         # last ≤3 move/copy destinations (album view right-click)
    "send_locations": [],                # [{name,path,action:"move"|"copy"}] — viewer "Selection" targets
    "confirm_delete": True,              # ask before sending a file to the Recycle Bin
    "send_feedback": True,               # subtle accent flash on move/copy/send
    "group_by": "flat",                  # "flat"|"date"|"folder"|"camera"
    "date_group_granularity": "day",     # "year"|"month"|"day"
    "sort_order": "date_taken",          # filename|date_taken|mtime|size|rating|random
    "sort_last_source": "",              # two-pane Sort: last source folder
    "sort_last_destination": "",         # two-pane Sort: last destination folder

    # ── Editing (crop/rotate save)
    "edit_save_mode": "ask",             # "ask"|"new"|"overwrite"
    "edit_new_suffix": "_edit",          # appended before extension for "save as new"
    "edit_jpeg_quality": 95,

    # ── Slideshow animation
    "slideshow_animation": True,         # cross-fade between images in fullscreen
    # ── Interface motion: one switch for every transition (off = instant)
    "ui_motion": True,
    # ── Folder browser: scroll track labelled with capture times
    "timeline_scroll": True,
    # ── Window: full screen is a mode you choose; it survives restarts
    "fullscreen": False,

    # ── File associations
    "file_associations_registered": False,

    # ── Debug
    "log_enabled": False,                # print diagnostic logs to stderr
    "check_updates": True,               # check for new versions on startup
}


# ── Settings migration ────────────────────────────────────────────────────────

_MIGRATIONS: dict[int, callable] = {}


def _migration(from_version: int):
    def decorator(fn):
        _MIGRATIONS[from_version] = fn
        return fn
    return decorator


@_migration(0)
def _migrate_v0_to_v1(data: dict) -> dict:
    data.setdefault("slideshow_animation", True)
    data.setdefault("file_associations_registered", False)
    data["settings_version"] = 1
    return data


@_migration(1)
def _migrate_v1_to_v2(data: dict) -> dict:
    data.setdefault("check_updates", True)
    data["settings_version"] = 2
    return data


@_migration(2)
def _migrate_v2_to_v3(data: dict) -> dict:
    # Old default decode resolution was 25%, which softened small images.
    # Bump users still sitting on the old default up to the new 50% default.
    # Anyone who deliberately picked another value is left untouched.
    if data.get("default_resolution_pct") == 25:
        data["default_resolution_pct"] = 50
    data["settings_version"] = 3
    return data


@_migration(3)
def _migrate_v3_to_v4(data: dict) -> dict:
    # 5.0 folds the viewer-only "slideshow_animation" into one Animation switch
    # for the whole app. Someone who had turned animation off keeps it off.
    data.setdefault("ui_motion", bool(data.get("slideshow_animation", True)))
    data["settings_version"] = 4
    return data


# File types PICker offered before it recorded which ones you'd been shown
# (4.8 and earlier). SVG arrived in 4.9 and HEIC/AVIF in 4.10 without an
# upgrade step, so a list saved earlier silently skipped those files.
_TYPES_BEFORE_TRACKING = ("jpeg", "png", "tiff", "webp", "bmp", "raw")


def _reconcile_file_types(data: dict) -> bool:
    """Switch on file types added since these settings were saved.

    ``file_types_known`` records every type the user has been offered. A type
    missing from it is new to them — enable it. A known type that's off was
    turned off on purpose — leave it. Returns True if ``data`` changed."""
    types = data.get("file_types")
    if not isinstance(types, list):
        return False
    all_types = list(DEFAULTS["file_types"])
    known = data.get("file_types_known")
    if not isinstance(known, list):
        known = list(_TYPES_BEFORE_TRACKING)
    new = [t for t in all_types if t not in known and t not in types]
    changed = bool(new) or sorted(known) != sorted(all_types)
    if new:
        data["file_types"] = list(types) + new
    data["file_types_known"] = all_types
    return changed


def _run_migrations(data: dict) -> dict:
    version = data.get("settings_version", 0)
    if version >= SETTINGS_VERSION:
        return data
    backup = _settings_file().with_suffix(".json.bak")
    try:
        payload = json.dumps(data, indent=2)
        with open(backup, "w", encoding="utf-8") as f:
            f.write(payload)
    except Exception as e:
        from . import log
        log.warn("settings migration backup failed", err=str(e))
    while version < SETTINGS_VERSION:
        fn = _MIGRATIONS.get(version)
        if fn is None:
            data["settings_version"] = SETTINGS_VERSION
            break
        data = fn(data)
        version = data.get("settings_version", version + 1)
    return data

# ── Folder position memory (separate from main settings to avoid bloat) ──────

_POS_CACHE_MAX = 200

# Positions are saved on *every* image navigation. That used to re-read, rewrite
# and fsync positions.json on the UI thread per arrow-key press — a real disk
# stall while flicking through photos. Now the map lives in memory and is
# written by a debounced background thread (and flushed on exit).
import threading as _threading

_pos_lock = _threading.Lock()
_pos_mem: dict | None = None
_pos_mem_path: str | None = None
_pos_dirty = False
_pos_timer: "_threading.Timer | None" = None
_POS_WRITE_DELAY = 1.5


def _positions_file():
    return _config_dir() / "positions.json"


def _positions_locked() -> dict:
    """In-memory positions for the current config dir (call under _pos_lock)."""
    global _pos_mem, _pos_mem_path
    cur = str(_positions_file())
    if _pos_mem is None or _pos_mem_path != cur:
        data: dict = {}
        try:
            p = Path(cur)
            if p.exists():
                raw = json.loads(p.read_text(encoding="utf-8"))
                if isinstance(raw, dict):
                    data = raw
        except Exception:
            pass
        _pos_mem, _pos_mem_path = data, cur
    return _pos_mem


def load_positions() -> dict:
    with _pos_lock:
        return dict(_positions_locked())


def save_position(folder: str, idx: int) -> None:
    global _pos_dirty, _pos_timer
    key = os.path.normcase(os.path.abspath(folder))
    with _pos_lock:
        data = _positions_locked()
        if data.get(key) == idx:
            return
        data.pop(key, None)          # re-insert so the most recent sits last (LRU)
        data[key] = idx
        while len(data) > _POS_CACHE_MAX:
            del data[next(iter(data))]
        _pos_dirty = True
        if _pos_timer is None:
            _pos_timer = _threading.Timer(_POS_WRITE_DELAY, flush_positions)
            _pos_timer.daemon = True
            _pos_timer.start()


def flush_positions() -> None:
    """Write pending positions to disk now (atomic). Safe from any thread."""
    global _pos_dirty, _pos_timer
    with _pos_lock:
        _pos_timer = None
        if not _pos_dirty or _pos_mem_path is None:
            return
        payload = json.dumps(_pos_mem)
        target = Path(_pos_mem_path)
        _pos_dirty = False
    try:
        tmp = target.with_suffix(target.suffix + ".tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            f.write(payload)
        os.replace(str(tmp), str(target))
    except Exception as e:
        from . import log
        log.warn("position save failed", err=str(e))


def clear_positions() -> None:
    """Forget all remembered positions (memory + disk)."""
    global _pos_mem, _pos_mem_path, _pos_dirty
    with _pos_lock:
        _pos_mem, _pos_mem_path, _pos_dirty = {}, str(_positions_file()), False
    try:
        _positions_file().unlink(missing_ok=True)
    except OSError:
        pass


def get_position(folder: str) -> int:
    with _pos_lock:
        return _positions_locked().get(os.path.normcase(os.path.abspath(folder)), 0)


def _config_dir() -> Path:
    if _portable_dir is not None:
        return _portable_dir
    if os.name == "nt":
        base = Path(os.environ.get("APPDATA", Path.home()))
    elif sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support"
    else:
        base = Path(os.environ.get("XDG_CONFIG_HOME") or (Path.home() / ".config"))
    d = base / "PICker"
    try:
        d.mkdir(parents=True, exist_ok=True)
    except Exception:
        pass
    return d


def _settings_file() -> Path:
    return _config_dir() / "settings.json"


# In-memory cache of the merged settings. `get()`/`load()` were re-reading and
# re-parsing settings.json from disk on every single call; modules poll settings
# constantly, so this turned into thousands of redundant syscalls + JSON parses.
# We hold the merged dict in memory and only touch disk on first load / save().
# Single-process app, so an external edit to the file won't be picked up until
# restart — acceptable, and `invalidate()` is available if ever needed.
_mem: dict | None = None
_mem_path: str | None = None   # file path the cache was built from (dir can change in tests/portable)


def invalidate() -> None:
    """Drop the in-memory cache so the next load() re-reads from disk."""
    global _mem, _mem_path
    _mem = None
    _mem_path = None


def load() -> dict:
    global _mem, _mem_path
    cur_path = str(_settings_file())
    if _mem is not None and _mem_path == cur_path:
        return dict(_mem)
    data = dict(DEFAULTS)
    try:
        p = _settings_file()
        if p.exists():
            text = p.read_text(encoding="utf-8")
            if not text.strip():
                _mem = dict(data)
                _mem_path = cur_path
                return dict(data)
            raw = json.loads(text)
            if isinstance(raw, dict):
                version = raw.get("settings_version", 0)
                migrated = version < SETTINGS_VERSION
                if migrated:
                    raw = _run_migrations(raw)
                if _reconcile_file_types(raw) or migrated:
                    save(raw)
                data.update({k: v for k, v in raw.items() if k in DEFAULTS})
    except json.JSONDecodeError:
        bak = _settings_file().with_suffix(".json.corrupt")
        try:
            os.replace(str(_settings_file()), str(bak))
        except OSError:
            pass
    except Exception:
        pass
    _mem = dict(data)
    _mem_path = cur_path
    return dict(data)


_save_lock = _threading.Lock()
_save_timer: "_threading.Timer | None" = None
_save_pending = False
_SAVE_DELAY = 0.4


def save(data: dict, *, immediate: bool = False) -> None:
    """Update settings. The in-memory copy changes at once; the disk write is
    debounced onto a background thread (and flushed on exit), because an atomic
    write + fsync on the UI thread stalled the window on things as ordinary as
    toggling the theme or remembering a folder."""
    global _mem, _mem_path, _save_timer, _save_pending
    try:
        merged = dict(DEFAULTS)
        merged.update({k: v for k, v in data.items() if k in DEFAULTS})
        target = _settings_file()
        _mem = dict(merged)            # keep cache in sync with what we persist
        _mem_path = str(target)
        if immediate:
            flush_settings()
            return
        with _save_lock:
            _save_pending = True
            if _save_timer is None:
                _save_timer = _threading.Timer(_SAVE_DELAY, flush_settings)
                _save_timer.daemon = True
                _save_timer.start()
    except Exception as e:
        from . import log
        log.error("settings save failed", err=str(e))


def flush_settings() -> None:
    """Write pending settings to disk now (atomic). Safe from any thread."""
    global _save_timer, _save_pending
    with _save_lock:
        _save_timer = None
        if not _save_pending or _mem is None or _mem_path is None:
            return
        _save_pending = False
        payload = json.dumps(dict(_mem), indent=2)
        target = Path(_mem_path)
    try:
        tmp = target.with_suffix(target.suffix + ".tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            f.write(payload)
            try:
                f.flush()
                os.fsync(f.fileno())
            except OSError as e:
                from . import log
                log.warn("settings fsync failed", err=str(e))
        os.replace(str(tmp), str(target))
    except Exception as e:
        from . import log
        log.error("settings save failed", err=str(e))


def get(key: str):
    return load().get(key, DEFAULTS.get(key, ""))


def set_value(key: str, value) -> None:
    data = load()
    data[key] = value
    save(data)
