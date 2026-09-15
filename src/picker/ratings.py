"""Per-file star ratings (0–5), stored app-side — originals are never touched.

Ratings live in one small JSON per folder, alongside the thumbnail cache in
``.picker_cache/ratings.json``, keyed by absolute path. Kept in memory and
flushed atomically so rating a fast burst of photos costs no per-keystroke disk
write. A rating of 0 means "unrated" and is dropped from the store.
"""
from __future__ import annotations

import json
import os
import threading
from pathlib import Path

_CACHE_DIRNAME = ".picker_cache"
_INDEX_NAME = "ratings.json"

_lock = threading.Lock()
_folders: dict[str, dict[str, int]] = {}   # source_folder -> {npath: stars}
_loaded: set[str] = set()
_dirty: set[str] = set()


def _index_path(source_folder: str) -> Path:
    return Path(source_folder) / _CACHE_DIRNAME / _INDEX_NAME


def _npath(path: str) -> str:
    return os.path.normcase(os.path.abspath(path))


def _ensure_loaded(source_folder: str) -> dict:
    d = _folders.get(source_folder)
    if source_folder in _loaded:
        return d if d is not None else {}
    data: dict = {}
    try:
        p = _index_path(source_folder)
        if p.is_file():
            loaded = json.loads(p.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                data = {k: int(v) for k, v in loaded.items()
                        if isinstance(v, (int, float)) and 1 <= int(v) <= 5}
    except (OSError, ValueError):
        data = {}
    _folders[source_folder] = data
    _loaded.add(source_folder)
    return data


def get(source_folder: str, path: str) -> int:
    """Star rating 0–5 for ``path`` (0 = unrated)."""
    key = _npath(path)
    with _lock:
        return _ensure_loaded(source_folder).get(key, 0)


def set_rating(source_folder: str, path: str, stars: int) -> int:
    """Set (clamped 0–5) and return the stored value. 0 clears the rating.
    Buffered — call :func:`flush` to persist."""
    stars = max(0, min(5, int(stars)))
    key = _npath(path)
    with _lock:
        d = _ensure_loaded(source_folder)
        if stars == 0:
            if d.pop(key, None) is None:
                return 0
        elif d.get(key) == stars:
            return stars
        else:
            d[key] = stars
        _dirty.add(source_folder)
    return stars


def flush(source_folder: str | None = None) -> None:
    """Write buffered ratings to disk atomically (one folder, or all dirty)."""
    with _lock:
        targets = ([source_folder] if source_folder is not None
                   else list(_dirty))
        pending = []
        for src in targets:
            if src not in _dirty:
                continue
            pending.append((src, dict(_folders.get(src, {}))))
            _dirty.discard(src)
    for src, data in pending:
        try:
            p = _index_path(src)
            if not data:
                # Nothing rated anymore — remove the file rather than leave {}.
                if p.exists():
                    p.unlink()
                continue
            p.parent.mkdir(parents=True, exist_ok=True)
            tmp = p.with_suffix(".tmp")
            tmp.write_text(json.dumps(data, separators=(",", ":")),
                           encoding="utf-8")
            os.replace(tmp, p)
        except OSError:
            pass
