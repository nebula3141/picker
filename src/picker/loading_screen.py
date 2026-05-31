"""Animated loading dialog. Replaces static QSplashScreen.

Shows spinner + progress bar so users know the app is alive during folder
scans. Supports indeterminate (pulsing segment) and determinate (0..1 fill)
modes via set_progress()."""
import time

from PyQt6.QtWidgets import QDialog, QApplication
from PyQt6.QtCore import Qt, QTimer, QRect, QSize
from PyQt6.QtGui import QPainter, QColor, QFont, QPen, QFontMetrics

from picker.icon import app_icon


class LoadingScreen(QDialog):
    WIDTH = 540
    HEIGHT = 260
    LOGO_SIZE = 48

    def __init__(self, title: str = "PICker", sub: str = "Loading…", parent=None):
        super().__init__(
            parent,
            Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint,
        )
        self.setFixedSize(self.WIDTH, self.HEIGHT)
        self.setModal(False)
        try:
            from picker import __version__ as _ver
        except Exception:
            _ver = ""
        self._title = title
        self._sub = sub
        self._tagline = "capturing moments, minus the noise."
        self._version = _ver
        self._angle = 0
        self._progress = -1.0          # -1 = indeterminate
        self._pulse_pos = 0.0          # 0..1 for indeterminate band
        self._logo = app_icon().pixmap(QSize(self.LOGO_SIZE, self.LOGO_SIZE))
        self.setAccessibleName(f"Loading: {sub}")

        self._timer = QTimer(self)
        self._timer.setInterval(33)    # ~30fps
        self._timer.timeout.connect(self._tick)
        self._timer.start()
        self._shown_at = time.perf_counter()

        self._center_on_screen()

    def _center_on_screen(self):
        scr = QApplication.primaryScreen().availableGeometry()
        self.move(
            scr.center().x() - self.WIDTH // 2,
            scr.center().y() - self.HEIGHT // 2,
        )

    def _tick(self):
        self._angle = (self._angle + 14) % 360
        self._pulse_pos = (self._pulse_pos + 0.012) % 1.0
        self.update()

    # ── API ────────────────────────────────────────────────────────────────────

    def set_text(self, sub: str):
        self._sub = sub
        self.update()

    def set_progress(self, value: float):
        """0.0..1.0 for determinate, negative for indeterminate."""
        self._progress = value
        self.update()
        QApplication.processEvents()

    def wait_minimum(self, seconds: float) -> None:
        """Block until the screen has been visible for at least N seconds,
        keeping the UI responsive (animation + user events)."""
        end = self._shown_at + seconds
        while time.perf_counter() < end:
            QApplication.processEvents()
            QTimer.singleShot(20, lambda: None)
            time.sleep(0.02)

    def close_smoothly(self):
        self._timer.stop()
        self.hide()
        self.deleteLater()

    # ── Paint ──────────────────────────────────────────────────────────────────

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)

        # Card background
        p.fillRect(self.rect(), QColor(18, 18, 18))
        p.setPen(QPen(QColor(42, 130, 218), 2))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawRoundedRect(1, 1, self.width() - 2, self.height() - 2, 12, 12)

        # ── Brand row: logo + title, centered as a group ──────────────────────
        title_font = QFont()
        title_font.setPointSize(26)
        title_font.setBold(True)
        p.setFont(title_font)
        fm = QFontMetrics(title_font)
        title_w = fm.horizontalAdvance(self._title)
        gap = 14
        row_w = self.LOGO_SIZE + gap + title_w
        row_y = 26
        row_x = (self.width() - row_w) // 2

        p.drawPixmap(row_x, row_y, self.LOGO_SIZE, self.LOGO_SIZE, self._logo)
        p.setPen(QColor(255, 255, 255))
        p.drawText(
            QRect(row_x + self.LOGO_SIZE + gap, row_y, title_w + 2, self.LOGO_SIZE),
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
            self._title,
        )

        # Tagline
        f = QFont()
        f.setPointSize(10)
        f.setItalic(True)
        p.setFont(f)
        p.setPen(QColor(140, 140, 140))
        p.drawText(QRect(0, row_y + self.LOGO_SIZE + 10, self.width(), 20),
                   Qt.AlignmentFlag.AlignCenter, self._tagline)

        # Version chip
        if self._version:
            f.setItalic(False)
            f.setPointSize(8)
            f.setBold(True)
            p.setFont(f)
            p.setPen(QColor(106, 158, 255))
            p.drawText(QRect(0, row_y + self.LOGO_SIZE + 34, self.width(), 14),
                       Qt.AlignmentFlag.AlignCenter, f"VERSION {self._version}")

        # Spinner
        spinner_cy = 158
        r = 14
        cx = self.width() // 2
        p.setPen(QPen(QColor(55, 55, 55), 3))
        p.drawEllipse(cx - r, spinner_cy - r, 2 * r, 2 * r)
        p.setPen(QPen(QColor(42, 130, 218), 3, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        p.drawArc(cx - r, spinner_cy - r, 2 * r, 2 * r, -self._angle * 16, 100 * 16)

        # Sub text
        p.setPen(QColor(220, 220, 220))
        f.setPointSize(10)
        f.setBold(True)
        f.setItalic(False)
        p.setFont(f)
        p.drawText(QRect(0, 188, self.width(), 20),
                   Qt.AlignmentFlag.AlignCenter, self._sub)

        # Progress bar
        bar_w = 400
        bar_h = 6
        bx = (self.width() - bar_w) // 2
        by = 220
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(40, 40, 40))
        p.drawRoundedRect(bx, by, bar_w, bar_h, 3, 3)

        p.setBrush(QColor(42, 130, 218))
        if self._progress < 0:
            seg_w = bar_w // 3
            span = bar_w + seg_w
            pos = int(self._pulse_pos * span) - seg_w
            left = max(0, pos)
            right = min(bar_w, pos + seg_w)
            width = right - left
            if width > 0:
                p.drawRoundedRect(bx + left, by, width, bar_h, 3, 3)
        else:
            fill = int(bar_w * max(0.0, min(1.0, self._progress)))
            if fill > 0:
                p.drawRoundedRect(bx, by, fill, bar_h, 3, 3)

            # Percentage caption
            pct = int(self._progress * 100)
            p.setPen(QColor(140, 140, 140))
            f.setPointSize(9)
            f.setBold(False)
            p.setFont(f)
            p.drawText(QRect(0, by + 10, self.width(), 16),
                       Qt.AlignmentFlag.AlignCenter, f"{pct}%")

        p.end()
