"""Library config: roots, pinned, recents. Persisted to %APPDATA%/PICker/library.json.

Schema:
    {
      "roots":   [{"path": str, "label": str|None, "cover": str|None,
                   "stat": {"count": int, "size": int, "mtime": float}|None}],
      "pinned":  [str, ...],
      "recents": [str, ...]
    }

Older versions stored `roots` as list[str] — load() migrates transparently.
"""
import json
import os
from pathlib import Path

MAX_RECENTS = 12


def _config_dir() -> Path:
    from . import settings as settings_mod
    if settings_mod.is_portable():
        return settings_mod.portable_dir()
    if os.name == "nt":
        base = Path(os.environ.get("APPDATA", Path.home()))
    else:
        base = Path.home() / ".config"
    d = base / "PICker"
    try:
        d.mkdir(parents=True, exist_ok=True)
    except Exception:
        pass
    return d


def _file() -> Path:
    return _config_dir() / "library.json"


_EMPTY = {"roots": [], "pinned": [], "recents": []}


def _normalize_root(entry) -> dict:
    """Coerce legacy str entries or partial dicts into the canonical shape."""
    if isinstance(entry, str):
        return {"path": entry, "label": None, "cover": None, "stat": None}
    if isinstance(entry, dict) and entry.get("path"):
        return {
            "path": entry["path"],
            "label": entry.get("label") or None,
            "cover": entry.get("cover") or None,
            "stat":  entry.get("stat") or None,
        }
    return {}


def load() -> dict:
    try:
        p = _file()
        if p.exists():
            raw = json.loads(p.read_text(encoding="utf-8"))
            if isinstance(raw, dict):
                roots = [r for r in (_normalize_root(e) for e in raw.get("roots", [])) if r]
                pinned = [s for s in raw.get("pinned", []) if isinstance(s, str)]
                recents = [s for s in raw.get("recents", []) if isinstance(s, str)]
                return {"roots": roots, "pinned": pinned, "recents": recents}
    except Exception:
        pass
    return {"roots": [], "pinned": [], "recents": []}


def save(data: dict) -> None:
    """Atomic write — see picker.settings.save for rationale."""
    try:
        merged = {
            "roots":   [_normalize_root(e) for e in data.get("roots", []) if _normalize_root(e)],
            "pinned":  [s for s in data.get("pinned", []) if isinstance(s, str)],
            "recents": [s for s in data.get("recents", []) if isinstance(s, str)],
        }
        target = _file()
        tmp = target.with_suffix(target.suffix + ".tmp")
        payload = json.dumps(merged, indent=2)
        with open(tmp, "w", encoding="utf-8") as f:
            f.write(payload)
            try:
                f.flush()
                os.fsync(f.fileno())
            except OSError as e:
                from . import log
                log.warn("library fsync failed", err=str(e))
        os.replace(str(tmp), str(target))
    except Exception as e:
        from . import log
        log.error("library save failed", err=str(e))


def _norm(p: str) -> str:
    return os.path.normcase(os.path.abspath(p))


# ── Roots ──────────────────────────────────────────────────────────────────────

def roots() -> list[dict]:
    """Return full root dicts. Stale (missing) paths are filtered out."""
    return [r for r in load()["roots"] if r.get("path") and os.path.isdir(r["path"])]


def root_paths() -> list[str]:
    """Convenience: just the paths."""
    return [r["path"] for r in roots()]


def get_root(path: str) -> dict | None:
    norm = _norm(path)
    for r in load()["roots"]:
        if _norm(r["path"]) == norm:
            return r
    return None


def add_root(path: str, label: str | None = None, cover: str | None = None) -> None:
    if not path or not os.path.isdir(path):
        return
    data = load()
    norm = _norm(path)
    if any(_norm(r["path"]) == norm for r in data["roots"]):
        return
    data["roots"].append({
        "path": path, "label": label, "cover": cover, "stat": None,
    })
    save(data)


def remove_root(path: str) -> None:
    data = load()
    norm = _norm(path)
    data["roots"] = [r for r in data["roots"] if _norm(r["path"]) != norm]
    save(data)


