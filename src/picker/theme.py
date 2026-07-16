"""Dark / light themes. Live-swappable via apply(app, name).

Widgets read colors via `c(key)` at paint-time so theme switches take effect
without rebuilding the UI. Dialogs intentionally stay dark (their QSS is
hardcoded) to keep the brand look consistent; only viewing surfaces swap."""
from PyQt6.QtGui import QPalette, QColor
from PyQt6.QtWidgets import QApplication


# Modern accent — a clean, slightly brighter blue that reads well on both
# near-black and light surfaces. Shared by both themes.
ACCENT = QColor(59, 130, 246)   # #3b82f6

DARK = {
    "bg":                QColor(11, 11, 13),
    "canvas_bg":         QColor(16, 16, 18),
    "tile_placeholder":  QColor(24, 24, 27),
    "filmstrip_bg":      QColor(13, 13, 15),
    "fg":                QColor(230, 230, 235),
    "muted":             QColor(138, 138, 147),
    "accent":            ACCENT,
    "status_bar_bg":     "#111114",
    "status_bar_fg":     "#c9c9d0",
    "hint_bar_bg":       "#111114",
    "hint_bar_fg":       "#c9c9d0",
    "empty_dash":        QColor(62, 62, 68),
    "empty_title":       QColor(232, 232, 236),
    "empty_hint":        QColor(138, 138, 147),
    "empty_footer":      QColor(96, 96, 104),
    "scrollarea_bg":     "#000000",
}

LIGHT = {
    "bg":                QColor(244, 244, 247),
    "canvas_bg":         QColor(250, 250, 252),
    "tile_placeholder":  QColor(224, 224, 230),
    "filmstrip_bg":      QColor(235, 235, 240),
    "fg":                QColor(28, 28, 32),
    "muted":             QColor(106, 106, 114),
    "accent":            ACCENT,
    "status_bar_bg":     "#e9e9ef",
    "status_bar_fg":     "#33333a",
    "hint_bar_bg":       "#e9e9ef",
    "hint_bar_fg":       "#33333a",
    "empty_dash":        QColor(180, 180, 190),
    "empty_title":       QColor(28, 28, 32),
    "empty_hint":        QColor(106, 106, 114),
    "empty_footer":      QColor(150, 150, 158),
    "scrollarea_bg":     "#f4f4f7",
}

THEMES = {"dark": DARK, "light": LIGHT}

_current = "dark"


def current() -> str:
    return _current


def set_theme(name: str) -> None:
    global _current
    if name in THEMES:
        _current = name


def c(key: str):
    return THEMES[_current].get(key, DARK.get(key))


def apply(app: QApplication, name: str) -> None:
    """Set theme + QApplication palette. Does not repaint existing widgets;
    caller should trigger updates (usually window.update() suffices because
    paintEvent reads via c())."""
    set_theme(name)
    pal = QPalette()
    accent = c("accent")
    if name == "light":
        pal.setColor(QPalette.ColorRole.Window, QColor(240, 240, 244))
        pal.setColor(QPalette.ColorRole.WindowText, QColor(30, 30, 32))
        pal.setColor(QPalette.ColorRole.Base, QColor(255, 255, 255))
        pal.setColor(QPalette.ColorRole.AlternateBase, QColor(230, 230, 238))
        pal.setColor(QPalette.ColorRole.Text, QColor(30, 30, 32))
        pal.setColor(QPalette.ColorRole.Button, QColor(228, 228, 236))
        pal.setColor(QPalette.ColorRole.ButtonText, QColor(30, 30, 32))
        pal.setColor(QPalette.ColorRole.HighlightedText, QColor(255, 255, 255))
    else:
        pal.setColor(QPalette.ColorRole.Window, QColor(30, 30, 30))
        pal.setColor(QPalette.ColorRole.WindowText, QColor(220, 220, 220))
        pal.setColor(QPalette.ColorRole.Base, QColor(20, 20, 20))
        pal.setColor(QPalette.ColorRole.AlternateBase, QColor(40, 40, 40))
        pal.setColor(QPalette.ColorRole.Text, QColor(220, 220, 220))
        pal.setColor(QPalette.ColorRole.Button, QColor(50, 50, 50))
        pal.setColor(QPalette.ColorRole.ButtonText, QColor(220, 220, 220))
        pal.setColor(QPalette.ColorRole.HighlightedText, QColor(0, 0, 0))
    pal.setColor(QPalette.ColorRole.Highlight, accent)
    # Modern dark tooltip (was the dated yellow sticky-note look).
    if name == "light":
        pal.setColor(QPalette.ColorRole.ToolTipBase, QColor(38, 38, 43))
        pal.setColor(QPalette.ColorRole.ToolTipText, QColor(240, 240, 242))
    else:
        pal.setColor(QPalette.ColorRole.ToolTipBase, QColor(31, 31, 36))
        pal.setColor(QPalette.ColorRole.ToolTipText, QColor(234, 234, 239))
    app.setPalette(pal)


