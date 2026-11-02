"""PICker's surface: two modes, translucent chrome, one accent.

DIRECTION (UI/UX Pro Max: Minimalism & Swiss for a professional tool, with a
glass treatment on chrome)
The photograph is the only opaque, saturated thing on screen. Every piece of
chrome — rails, panels, buttons, readouts — is a translucent plate over one
ground colour: a hairline edge and nothing else. No moulding, no gradients, no
drop shadows, no decorative colour.

RULES
* Two modes: dark (default) and light. `toggle()` flips between them.
* Surfaces are alpha over the ground, so depth comes from stacking, not hue.
* #3b82f6 is the only lit colour (commit, focus, active target); red is only
  ever destructive; everything else is ink on glass.
* Text is checked against the ground it actually lands on: >= 4.5:1 for text,
  >= 3:1 for edges and focus rings.
* One radius (6px), 1px hairlines, spacing on an 8px module.

REACTIVE
Painters read colours through `c(key)` at paint time. Stylesheets are bound
with `bind(widget, fn)` instead of baked, and `restyle()` re-runs every binding,
so flipping the mode is a repaint: no view is rebuilt, nothing loses its place.
"""
from __future__ import annotations

from typing import Callable

from PyQt6.QtGui import QColor, QFont, QFontDatabase, QPalette
from PyQt6.QtWidgets import QApplication, QWidget

# The one lit colour, pinned by the product.
ACCENT = QColor(59, 130, 246)   # #3b82f6
DANGER = QColor(226, 86, 74)    # #e2564a — destructive only


def _a(c: QColor, alpha: float) -> QColor:
    """The same colour at a given opacity — how every surface here is made."""
    out = QColor(c)
    out.setAlpha(round(alpha * 255))
    return out


_WHITE = QColor(255, 255, 255)
_INK = QColor(9, 11, 13)

DARK = {
    # ground: the only opaque chrome surface
    "bg":                QColor(15, 17, 20),      # #0f1114
    "chassis":           QColor(15, 17, 20),
    # glass: everything else is white at low opacity over that ground
    "deck":              _a(_WHITE, 0.045),       # panel / bar plate
    "deck_high":         _a(_WHITE, 0.085),       # hover / raised plate
    "rail":              _a(_WHITE, 0.10),        # hairline edge
    "rail_light":        _a(_WHITE, 0.18),        # scroll handle, strong edge
    "glass":             _a(_WHITE, 0.045),
    "glass_high":        _a(_WHITE, 0.085),
    "glass_edge":        _a(_WHITE, 0.14),
    "scrim":             _a(_INK, 0.62),          # chrome laid over a photo
    # the photo well: neutral, nothing tints the picture
    "canvas_bg":         QColor(10, 11, 13),
    "well":              QColor(10, 11, 13),
    "tile_placeholder":  _a(_WHITE, 0.05),
    "filmstrip_bg":      _a(_INK, 0.45),
    # buttons (glass chips)
    "cap":               _a(_WHITE, 0.14),
    "cap_alt":           _a(_WHITE, 0.07),
    "cap_dark":          _a(_WHITE, 0.05),
    "cap_dark_hover":    _a(_WHITE, 0.11),
    "legend":            QColor(237, 241, 246),   # ink on glass
    "legend_dark":       QColor(237, 241, 246),
    # text
    "fg":                QColor(237, 241, 246),   # #edf1f6
    "muted":             QColor(152, 160, 170),   # #98a0aa
    "canvas_fg":         QColor(152, 160, 170),
    "faint":             QColor(128, 136, 146),
    # signals
    "accent":            ACCENT,
    "accent_text":       QColor(0x7c, 0xb0, 0xff),
    "focus":             QColor(0x7c, 0xb0, 0xff),
    "amber":             QColor(0x7c, 0xb0, 0xff),   # retired; aliases accent
    "danger":            DANGER,
    # readout: type on glass, no coloured pane
    "screen_bg":         _a(_WHITE, 0.045),
    "screen_edge":       _a(_WHITE, 0.10),
    "screen_fg":         QColor(237, 241, 246),
    "screen_dim":        QColor(152, 160, 170),
    # legacy string keys (older QSS call sites)
    "status_bar_bg":     "#0f1114",
    "status_bar_fg":     "#edf1f6",
    "hint_bar_bg":       "#0f1114",
    "hint_bar_fg":       "#edf1f6",
    "empty_dash":        _a(_WHITE, 0.16),
    "empty_title":       QColor(237, 241, 246),
    "empty_hint":        QColor(152, 160, 170),
    "empty_footer":      QColor(128, 136, 146),
    "scrollarea_bg":     "#0a0b0d",
}