def update_root(path: str, *, label: str | None = ..., cover: str | None = ...,
                stat: dict | None = ...) -> None:
    """Update fields of an existing root. Sentinel `...` = leave unchanged."""
    data = load()
    norm = _norm(path)
    for r in data["roots"]:
        if _norm(r["path"]) == norm:
            if label is not ...:
                r["label"] = label
            if cover is not ...:
                r["cover"] = cover
            if stat is not ...:
                r["stat"] = stat
            save(data)
            return


def rename_root(path: str, new_label: str | None) -> None:
    update_root(path, label=new_label)


def set_cover(path: str, cover_path: str | None) -> None:
    update_root(path, cover=cover_path)


# ── Pinned ─────────────────────────────────────────────────────────────────────

def pinned() -> list[str]:
    return [p for p in load()["pinned"] if os.path.isdir(p)]


def toggle_pin(path: str) -> bool:
    if not path:
        return False
    data = load()
    norm = _norm(path)
    exists = any(_norm(p) == norm for p in data["pinned"])
    if exists:
        data["pinned"] = [p for p in data["pinned"] if _norm(p) != norm]
    else:
        data["pinned"].append(path)
    save(data)
    return not exists


def is_pinned(path: str) -> bool:
    norm = _norm(path)
    return any(_norm(p) == norm for p in load()["pinned"])


# ── Recents ────────────────────────────────────────────────────────────────────

def recents() -> list[str]:
    return [p for p in load()["recents"] if os.path.isdir(p)]


def push_recent(path: str) -> None:
    if not path or not os.path.isdir(path):
        return
    data = load()
    norm = _norm(path)
    data["recents"] = [p for p in data["recents"] if _norm(p) != norm]
    data["recents"].insert(0, path)
    data["recents"] = data["recents"][:MAX_RECENTS]
    save(data)


def clear_recents() -> None:
    data = load()
    data["recents"] = []
    save(data)


# ── Quick-diff stat for rescan optimization ────────────────────────────────────

def compute_stat(root_path: str, exts: set[str] | None = None) -> dict:
    """Cheap folder snapshot — count, byte-size, max mtime across matching files.

    Walking for file metadata is ~10-50x faster than opening each image, so
    this can run on every "rescan" click to decide if a deep walk is needed.
    """
    count = 0
    size = 0
    max_mtime = 0.0
    try:
        for dirpath, dirnames, filenames in os.walk(root_path):
            dirnames[:] = [d for d in dirnames if not d.startswith(".")]
            for fn in filenames:
                if fn.startswith("."):
                    continue
                if exts:
                    e = os.path.splitext(fn)[1].lower()
                    if e not in exts:
                        continue
                try:
                    st = os.stat(os.path.join(dirpath, fn))
                except OSError:
                    continue
                count += 1
                size += st.st_size
                if st.st_mtime > max_mtime:
                    max_mtime = st.st_mtime
    except OSError:
        pass
    return {"count": count, "size": size, "mtime": max_mtime}


def stat_differs(a: dict | None, b: dict | None) -> bool:
    if not a or not b:
        return True
    return (
        a.get("count") != b.get("count")
        or a.get("size") != b.get("size")
        or abs((a.get("mtime") or 0) - (b.get("mtime") or 0)) > 0.001
    )


# ── Default seed ───────────────────────────────────────────────────────────────

def default_pictures_folder() -> str | None:
    candidates = []
    if os.name == "nt":
        up = os.environ.get("USERPROFILE")
        if up:
            candidates.append(os.path.join(up, "Pictures"))
    candidates.append(str(Path.home() / "Pictures"))
    candidates.append(str(Path.home() / "Photos"))
    for c in candidates:
        if os.path.isdir(c):
            return c
    return None


def seed_if_empty() -> bool:
    data = load()
    if data["roots"]:
        return False
    pics = default_pictures_folder()
    if not pics:
        return False
    data["roots"].append({
        "path": pics, "label": None, "cover": None, "stat": None,
    })
    save(data)
    return True
