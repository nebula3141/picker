"""Platform feature gating.

Windows-only integrations — file associations, the shell "Browse with PICker"
context menu, and the taskbar AppUserModelID — are gated through
``windows_features_enabled()``. They are ON only on Windows, and a launcher (or
the user) can force them OFF anywhere by exporting::

    PICKER_DISABLE_WINDOWS_FEATURES=1

The cross-platform launcher in ``linux/launch.py`` sets that flag (and
``PICKER_PLATFORM``) automatically on non-Windows systems, so the rest of the
app never needs to special-case the OS.
"""
import os
import sys


def platform_name() -> str:
    """Best-effort platform id: 'windows' | 'linux' | 'macos' | <raw>.
    Honors an explicit PICKER_PLATFORM override set by the launcher."""
    forced = os.environ.get("PICKER_PLATFORM")
    if forced:
        return forced
    if sys.platform.startswith("win"):
        return "windows"
    if sys.platform == "darwin":
        return "macos"
    if sys.platform.startswith("linux"):
        return "linux"
    return sys.platform or "unknown"


def windows_features_enabled() -> bool:
    """True only on Windows and only when not explicitly disabled.

    Gates file associations, the shell context menu, and the taskbar
    AppUserModelID. Everything else in the app is cross-platform.
    """
    if os.environ.get("PICKER_DISABLE_WINDOWS_FEATURES") == "1":
        return False
    return sys.platform == "win32"