LIGHT = {
    "bg":                QColor(244, 245, 247),   # #f4f5f7
    "chassis":           QColor(244, 245, 247),
    "deck":              _a(_INK, 0.035),
    "deck_high":         _a(_INK, 0.07),
    "rail":              _a(_INK, 0.12),
    "rail_light":        _a(_INK, 0.24),
    "glass":             _a(_INK, 0.035),
    "glass_high":        _a(_INK, 0.07),
    "glass_edge":        _a(_INK, 0.16),
    "scrim":             _a(_WHITE, 0.72),
    # a mid neutral is the correct surround for judging a photograph
    "canvas_bg":         QColor(150, 149, 145),
    "well":              QColor(150, 149, 145),
    "tile_placeholder":  _a(_INK, 0.07),
    "filmstrip_bg":      _a(_WHITE, 0.60),
    "cap":               _a(_INK, 0.10),
    "cap_alt":           _a(_INK, 0.055),
    "cap_dark":          _a(_INK, 0.05),
    "cap_dark_hover":    _a(_INK, 0.10),
    "legend":            QColor(22, 25, 29),
    "legend_dark":       QColor(22, 25, 29),
    "fg":                QColor(22, 25, 29),      # #16191d
    "muted":             QColor(86, 92, 100),     # #565c64
    "canvas_fg":         QColor(22, 24, 27),      # on the mid-grey well
    "faint":             QColor(98, 104, 112),
    "accent":            ACCENT,
    "accent_text":       QColor(0x1d, 0x4e, 0xd8),
    "focus":             QColor(0x1d, 0x4e, 0xd8),
    "amber":             QColor(0x1d, 0x4e, 0xd8),
    "danger":            QColor(186, 58, 47),
    "screen_bg":         _a(_INK, 0.035),
    "screen_edge":       _a(_INK, 0.12),
    "screen_fg":         QColor(22, 25, 29),
    "screen_dim":        QColor(86, 92, 100),
    "status_bar_bg":     "#f4f5f7",
    "status_bar_fg":     "#16191d",
    "hint_bar_bg":       "#f4f5f7",
    "hint_bar_fg":       "#16191d",
    "empty_dash":        _a(_INK, 0.18),
    "empty_title":       QColor(22, 25, 29),
    "empty_hint":        QColor(86, 92, 100),
    "empty_footer":      QColor(98, 104, 112),
    "scrollarea_bg":     "#969591",
}

# Two modes. Stored keys stay "dark"/"light" so existing settings carry over.
THEMES = {"dark": DARK, "light": LIGHT}

RADIUS = 6      # every rounded corner in the app
HAIRLINE = 1


def theme_names() -> list[str]:
    return list(THEMES)


def label(name: str) -> str:
    return "Light" if name == "light" else "Dark"


_current = "dark"


def current() -> str:
    return _current


def toggle(name: str | None = None) -> str:
    """The other mode. This is the whole theme system."""
    return "dark" if (name or _current) == "light" else "light"


def set_theme(name: str) -> None:
    global _current
    if name in THEMES:
        _current = name


def is_light() -> bool:
    return _current == "light"


def c(key: str):
    return THEMES[_current].get(key, DARK.get(key))


def hexc(key: str) -> str:
    """Token as a QSS colour. Translucent tokens come back as rgba() so the
    glass survives the trip into a stylesheet — `.name()` drops the alpha."""
    v = c(key)
    if isinstance(v, QColor):
        if v.alpha() < 255:
            return f"rgba({v.red()}, {v.green()}, {v.blue()}, {v.alpha()})"
        return v.name()
    return str(v)


