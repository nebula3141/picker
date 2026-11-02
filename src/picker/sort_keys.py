"""Which keyboard key sends a photo to which Multi Sort destination.

Each destination carries a ``"key"`` (e.g. "1", "Q", "F2"). Defaults are 1, 2,
3, 4 … in order. Only keys the viewer doesn't already use are offered, so a
destination key can never shadow Rotate, Crop, navigation and so on. Star
rating lives on Ctrl+1–5, so plain digits are free for sorting.
"""
from __future__ import annotations

from PyQt6.QtCore import Qt

DIGITS = [str(n) for n in range(1, 10)]
# Letters the viewer leaves free (A/D navigate, C crop, E explorer, F full
# screen, H histogram, I info, J/K/L video, M mute, O open, P peaking, R rotate,
# S sync, V compare, Z 1:1 are all taken).
LETTERS = ["B", "G", "N", "Q", "T", "U", "W", "Y"]
FKEYS = [f"F{n}" for n in range(1, 11)] + ["F12"]      # F11 = full screen
ALLOWED = DIGITS + LETTERS + FKEYS


def default_key(index: int, taken: set[str] | None = None) -> str:
    """The key a destination gets by default: 1, 2, 3 … skipping taken ones."""
    taken = taken or set()
    preferred = DIGITS[index] if index < len(DIGITS) else None
    if preferred and preferred not in taken:
        return preferred
    for k in ALLOWED:
        if k not in taken:
            return k
    return ""


def key_of(dest: dict, index: int) -> str:
    k = str(dest.get("key") or "").upper()
    return k if k in ALLOWED else (DIGITS[index] if index < len(DIGITS) else "")


def qt_key(label: str) -> int | None:
    """Qt key code for an ALLOWED label, or None."""
    label = (label or "").upper()
    if label in DIGITS:
        return int(Qt.Key.Key_0.value) + int(label)
    if label in LETTERS:
        return int(Qt.Key.Key_A.value) + (ord(label) - ord("A"))
    if label in FKEYS:
        return int(Qt.Key.Key_F1.value) + int(label[1:]) - 1
    return None


def key_map(destinations: list[dict]) -> dict[int, int]:
    """{Qt key code: destination index} for a destination list."""
    out: dict[int, int] = {}
    for i, d in enumerate(destinations):
        code = qt_key(key_of(d, i))
        if code is not None and code not in out:
            out[code] = i
    return out
