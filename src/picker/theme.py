"""Dark / light themes. Live-swappable via apply(app, name).

Widgets read colors via `c(key)` at paint-time so theme switches take effect
without rebuilding the UI. Dialogs intentionally stay dark (their QSS is
hardcoded) to keep the brand look consistent; only viewing surfaces swap."""
from PyQt6.QtGui import QPalette, QColor
from PyQt6.QtWidgets import QApplication


DARK = {
    "bg":                QColor(10, 10, 10),
    "canvas_bg":         QColor(17, 17, 17),
    "tile_placeholder":  QColor(22, 22, 22),
    "filmstrip_bg":      QColor(10, 10, 10),
    "fg":                QColor(220, 220, 220),
    "muted":             QColor(140, 140, 140),
    "accent":            QColor(42, 130, 218),
    "status_bar_bg":     "#1a1a2e",
    "status_bar_fg":     "#dddddd",
    "hint_bar_bg":       "#1a1a2e",
    "hint_bar_fg":       "#dddddd",
    "empty_dash":        QColor(70, 70, 70),
    "empty_title":       QColor(230, 230, 230),
    "empty_hint":        QColor(140, 140, 140),
    "empty_footer":      QColor(100, 100, 100),
    "scrollarea_bg":     "#000000",
}

LIGHT = {
    "bg":                QColor(246, 246, 248),
    "canvas_bg":         QColor(252, 252, 254),
    "tile_placeholder":  QColor(222, 222, 228),
    "filmstrip_bg":      QColor(232, 232, 238),
    "fg":                QColor(30, 30, 32),
    "muted":             QColor(110, 110, 118),
    "accent":            QColor(42, 130, 218),
    "status_bar_bg":     "#e4e4ec",
    "status_bar_fg":     "#222222",
    "hint_bar_bg":       "#e4e4ec",
    "hint_bar_fg":       "#222222",
    "empty_dash":        QColor(180, 180, 190),
    "empty_title":       QColor(30, 30, 32),
    "empty_hint":        QColor(110, 110, 118),
    "empty_footer":      QColor(150, 150, 158),
    "scrollarea_bg":     "#f6f6f8",
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
    pal.setColor(QPalette.ColorRole.ToolTipBase, QColor(255, 255, 220))
    pal.setColor(QPalette.ColorRole.ToolTipText, QColor(0, 0, 0))
    app.setPalette(pal)


def main_window_qss() -> str:
    """QSS used on MainWindow to avoid native-flash before child paints."""
    bg = c("bg").name()
    return f"QMainWindow {{ background: {bg}; }}"