def solid(key: str) -> QColor:
    """A translucent token flattened onto the ground — for the few surfaces
    that must be opaque (menus, popups, anything floating over a photo)."""
    v = c(key)
    v = v if isinstance(v, QColor) else QColor(v)
    if v.alpha() == 255:
        return QColor(v)
    base = c("bg")
    f = v.alpha() / 255
    return QColor(round(base.red() * (1 - f) + v.red() * f),
                  round(base.green() * (1 - f) + v.green() * f),
                  round(base.blue() * (1 - f) + v.blue() * f))


def solid_hex(key: str) -> str:
    return solid(key).name()


# ── Reactive styling ─────────────────────────────────────────────────────────

def bind(widget: QWidget, fn: Callable[[], str]) -> QWidget:
    """Style `widget` with `fn()` now, and again on every mode change.

    Use instead of `setStyleSheet(f"...")` wherever the sheet reads a token:
    the binding lives on the widget, so it dies with it — no registry, no leak.
    """
    widget._qss_fn = fn            # type: ignore[attr-defined]
    widget.setStyleSheet(fn())
    return widget


def restyle(root: QWidget | None = None) -> None:
    """Re-run every stylesheet binding and `theme_changed()` hook under `root`
    (or every top-level window), then repaint. This is a mode switch."""
    roots = [root] if root is not None else list(QApplication.topLevelWidgets())
    for r in roots:
        if r is None:
            continue
        for w in (r, *r.findChildren(QWidget)):
            fn = getattr(w, "_qss_fn", None)
            if fn is not None:
                try:
                    w.setStyleSheet(fn())
                except RuntimeError:     # widget already deleted on the C++ side
                    continue
            hook = getattr(w, "theme_changed", None)
            if callable(hook):
                hook()
        r.update()


# ── Lettering ─────────────────────────────────────────────────────────────────
# Swiss: one clean sans for all chrome, set small, semibold and lightly tracked
# for labels. Counts and readouts use a monospace face so columns of numbers
# stay steady while they change.

_UI_CANDIDATES = ("Segoe UI Variable Text", "Segoe UI Variable", "Segoe UI",
                  "SF Pro Text", ".AppleSystemUIFont", "Helvetica Neue",
                  "Inter", "Noto Sans", "Cantarell", "Ubuntu", "DejaVu Sans")
_MONO_CANDIDATES = ("Cascadia Mono", "Cascadia Code", "Consolas", "SF Mono",
                    "Menlo", "DejaVu Sans Mono", "Liberation Mono", "Monaco")
_ui_family_cache: str | None = None
_mono_family_cache: str | None = None


def ui_family() -> str:
    global _ui_family_cache
    if _ui_family_cache is None:
        available = set(QFontDatabase.families())
        _ui_family_cache = next(
            (f for f in _UI_CANDIDATES if f in available), QFont().family())
    return _ui_family_cache


def mono_family() -> str:
    global _mono_family_cache
    if _mono_family_cache is None:
        available = set(QFontDatabase.families())
        _mono_family_cache = next(
            (f for f in _MONO_CANDIDATES if f in available), "monospace")
    return _mono_family_cache


def legend_font(size: int = 10, *, bold: bool = True, tracking: float = 1.4) -> QFont:
    """Chrome label: UI sans, semibold, lightly tracked. `tracking` is kept
    from the older mono labels and scaled down — sans needs far less air."""
    f = QFont(ui_family(), size)
    f.setWeight(QFont.Weight.DemiBold if bold else QFont.Weight.Normal)
    f.setLetterSpacing(QFont.SpacingType.AbsoluteSpacing, round(tracking * 0.35, 2))
    f.setStyleStrategy(QFont.StyleStrategy.PreferAntialias)
    return f


def readout_font(size: int = 13, *, bold: bool = True) -> QFont:
    """Live values — steady-width digits, one number at a time."""
    f = QFont(mono_family(), size)
    f.setWeight(QFont.Weight.DemiBold if bold else QFont.Weight.Normal)
    return f


def display_font(size: int = 26) -> QFont:
    f = QFont(ui_family(), size)
    f.setWeight(QFont.Weight.DemiBold)
    f.setLetterSpacing(QFont.SpacingType.AbsoluteSpacing, -0.4)
    return f


