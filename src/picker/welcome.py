"""First-run welcome / coach marks.

A small three-card carousel shown once (guarded by the ``seen_welcome`` setting)
to teach the core workflow. Each card can show a banner screenshot from
``assets/coach/{1,2,3}.jpg`` — missing images degrade to a text-only card.
"""
from pathlib import Path

from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QWidget, QSizePolicy,
)
from PyQt6.QtCore import Qt, QSize
from PyQt6.QtGui import QPixmap, QPainter, QColor, QBrush, QPainterPath

from . import settings as settings_mod
from . import theme as theme_mod


_PAGES = [
    ("1.jpg", "Browse your photos",
     "Open a folder and PICker lays out its photos and subfolders. Use the "
     "arrow keys to move around, Enter to open, and Esc to step back."),
    ("2.jpg", "Sort in a flash",
     "Save a folder to the Selection menu once, then send the current photo "
     "there with Ctrl+Space (or Ctrl+1…9). PICker jumps to the next photo "
     "automatically — so you can fly through a whole shoot."),
    ("3.jpg", "Find & do more",
     "Ctrl+F searches your library. Right-click any photo to Copy Image, "
     "Rotate, or Show on Map. Press ? at any time to see every shortcut."),
]

_BANNER = QSize(540, 232)


def _coach_dir() -> Path:
    return Path(__file__).parent / "assets" / "coach"


def should_show() -> bool:
    return not bool(settings_mod.get("seen_welcome"))


def _cover_pixmap(src: QPixmap, size: QSize, radius: int = 12) -> QPixmap:
    """object-fit: cover into `size` with rounded corners."""
    out = QPixmap(size)
    out.fill(Qt.GlobalColor.transparent)
    scaled = src.scaled(size, Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                        Qt.TransformationMode.SmoothTransformation)
    x = (scaled.width() - size.width()) // 2
    y = (scaled.height() - size.height()) // 2
    p = QPainter(out)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    path = QPainterPath()
    path.addRoundedRect(0, 0, size.width(), size.height(), radius, radius)
    p.setClipPath(path)
    p.drawPixmap(0, 0, scaled, x, y, size.width(), size.height())
    p.end()
    return out


class WelcomeDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Welcome to PICker")
        self.setModal(True)
        self.setFixedWidth(_BANNER.width() + 56)
        self.setStyleSheet(theme_mod.dialog_qss())
        self._page = 0

        outer = QVBoxLayout(self)
        outer.setContentsMargins(28, 24, 28, 20)
        outer.setSpacing(16)

        self._banner = QLabel()
        self._banner.setFixedSize(_BANNER)
        self._banner.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._banner.setStyleSheet(
            f"background: {theme_mod.c('bg').name()};"
            " border-radius: 12px;")
        outer.addWidget(self._banner, 0, Qt.AlignmentFlag.AlignHCenter)

        self._heading = QLabel()
        self._heading.setStyleSheet("font-size: 20px; font-weight: 700;")
        self._heading.setWordWrap(True)
        outer.addWidget(self._heading)

        self._body = QLabel()
        self._body.setWordWrap(True)
        self._body.setStyleSheet(
            f"color: {theme_mod.c('muted').name()}; font-size: 13px; line-height: 150%;")
        self._body.setMinimumHeight(78)
        self._body.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Minimum)
        outer.addWidget(self._body)

        # Dots
        self._dots = QLabel()
        self._dots.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._dots.setStyleSheet("font-size: 16px; letter-spacing: 4px;")
        outer.addWidget(self._dots)

        # Buttons
        row = QHBoxLayout()
        self._skip = QPushButton("Skip")
        self._skip.clicked.connect(self._finish)
        row.addWidget(self._skip)
        row.addStretch(1)
        self._back = QPushButton("Back")
        self._back.clicked.connect(self._go_back)
        row.addWidget(self._back)
        self._next = QPushButton("Next")
        self._next.setObjectName("primary")
        self._next.clicked.connect(self._go_next)
        row.addWidget(self._next)
        outer.addLayout(row)

        self._render()

    def _render(self):
        fname, heading, body = _PAGES[self._page]
        self._heading.setText(heading)
        self._body.setText(body)
        # Banner image (graceful if missing)
        img_path = _coach_dir() / fname
        pm = QPixmap(str(img_path)) if img_path.exists() else QPixmap()
        if not pm.isNull():
            self._banner.setPixmap(_cover_pixmap(pm, _BANNER))
            self._banner.show()
        else:
            # No screenshot yet — show a subtle numbered placeholder.
            self._banner.setPixmap(QPixmap())
            self._banner.setText(f"{self._page + 1} / {len(_PAGES)}")
            self._banner.setStyleSheet(
                f"background: {theme_mod.c('bg').name()};"
                f" color: {theme_mod.c('muted').name()};"
                " border-radius: 12px; font-size: 32px; font-weight: 800;")
        self._dots.setText("  ".join(
            "●" if i == self._page else "○" for i in range(len(_PAGES))))
        self._back.setVisible(self._page > 0)
        last = self._page == len(_PAGES) - 1
        self._next.setText("Get started" if last else "Next")
        self._skip.setVisible(not last)

    def _go_next(self):
        if self._page < len(_PAGES) - 1:
            self._page += 1
            self._render()
        else:
            self._finish()

    def _go_back(self):
        if self._page > 0:
            self._page -= 1
            self._render()

    def _finish(self):
        settings_mod.set_value("seen_welcome", True)
        self.accept()

    # Esc / close also count as "seen" so it doesn't nag next launch.
    def reject(self):
        settings_mod.set_value("seen_welcome", True)
        super().reject()
