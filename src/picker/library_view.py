"""Library home screen.

Grid of library-root cards (cover image, label, indexed-count). Click a card
to open that folder in the gallery. This replaces the "jump straight to
gallery of one folder" behavior as the default startup view.
"""
import os

from PyQt6.QtCore import Qt, QSize, pyqtSignal
from PyQt6.QtGui import QPixmap, QFont, QColor, QPainter
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QScrollArea,
    QGridLayout, QFrame, QSizePolicy,
)

from . import library as library_mod
from . import index as index_mod
from . import log


CARD_W = 260
CARD_H = 220
COVER_H = 150
MIN_COLS = 1
MAX_COLS = 8


class _RootCard(QFrame):
    clicked = pyqtSignal(str)   # emits root path

    def __init__(self, root: dict, parent=None):
        super().__init__(parent)
        self._root = root
        self.setFixedSize(CARD_W, CARD_H)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setToolTip(root.get("path", ""))
        self.setObjectName("rootCard")
        self.setStyleSheet("""
            QFrame#rootCard {
                background: #17171a;
                border: 1px solid #26262c;
                border-radius: 14px;
            }
            QFrame#rootCard:hover { border-color: #3b82f6; background: #1b1b20; }
            QLabel#cover { background: #0d0d0f; border-top-left-radius: 14px; border-top-right-radius: 14px; }
            QLabel#label { font-size: 14px; font-weight: 600; color: #eaeaef; }
            QLabel#meta  { color: #8a8a93; font-size: 11px; }
        """)

        root_layout = QVBoxLayout(self)
        root_layout.setContentsMargins(0, 0, 0, 0)
        root_layout.setSpacing(0)

        # Cover area
        self._cover = QLabel()
        self._cover.setObjectName("cover")
        self._cover.setFixedHeight(COVER_H)
        self._cover.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._cover.setScaledContents(False)
        root_layout.addWidget(self._cover)
        self._set_cover_pixmap()

        # Text block
        text = QWidget()
        tlay = QVBoxLayout(text)
        tlay.setContentsMargins(12, 10, 12, 12)
        tlay.setSpacing(3)

        label = root.get("label") or os.path.basename(root["path"].rstrip("/\\"))
        self._label_lbl = QLabel(label)
        self._label_lbl.setObjectName("label")
        tlay.addWidget(self._label_lbl)

        try:
            n = index_mod.folder_count_recursive(root["path"])
        except Exception:
            n = 0
        meta = f"{n:,} photo{'s' if n != 1 else ''}" if n else "tap to scan"
        meta_lbl = QLabel(meta)
        meta_lbl.setObjectName("meta")
        tlay.addWidget(meta_lbl)

        root_layout.addWidget(text)

    def _set_cover_pixmap(self):
        pix = None
        cover = self._root.get("cover")
        if cover and os.path.isfile(cover):
            pix = QPixmap(cover)
        if pix is None or pix.isNull():
            try:
                rows = index_mod.search(
                    root=self._root["path"], order_by="date_taken", limit=1,
                )
                if rows:
                    pix = QPixmap(rows[0]["path"])
            except Exception:
                pix = None
        if pix is None or pix.isNull():
            placeholder = QPixmap(CARD_W, COVER_H)
            placeholder.fill(QColor("#141414"))
            p = QPainter(placeholder)
            p.setPen(QColor("#3a3a3a"))
            f = QFont()
            f.setPointSize(28)
            p.setFont(f)
            p.drawText(placeholder.rect(), Qt.AlignmentFlag.AlignCenter, "📁")
            p.end()
            self._cover.setPixmap(placeholder)
            return
        self._cover.setPixmap(pix.scaled(
            CARD_W, COVER_H,
            Qt.AspectRatioMode.KeepAspectRatioByExpanding,
            Qt.TransformationMode.SmoothTransformation,
        ))

    def mousePressEvent(self, event):
        # super() FIRST: the slot connected to `clicked` swaps the central
        # widget, which deletes this card's C++ object. Touching `self` after
        # the emit (e.g. another super() call) would raise RuntimeError.
        super().mousePressEvent(event)
        if event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit(self._root["path"])


class _RecentTile(QPushButton):
    def __init__(self, path: str, parent=None):
        name = os.path.basename(path.rstrip("/\\")) or path
        super().__init__(name, parent)
        self._path = path
        self.setToolTip(path)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setStyleSheet("""
            QPushButton {
                background: #17171a; color: #cfcfd6;
                border: 1px solid #26262c; border-radius: 14px;
                padding: 6px 14px; font-size: 11px;
            }
            QPushButton:hover { border-color: #3b82f6; color: #fff; background: #1b1b20; }
        """)