def apply(app: QApplication, name: str) -> None:
    """Set the mode + QApplication palette. Call `restyle()` afterwards to
    re-run stylesheet bindings on widgets that already exist."""
    set_theme(name)
    pal = QPalette()
    pal.setColor(QPalette.ColorRole.Window, c("bg"))
    pal.setColor(QPalette.ColorRole.WindowText, c("fg"))
    pal.setColor(QPalette.ColorRole.Base, solid("deck"))
    pal.setColor(QPalette.ColorRole.AlternateBase, solid("deck_high"))
    pal.setColor(QPalette.ColorRole.Text, c("fg"))
    pal.setColor(QPalette.ColorRole.Button, solid("cap_dark"))
    pal.setColor(QPalette.ColorRole.ButtonText, c("fg"))
    pal.setColor(QPalette.ColorRole.Highlight, c("accent"))
    pal.setColor(QPalette.ColorRole.HighlightedText, QColor(255, 255, 255))
    pal.setColor(QPalette.ColorRole.ToolTipBase, solid("deck_high"))
    pal.setColor(QPalette.ColorRole.ToolTipText, c("fg"))
    pal.setColor(QPalette.ColorRole.PlaceholderText, c("faint"))
    app.setPalette(pal)
    f = QFont(ui_family(), app.font().pointSize())
    app.setFont(f)


def match_titlebar(window: QWidget) -> None:
    """Windows 10/11: make the native title bar follow the mode, so the frame
    doesn't stay white above a dark app. No-op elsewhere or on failure."""
    import sys
    if sys.platform != "win32":
        return
    try:
        import ctypes
        hwnd = int(window.winId())
        value = ctypes.c_int(0 if is_light() else 1)
        for attr in (20, 19):   # DWMWA_USE_IMMERSIVE_DARK_MODE (new, pre-20H1)
            if ctypes.windll.dwmapi.DwmSetWindowAttribute(
                    hwnd, attr, ctypes.byref(value), ctypes.sizeof(value)) == 0:
                break
    except Exception:
        pass


_chevrons: dict[str, str] = {}


def _chevron_url() -> str:
    """A small drawn chevron for combo boxes, rendered once per colour to a PNG
    (QSS can't draw one, and the CSS border-triangle trick renders as a bar)."""
    col = c("muted")
    key = col.name()
    if key not in _chevrons:
        import os
        from PyQt6.QtCore import QPointF, Qt
        from PyQt6.QtGui import QImage, QPainter, QPen
        img = QImage(20, 12, QImage.Format.Format_ARGB32_Premultiplied)
        img.fill(Qt.GlobalColor.transparent)
        p = QPainter(img)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        pen = QPen(col, 2.2)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        p.setPen(pen)
        p.drawPolyline([QPointF(4, 3), QPointF(10, 9), QPointF(16, 3)])
        p.end()
        # Per-user app folder, never the shared temp dir: on Linux /tmp is
        # world-writable, and a predictable name there could be pre-planted as
        # a symlink that makes us overwrite some other file of the user's.
        try:
            from . import settings as settings_mod
            folder = settings_mod._config_dir()
            os.makedirs(folder, exist_ok=True)
            path = os.path.join(str(folder), f"chevron-{key[1:]}.png")
            if not img.save(path):
                return ""
        except Exception:
            return ""
        _chevrons[key] = path.replace("\\", "/")
    return _chevrons[key]


def _qss_tokens() -> dict:
    return {
        "win": hexc("bg"), "panel": hexc("deck"), "panel_high": hexc("deck_high"),
        "panel_solid": solid_hex("deck"), "popup": solid_hex("deck_high"),
        "border": hexc("rail"), "edge": hexc("glass_edge"),
        "fg": hexc("fg"), "muted": hexc("muted"), "faint": hexc("faint"),
        "accent": hexc("accent"), "accent_fg": "#ffffff",
        "cap": hexc("cap"), "cap_alt": hexc("cap_alt"),
        "ui": ui_family(), "mono": mono_family(),
        "scroll": hexc("rail_light"), "scroll_hover": hexc("muted"),
        "danger": hexc("danger"), "r": RADIUS,
    }


