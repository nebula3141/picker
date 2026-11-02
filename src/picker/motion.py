"""Motion: one switch, a few tokens, and a hard rule about what may move.

The app has a single Animation setting. On, motion carries *presence*: a view
arrives with a short fade, a toast settles in, a control acknowledges a press.
Off, every one of those becomes a hard cut with zero delay — the same UI at
full speed, never a "reduced" one with pieces missing.

Durations are tokens, not one number reused everywhere: feedback under your
finger is faster than a whole view arriving. Nothing here animates layout
(width/height/geometry) — only opacity, so motion never costs a relayout.
"""
from __future__ import annotations

from PyQt6.QtCore import QEasingCurve

from . import settings as settings_mod

# Tokens, in milliseconds.
MICRO = 90     # a press, a hover, a switch flipping
SHORT = 140    # a toast, a panel, a badge
VIEW = 180     # a whole screen arriving

_enabled: bool | None = None


def enabled() -> bool:
    """Is motion on? Cached after the first read — this sits on paint and
    event paths where a settings lookup per call would be waste."""
    global _enabled
    if _enabled is None:
        v = settings_mod.get("ui_motion")
        _enabled = True if v is None else bool(v)
    return _enabled


def set_enabled(value: bool, *, persist: bool = True) -> None:
    global _enabled
    _enabled = bool(value)
    if persist:
        settings_mod.set_value("ui_motion", _enabled)
        # The viewer's cross-fade obeys the same switch, so the two settings
        # can never disagree.
        settings_mod.set_value("slideshow_animation", _enabled)


def invalidate() -> None:
    """Forget the cached value (after Settings writes the key directly)."""
    global _enabled
    _enabled = None


def dur(token: int = SHORT) -> int:
    """A duration in ms, or 0 when motion is off."""
    return token if enabled() else 0


def ease() -> QEasingCurve.Type:
    """Decelerate on arrival: quick out of the gate, settling at the end."""
    return QEasingCurve.Type.OutCubic
