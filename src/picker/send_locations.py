"""Send locations — the saved folders behind the viewer's "Selection" menu.

Each entry is ``{"name": str, "path": str, "action": "move"|"copy"}``, persisted
in settings under ``send_locations``. The user adds one from the right-click
menu ("Copy/Move to a new folder…"); it then sticks around as a one-click (and
one-key) target:

    * exactly 1 location   → Ctrl+Space
    * 2 or more locations  → Ctrl+1 … Ctrl+9  (in list order)

Capped at 9 so every location can hold a number key.
"""
import os

from . import settings as settings_mod

MAX = 9


def _norm(p: str) -> str:
    return os.path.normcase(os.path.abspath(p))


def load() -> list[dict]:
    """Sanitized location list (order = shortcut order)."""
    raw = settings_mod.get("send_locations") or []
    out: list[dict] = []
    for e in raw:
        if not isinstance(e, dict) or not e.get("path"):
            continue
        path = e["path"]
        action = e.get("action") if e.get("action") in ("move", "copy") else "copy"
        name = e.get("name") or (os.path.basename(path.rstrip("/\\")) or path)
        out.append({"name": name, "path": path, "action": action})
    return out[:MAX]


def save(items: list[dict]) -> None:
    settings_mod.set_value("send_locations", items[:MAX])


def add(path: str, action: str, name: str | None = None) -> list[dict]:
    """Add a location (dedup on path+action). Returns the new list."""
    action = "move" if action == "move" else "copy"
    items = [i for i in load()
             if (_norm(i["path"]), i["action"]) != (_norm(path), action)]
    items.append({
        "name": name or (os.path.basename(path.rstrip("/\\")) or path),
        "path": path,
        "action": action,
    })
    items = items[-MAX:]
    save(items)
    return items


def remove(path: str, action: str) -> list[dict]:
    action = "move" if action == "move" else "copy"
    items = [i for i in load()
             if (_norm(i["path"]), i["action"]) != (_norm(path), action)]
    save(items)
    return items


def is_full() -> bool:
    return len(load()) >= MAX


def shortcut_label(index: int, count: int) -> str:
    """Shortcut text for the location at `index`, given the total count."""
    return "Ctrl+Space" if count == 1 else f"Ctrl+{index + 1}"