def main_window_qss() -> str:
    """Window chrome: transparent bars on the ground, hairline edges, slim
    scroll tracks. Popups flatten their glass so they stay legible anywhere."""
    t = _qss_tokens()
    return f"""
    QMainWindow, QWidget#centralHost {{ background: {t['win']}; }}

    QMenuBar {{
        background: transparent;
        color: {t['fg']};
        padding: 2px 6px;
        border-bottom: 1px solid {t['border']};
        font-family: "{t['ui']}";
        font-size: 12px;
    }}
    QMenuBar::item {{
        background: transparent;
        padding: 6px 11px;
        margin: 2px 1px;
        border-radius: {t['r']}px;
        color: {t['muted']};
    }}
    QMenuBar::item:selected {{ background: {t['panel_high']}; color: {t['fg']}; }}
    QMenuBar::item:pressed  {{ background: {t['accent']}; color: {t['accent_fg']}; }}

    QMenu {{
        background: {t['popup']};
        color: {t['fg']};
        border: 1px solid {t['edge']};
        border-radius: {t['r']}px;
        padding: 6px;
        font-family: "{t['ui']}";
        font-size: 12px;
    }}
    QMenu::item {{
        background: transparent;
        padding: 7px 18px 7px 12px;
        margin: 1px;
        border-radius: 4px;
    }}
    QMenu::item:selected {{ background: {t['accent']}; color: {t['accent_fg']}; }}
    QMenu::item:disabled {{ color: {t['faint']}; background: transparent; }}
    QMenu::separator {{ height: 1px; background: {t['border']}; margin: 5px 6px; }}
    QMenu::icon {{ padding-left: 6px; }}
    QMenu::indicator {{ width: 16px; height: 16px; }}

    QStatusBar {{
        background: transparent;
        color: {t['muted']};
        border-top: 1px solid {t['border']};
        padding: 2px 8px;
        font-family: "{t['ui']}";
        font-size: 11px;
    }}
    QStatusBar::item {{ border: 0; }}
    QStatusBar QLabel {{ color: {t['muted']}; }}

    QToolTip {{
        background: {t['popup']};
        color: {t['fg']};
        border: 1px solid {t['edge']};
        border-radius: 4px;
        padding: 6px 9px;
        font-family: "{t['ui']}";
        font-size: 11px;
    }}

    QScrollBar:vertical {{ background: transparent; width: 10px; margin: 2px; }}
    QScrollBar::handle:vertical {{
        background: {t['scroll']}; border-radius: 3px; min-height: 36px;
    }}
    QScrollBar::handle:vertical:hover {{ background: {t['scroll_hover']}; }}
    QScrollBar:horizontal {{ background: transparent; height: 10px; margin: 2px; }}
    QScrollBar::handle:horizontal {{
        background: {t['scroll']}; border-radius: 3px; min-width: 36px;
    }}
    QScrollBar::handle:horizontal:hover {{ background: {t['scroll_hover']}; }}
    QScrollBar::add-line, QScrollBar::sub-line {{ width: 0; height: 0; }}
    QScrollBar::add-page, QScrollBar::sub-page {{ background: transparent; }}
    """


# ── Shared dialog stylesheet ─────────────────────────────────────────────────

ACCENT_HEX = "#3b82f6"
ACCENT_SOLID = "#2563eb"    # filled buttons: white text needs >= 4.5:1
ACCENT_HOVER = "#3b74f0"
ACCENT_PRESSED = "#1d4ed8"


