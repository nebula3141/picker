"""Auto-update checker — polls GitHub Releases API in background."""
import json
import os
import time
import urllib.request
from pathlib import Path
from threading import Thread

RELEASES_URL = "https://api.github.com/repos/nebula3141/PICker/releases/latest"
CHECK_INTERVAL = 86400  # 24h


def _cache_file() -> Path:
    try:
        from . import settings as settings_mod
        if settings_mod.is_portable():
            return settings_mod.portable_dir() / "update-check.json"
    except Exception:
        pass
    if os.name == "nt":
        base = Path(os.environ.get("APPDATA", Path.home()))
    else:
        base = Path.home() / ".config"
    return base / "PICker" / "update-check.json"


def _read_cache() -> dict:
    try:
        p = _cache_file()
        if p.exists():
            return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        pass
    return {}


def _write_cache(data: dict):
    try:
        p = _cache_file()
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(data), encoding="utf-8")
    except Exception:
        pass


def _parse_version(tag: str) -> tuple[int, ...]:
    tag = tag.lstrip("vV")
    parts = []
    for p in tag.split("."):
        try:
            parts.append(int(p))
        except ValueError:
            break
    return tuple(parts)


def check_now() -> dict | None:
    """Fetch latest release. Returns {"tag": str, "url": str, "name": str} or None."""
    try:
        req = urllib.request.Request(RELEASES_URL, headers={"User-Agent": "PICker"})
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        tag = data.get("tag_name", "")
        url = data.get("html_url", "")
        name = data.get("name", tag)
        return {"tag": tag, "url": url, "name": name}
    except Exception:
        return None


def should_check() -> bool:
    try:
        from . import settings as settings_mod
        if not settings_mod.get("check_updates"):
            return False
    except Exception:
        pass
    cache = _read_cache()
    last = cache.get("last_check", 0)
    return (time.time() - last) > CHECK_INTERVAL


def is_newer(remote_tag: str) -> bool:
    from picker import __version__
    local = _parse_version(__version__)
    remote = _parse_version(remote_tag)
    return remote > local


def check_in_background(callback):
    """Run update check in thread. Calls callback(release_dict) on main thread if newer."""
    if not should_check():
        return

    def _worker():
        result = check_now()
        _write_cache({"last_check": time.time()})
        if result and is_newer(result["tag"]):
            callback(result)

    t = Thread(target=_worker, daemon=True)
    t.start()
