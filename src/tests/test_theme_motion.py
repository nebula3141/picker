"""Two-mode theme, reactive restyling, and the Animation switch."""
import os

import pytest



def _contrast(a, b):
    def lum(c):
        def ch(v):
            v /= 255
            return v / 12.92 if v <= 0.03928 else ((v + 0.055) / 1.055) ** 2.4
        return 0.2126 * ch(c.red()) + 0.7152 * ch(c.green()) + 0.0722 * ch(c.blue())
    hi, lo = sorted((lum(a), lum(b)), reverse=True)
    return (hi + 0.05) / (lo + 0.05)


def test_only_two_modes_and_toggle_flips(qapp):
    from picker import theme
    assert theme.theme_names() == ["dark", "light"]
    assert theme.toggle("dark") == "light"
    assert theme.toggle("light") == "dark"


def test_translucent_tokens_keep_alpha_in_qss(qapp):
    """`.name()` would drop the alpha; glass has to reach the stylesheet intact."""
    from picker import theme
    theme.apply(qapp, "dark")
    assert theme.hexc("deck").startswith("rgba(")
    assert theme.hexc("fg").startswith("#")
    # flattened glass is opaque and sits between the ground and the tint
    assert theme.solid("deck").alpha() == 255


@pytest.mark.parametrize("mode", ["dark", "light"])
def test_text_meets_aa_on_its_ground(qapp, mode):
    from picker import theme
    theme.apply(qapp, mode)
    ground = theme.c("bg")
    for key in ("fg", "muted", "faint", "accent_text"):
        assert _contrast(theme.c(key), ground) >= 4.5, (mode, key)
    # text on the glass panel it usually sits on
    for key in ("fg", "muted"):
        assert _contrast(theme.c(key), theme.solid("deck_high")) >= 4.5, (mode, key)
    # the photo well's own labels
    assert _contrast(theme.c("canvas_fg"), theme.c("canvas_bg")) >= 4.5, mode
    theme.apply(qapp, "dark")


def test_filled_button_label_contrast(qapp):
    from PyQt6.QtGui import QColor
    from picker import theme
    assert _contrast(QColor(theme.ACCENT_SOLID), QColor("#ffffff")) >= 4.5


def test_bind_restyles_live_without_rebuilding(qapp):
    from PyQt6.QtWidgets import QLabel, QWidget
    from picker import theme
    theme.apply(qapp, "dark")
    root = QWidget()
    lbl = QLabel("x", root)
    theme.bind(lbl, lambda: f"color: {theme.hexc('fg')};")
    dark_sheet = lbl.styleSheet()
    calls = []
    root.theme_changed = lambda: calls.append(1)
    theme.apply(qapp, "light")
    theme.restyle(root)
    assert lbl.styleSheet() != dark_sheet          # same widget, new colours
    assert calls == [1]                            # hook ran once
    theme.apply(qapp, "dark")


def test_motion_switch_zeroes_every_duration(settings_dir):
    from picker import motion
    motion.invalidate()
    motion.set_enabled(True)
    assert motion.dur(motion.VIEW) == motion.VIEW
    motion.set_enabled(False)
    assert motion.dur(motion.MICRO) == 0 and motion.dur(motion.VIEW) == 0
    # the viewer's cross-fade follows the same switch
    from picker import settings
    assert settings.get("slideshow_animation") is False
    assert settings.get("ui_motion") is False
    motion.set_enabled(True)


def test_shell_command_quotes_exe_path(monkeypatch):
    r"""CWE-428: an unquoted exe path with a space lets Windows run a planted
    "D:\My.exe" instead of "D:\My Apps\PICker.exe"."""
    import sys
    from picker import file_assoc
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", r"D:\My Apps\PICker.exe")
    assert file_assoc._exe_path() == r'"D:\My Apps\PICker.exe"'


def test_chevron_asset_lives_in_app_folder(qapp, settings_dir):
    """Written to the per-user app folder, never a shared temp dir where a
    predictable name could be pre-planted."""
    from picker import theme
    theme._chevrons.clear()
    theme.apply(qapp, "dark")
    url = theme._chevron_url()
    assert url and url.startswith(str(settings_dir).replace("\\", "/"))