class LibraryView(QWidget):
    """Home screen. Emits open_folder(path) on card click."""

    open_folder = pyqtSignal(str)
    manage_requested = pyqtSignal()
    add_requested = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setStyleSheet("""
            QWidget { background: #0b0b0d; color: #eaeaef; }
            QLabel#title    { font-size: 28px; font-weight: 700; color: #fff; letter-spacing: -0.5px; }
            QLabel#subtitle { color: #8a8a93; font-size: 12px; }
            QLabel#section  { font-size: 11px; font-weight: 700; color: #6f6f78;
                              text-transform: uppercase; letter-spacing: 1.2px; }
            QPushButton#primary {
                background: #3b82f6; color: #fff; border: 0;
                border-radius: 9px; padding: 9px 20px; font-weight: 600;
            }
            QPushButton#primary:hover { background: #5a9bff; }
            QPushButton#primary:pressed { background: #2f6fe0; }
            QPushButton#ghost {
                background: transparent; color: #c8c8d0; border: 1px solid #2a2a30;
                border-radius: 9px; padding: 9px 16px;
            }
            QPushButton#ghost:hover { border-color: #45454f; color: #fff; background: #16161a; }
            QScrollArea { background: #0b0b0d; border: 0; }
        """)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(28, 22, 28, 22)
        outer.setSpacing(18)

        # Header
        header = QHBoxLayout()
        tcol = QVBoxLayout()
        tcol.setSpacing(2)
        title = QLabel("Library")
        title.setObjectName("title")
        tcol.addWidget(title)
        subtitle = QLabel("Your photo folders at a glance.")
        subtitle.setObjectName("subtitle")
        tcol.addWidget(subtitle)
        header.addLayout(tcol)
        header.addStretch()

        manage_btn = QPushButton("Manage")
        manage_btn.setObjectName("ghost")
        manage_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        manage_btn.clicked.connect(self.manage_requested.emit)
        header.addWidget(manage_btn)

        add_btn = QPushButton("+  Add Folder")
        add_btn.setObjectName("primary")
        add_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        add_btn.clicked.connect(self.add_requested.emit)
        header.addWidget(add_btn)
        outer.addLayout(header)

        # Scroll area with cards + recents
        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(True)
        self._host = QWidget()
        self._host_layout = QVBoxLayout(self._host)
        self._host_layout.setContentsMargins(0, 0, 0, 0)
        self._host_layout.setSpacing(22)
        self._scroll.setWidget(self._host)
        outer.addWidget(self._scroll, 1)

        # Pinned recents bar at the bottom of the screen — kept outside the
        # scroll area so it stays put no matter how many folders are listed.
        self._recents_bar = QWidget()
        self._recents_bar.setObjectName("recentsBar")
        self._recents_bar.setStyleSheet(
            "#recentsBar { border-top: 1px solid #1f1f1f; }")
        self._recents_layout = QVBoxLayout(self._recents_bar)
        self._recents_layout.setContentsMargins(0, 12, 0, 0)
        self._recents_layout.setSpacing(10)
        outer.addWidget(self._recents_bar)

        self.reload()

    def reload(self):
        """Rebuild card grid and recents row from current library state."""
        log.info("library view reload")
        # Clear host
        while self._host_layout.count():
            item = self._host_layout.takeAt(0)
            w = item.widget()
            if w:
                w.deleteLater()

        roots = library_mod.roots()

        # ── Folders section
        folders_section = QVBoxLayout()
        folders_section.setSpacing(10)
        sec_label = QLabel("Folders")
        sec_label.setObjectName("section")
        folders_section.addWidget(sec_label)

        if not roots:
            empty = QLabel(
                "No library folders yet.\n\n"
                "Click “+ Add Folder” above to point PICker at a folder "
                "full of photos. You can add as many as you like."
            )
            empty.setStyleSheet("color:#6a6a6a; padding:40px; font-size:13px;")
            empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
            folders_section.addWidget(empty)
        else:
            self._grid = QGridLayout()
            self._grid.setSpacing(16)
            self._grid.setContentsMargins(0, 0, 0, 0)
            self._cards = []
            for r in roots:
                card = _RootCard(r)
                card.clicked.connect(self.open_folder.emit)
                self._cards.append(card)
            self._place_cards()
            grid_host = QWidget()
            grid_host.setLayout(self._grid)
            folders_section.addWidget(grid_host)

        self._host_layout.addLayout(folders_section)
        self._host_layout.addStretch()

        # ── Recents — rebuilt into the pinned bottom bar
        self._build_recents_bar()

    def _build_recents_bar(self):
        """(Re)populate the pinned bottom recents strip."""
        # Clear previous contents
        while self._recents_layout.count():
            item = self._recents_layout.takeAt(0)
            w = item.widget()
            if w:
                w.deleteLater()
            elif item.layout() is not None:
                self._clear_layout(item.layout())

        recents = library_mod.recents()
        self._recents_bar.setVisible(bool(recents))
        if not recents:
            return

        rec_label = QLabel("Recent")
        rec_label.setObjectName("section")
        self._recents_layout.addWidget(rec_label)

        chips = QHBoxLayout()
        chips.setSpacing(8)
        for path in recents[:10]:
            tile = _RecentTile(path)
            tile.clicked.connect(lambda _=False, p=path: self.open_folder.emit(p))
            chips.addWidget(tile)
        chips.addStretch()
        chip_host = QWidget()
        chip_host.setLayout(chips)
        self._recents_layout.addWidget(chip_host)

    def _clear_layout(self, layout):
        while layout.count():
            item = layout.takeAt(0)
            w = item.widget()
            if w:
                w.deleteLater()
            elif item.layout() is not None:
                self._clear_layout(item.layout())

    def _place_cards(self):
        if not hasattr(self, "_grid") or not self._cards:
            return
        # Clear layout without deleting card widgets
        while self._grid.count():
            self._grid.takeAt(0)
        width = max(0, self._scroll.viewport().width() - 4)
        cols = max(MIN_COLS, min(MAX_COLS, width // (CARD_W + 16) or 1))
        for i, card in enumerate(self._cards):
            self._grid.addWidget(card, i // cols, i % cols)
        # Absorb trailing columns
        self._grid.setColumnStretch(cols, 1)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._place_cards()
