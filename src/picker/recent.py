import json
import os
from pathlib import Path


MAX_RECENT = 6


def _config_dir() -> Path:
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


def _recent_file() -> Path:
    return _config_dir() / "recent.json"


def load() -> list[str]:
    try:
        p = _recent_file()
        if p.exists():
            data = json.loads(p.read_text(encoding="utf-8"))
            if isinstance(data, list):
                return [s for s in data if isinstance(s, str) and os.path.isdir(s)]
    except Exception:
        pass
    return []


def clear() -> None:
    try:
        p = _recent_file()
        if p.exists():
            p.unlink()
    except Exception:
        pass


def add(path: str) -> None:
    if not path:
        return
    try:
        recents = load()
        # De-dup case-insensitive on Windows
        norm = os.path.normcase(os.path.abspath(path))
        recents = [r for r in recents if os.path.normcase(os.path.abspath(r)) != norm]
        recents.insert(0, path)
        recents = recents[:MAX_RECENT]
        # Atomic: tmp + replace so a crash mid-write doesn't truncate.
        target = _recent_file()
        tmp = target.with_suffix(target.suffix + ".tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            f.write(json.dumps(recents, indent=2))
            try:
                f.flush(); os.fsync(f.fileno())
            except OSError:
                pass
        os.replace(str(tmp), str(target))
    except Exception as e:
        from . import log
        log.warn("recent save failed", err=str(e))