def _qss_tokens() -> dict:
    """Hex tokens for the application stylesheet, per active theme. Accent is
    kept in sync with c('accent') so menus match the (hard-coded) dialog QSS."""
    accent = c("accent").name() if hasattr(c("accent"), "name") else str(c("accent"))
    if _current == "light":
        return {
            "win": c("bg").name(), "panel": "#ffffff", "border": "#e4e4ea",
            "fg": "#1e1e20", "muted": "#6a6a72", "hover": "#ececf1",
            "accent": accent, "accent_fg": "#ffffff", "sep": "#ececf1",
            "scroll": "#c9c9d2", "scroll_hover": "#adadb8",
            "tip_bg": "#26262b", "tip_fg": "#f0f0f2", "tip_border": "#3a3a42",
        }
    return {
        "win": c("bg").name(), "panel": "#1b1b1f", "border": "#2b2b31",
        "fg": "#e6e6ea", "muted": "#8a8a93", "hover": "#26262c",
        "accent": accent, "accent_fg": "#ffffff", "sep": "#2a2a30",
        "scroll": "#3a3a42", "scroll_hover": "#4c4c56",
        "tip_bg": "#1f1f24", "tip_fg": "#eaeaef", "tip_border": "#34343c",
    }


def main_window_qss() -> str:
    """Modern application chrome. Applied to MainWindow; cascades to the menu
    bar, drop-down menus, every right-click context menu, the status bar,
    scrollbars, and tooltips. Rebuilt on theme switch."""
    t = _qss_tokens()
    return f"""
    QMainWindow, QWidget#centralHost {{ background: {t['win']}; }}

    /* ── Menu bar ─────────────────────────────────────────────── */
    QMenuBar {{
        background: transparent;
        color: {t['fg']};
        padding: 3px 6px;
        border: 0;
    }}
    QMenuBar::item {{
        background: transparent;
        padding: 6px 12px;
        margin: 0 1px;
        border-radius: 7px;
    }}
    QMenuBar::item:selected {{ background: {t['hover']}; }}
    QMenuBar::item:pressed  {{ background: {t['accent']}; color: {t['accent_fg']}; }}

    /* ── Menus + every right-click context menu ───────────────── */
    QMenu {{
        background: {t['panel']};
        color: {t['fg']};
        border: 1px solid {t['border']};
        border-radius: 12px;
        padding: 6px;
        font-size: 13px;
    }}
    QMenu::item {{
        background: transparent;
        padding: 7px 14px 7px 12px;
        margin: 1px 2px;
        border-radius: 8px;
    }}
    QMenu::item:selected {{ background: {t['accent']}; color: {t['accent_fg']}; }}
    QMenu::item:disabled {{ color: {t['muted']}; background: transparent; }}
    QMenu::separator {{ height: 1px; background: {t['sep']}; margin: 5px 8px; }}
    QMenu::icon {{ padding-left: 6px; }}
    QMenu::indicator {{ width: 16px; height: 16px; }}
    QMenu::right-arrow {{ width: 12px; height: 12px; margin-right: 6px; }}

    /* ── Status bar ───────────────────────────────────────────── */
    QStatusBar {{
        background: {t['win']};
        color: {t['muted']};
        border-top: 1px solid {t['border']};
        padding: 2px 6px;
    }}
    QStatusBar::item {{ border: 0; }}
    QStatusBar QLabel {{ color: {t['muted']}; }}

    /* ── Tooltips ─────────────────────────────────────────────── */
    QToolTip {{
        background: {t['tip_bg']};
        color: {t['tip_fg']};
        border: 1px solid {t['tip_border']};
        border-radius: 8px;
        padding: 6px 10px;
        font-size: 12px;
    }}

    /* ── Scrollbars (thin, rounded, subtle) ───────────────────── */
    QScrollBar:vertical {{ background: transparent; width: 12px; margin: 2px; }}
    QScrollBar::handle:vertical {{
        background: {t['scroll']}; border-radius: 5px; min-height: 40px;
    }}
    QScrollBar::handle:vertical:hover {{ background: {t['scroll_hover']}; }}
    QScrollBar:horizontal {{ background: transparent; height: 12px; margin: 2px; }}
    QScrollBar::handle:horizontal {{
        background: {t['scroll']}; border-radius: 5px; min-width: 40px;
    }}
    QScrollBar::handle:horizontal:hover {{ background: {t['scroll_hover']}; }}
    QScrollBar::add-line, QScrollBar::sub-line {{ width: 0; height: 0; }}
    QScrollBar::add-page, QScrollBar::sub-page {{ background: transparent; }}
    """


# ── Shared modern dialog stylesheet ───────────────────────────────────────────
# Dialogs stay dark in both themes (consistent brand look). This is the single
# source of truth for their chrome: rounded cards, roomy padding, modern inputs
# and toggles. Individual dialogs append their own object-name rules.

ACCENT_HEX = "#3b82f6"
ACCENT_HOVER = "#5a9bff"
ACCENT_PRESSED = "#2f6fe0"