def dialog_qss() -> str:
    """Dialogs are the same glass: translucent groups, hairline edges, one
    filled button per screen."""
    t = _qss_tokens()
    a, fill, hover, pressed = ACCENT_HEX, ACCENT_SOLID, ACCENT_HOVER, ACCENT_PRESSED
    return f"""
    QDialog {{ background: {t['win']}; }}
    QWidget {{ color: {t['fg']}; font-family: "{t['ui']}"; font-size: 13px; }}
    QLabel {{ color: {t['fg']}; background: transparent; }}
    QLabel#title {{ color: {t['fg']}; font-size: 18px; font-weight: 600; }}
    QLabel#subtitle {{ color: {t['muted']}; font-size: 12px; }}
    QLabel#hint {{ color: {t['muted']}; font-size: 11px; }}
    QLabel#detected {{ color: {t['muted']}; font-size: 11px; font-style: italic; }}

    QGroupBox {{
        color: {t['fg']};
        border: 1px solid {t['border']};
        border-radius: {t['r']}px;
        margin-top: 22px;
        padding: 20px 16px 16px 16px;
        background: {t['panel']};
    }}
    QGroupBox::title {{
        subcontrol-origin: margin;
        subcontrol-position: top left;
        left: 4px;
        padding: 2px 4px;
        color: {t['muted']};
        font-size: 11px;
        font-weight: 600;
        background: transparent;
    }}

    QLineEdit, QComboBox, QSpinBox, QDoubleSpinBox {{
        background: {t['panel']};
        color: {t['fg']};
        border: 1px solid {t['border']};
        border-radius: {t['r']}px;
        padding: 7px 10px;
        font-size: 12px;
        selection-background-color: {a};
        selection-color: #ffffff;
    }}
    QLineEdit:hover, QComboBox:hover, QSpinBox:hover, QDoubleSpinBox:hover {{
        border-color: {t['edge']};
    }}
    QLineEdit:focus, QComboBox:focus, QSpinBox:focus, QDoubleSpinBox:focus {{
        border: 1px solid {a};
    }}
    QComboBox::drop-down {{ border: 0; width: 24px; }}
    QComboBox::down-arrow {{
        image: url("{_chevron_url()}"); width: 10px; height: 6px; margin-right: 10px;
    }}
    QComboBox QAbstractItemView {{
        background: {t['popup']}; color: {t['fg']};
        border: 1px solid {t['edge']}; border-radius: {t['r']}px;
        selection-background-color: {a}; selection-color: #ffffff;
        padding: 4px; outline: 0;
    }}
    QSpinBox::up-button, QDoubleSpinBox::up-button,
    QSpinBox::down-button, QDoubleSpinBox::down-button {{
        background: transparent; border: 0; width: 16px;
    }}

    QCheckBox {{ color: {t['fg']}; spacing: 10px; padding: 3px 0; }}
    QCheckBox::indicator {{
        width: 16px; height: 16px; border-radius: 4px;
        border: 1px solid {t['edge']}; background: {t['panel']};
    }}
    QCheckBox::indicator:hover {{ border-color: {t['muted']}; }}
    QCheckBox::indicator:checked {{ background: {a}; border-color: {a}; }}

    QRadioButton {{ color: {t['fg']}; spacing: 9px; padding: 3px 0; }}
    QRadioButton::indicator {{
        width: 14px; height: 14px; border-radius: 8px;
        border: 1px solid {t['edge']}; background: {t['panel']};
    }}
    QRadioButton::indicator:checked {{ border: 4px solid {a}; background: {t['panel']}; }}

    QPushButton {{
        background: {t['cap_alt']};
        color: {t['fg']};
        border: 1px solid {t['border']};
        border-radius: {t['r']}px;
        padding: 8px 16px;
        font-size: 12px;
        font-weight: 600;
    }}
    QPushButton:hover {{ background: {t['cap']}; border-color: {t['edge']}; }}
    QPushButton:pressed {{ background: {t['panel_high']}; }}
    QPushButton:disabled {{ background: transparent; color: {t['faint']}; }}
    QPushButton#primary {{
        background: {fill}; border: 1px solid {fill}; color: #ffffff; padding: 9px 20px;
    }}
    QPushButton#primary:hover {{ background: {hover}; border-color: {hover}; }}
    QPushButton#primary:pressed {{ background: {pressed}; }}
    QPushButton#danger {{
        background: transparent; color: {t['danger']};
        border: 1px solid {t['border']};
    }}
    QPushButton#danger:hover {{ background: {t['danger']}; color: #ffffff; border-color: {t['danger']}; }}

    /* Keyboard focus is never removed. */
    QPushButton:focus {{ border: 2px solid {a}; }}
    QCheckBox:focus::indicator, QRadioButton:focus::indicator {{ border: 2px solid {a}; }}

    QFrame#divider {{ background: {t['border']}; max-height: 1px; border: 0; }}

    QScrollBar:vertical {{ background: transparent; width: 10px; margin: 2px; }}
    QScrollBar::handle:vertical {{ background: {t['scroll']}; border-radius: 3px; min-height: 36px; }}
    QScrollBar::handle:vertical:hover {{ background: {t['scroll_hover']}; }}
    QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}
    QScrollBar::add-page, QScrollBar::sub-page {{ background: transparent; }}
    """