def dialog_qss() -> str:
    a, ah, ap = ACCENT_HEX, ACCENT_HOVER, ACCENT_PRESSED
    return f"""
    QDialog {{ background: #141416; }}
    QWidget {{ color: #e6e6ea; font-size: 13px; }}
    QLabel {{ color: #d6d6dc; }}
    QLabel#title {{ color: #ffffff; font-size: 17px; font-weight: 700; letter-spacing: -0.3px; }}
    QLabel#subtitle {{ color: #8a8a93; font-size: 12px; }}
    QLabel#hint {{ color: #6f6f78; font-size: 11px; }}
    QLabel#detected {{ color: #6f6f78; font-size: 11px; font-style: italic; }}

    QGroupBox {{
        color: #e2e2e8;
        font-weight: 600;
        border: 1px solid #26262c;
        border-radius: 14px;
        margin-top: 22px;
        padding: 24px 18px 18px 18px;
        background: #1a1a1e;
    }}
    QGroupBox::title {{
        subcontrol-origin: margin;
        subcontrol-position: top left;
        left: 16px;
        padding: 2px 8px;
        color: #8ea2c0;
        font-size: 10px;
        font-weight: 700;
        text-transform: uppercase;
        letter-spacing: 1.4px;
        background: #141416;
    }}

    QLineEdit, QComboBox, QSpinBox, QDoubleSpinBox {{
        background: #101012;
        color: #e8e8ec;
        border: 1px solid #2a2a30;
        border-radius: 9px;
        padding: 8px 11px;
        selection-background-color: {a};
    }}
    QLineEdit:hover, QComboBox:hover, QSpinBox:hover, QDoubleSpinBox:hover {{ border-color: #3a3a42; }}
    QLineEdit:focus, QComboBox:focus, QSpinBox:focus, QDoubleSpinBox:focus {{
        border: 1px solid {a}; background: #131316;
    }}
    QLineEdit::placeholder {{ color: #55555c; }}
    QComboBox::drop-down {{ border: 0; width: 26px; }}
    QComboBox::down-arrow {{
        image: none; width: 0; height: 0; margin-right: 10px;
        border-left: 4px solid transparent; border-right: 4px solid transparent;
        border-top: 5px solid #8a8a93;
    }}
    QComboBox QAbstractItemView {{
        background: #1b1b1f; color: #e6e6ea;
        border: 1px solid #2a2a30; border-radius: 10px;
        selection-background-color: {a}; padding: 5px; outline: 0;
    }}
    QSpinBox::up-button, QDoubleSpinBox::up-button,
    QSpinBox::down-button, QDoubleSpinBox::down-button {{ background: transparent; border: 0; width: 16px; }}

    QCheckBox {{ color: #d6d6dc; spacing: 10px; padding: 3px 0; }}
    QCheckBox::indicator {{
        width: 18px; height: 18px; border-radius: 6px;
        border: 1px solid #3a3a42; background: #101012;
    }}
    QCheckBox::indicator:hover {{ border-color: #55555f; }}
    QCheckBox::indicator:checked {{ background: {a}; border-color: {a}; }}

    QRadioButton {{ color: #d6d6dc; spacing: 9px; padding: 3px 0; }}
    QRadioButton::indicator {{
        width: 16px; height: 16px; border-radius: 9px;
        border: 1px solid #55555f; background: #101012;
    }}
    QRadioButton::indicator:checked {{
        border: 5px solid {a}; background: #ffffff;
    }}

    QPushButton {{
        background: #26262c; color: #e6e6ea;
        border: 1px solid #34343c; border-radius: 9px;
        padding: 8px 16px; font-size: 12px; font-weight: 500;
    }}
    QPushButton:hover {{ background: #2f2f37; border-color: #45454f; }}
    QPushButton:pressed {{ background: #202026; }}
    QPushButton:disabled {{ color: #55555c; background: #1c1c20; border-color: #26262c; }}
    QPushButton#primary {{
        background: {a}; border: 1px solid {a}; color: #ffffff;
        font-weight: 600; padding: 9px 22px;
    }}
    QPushButton#primary:hover {{ background: {ah}; border-color: {ah}; }}
    QPushButton#primary:pressed {{ background: {ap}; }}
    QPushButton#danger {{ background: transparent; color: #ef6b6b; border: 1px solid #45272a; }}
    QPushButton#danger:hover {{ background: #2a1618; border-color: #ef6b6b; color: #ffffff; }}

    QFrame#divider {{ background: #26262c; max-height: 1px; border: 0; }}

    QScrollBar:vertical {{ background: transparent; width: 12px; margin: 2px; }}
    QScrollBar::handle:vertical {{ background: #34343c; border-radius: 5px; min-height: 40px; }}
    QScrollBar::handle:vertical:hover {{ background: #45454f; }}
    QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}
    QScrollBar::add-page, QScrollBar::sub-page {{ background: transparent; }}
    """
