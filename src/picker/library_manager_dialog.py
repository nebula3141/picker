"""Dialog: Manage Library Folders.

Each row shows: cover thumbnail · label · path, with Rename / Cover / Remove
buttons. "Add" at the footer opens a folder picker. Changes apply immediately.
"""
import os

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QPixmap
from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QFrame,
    QScrollArea, QWidget, QInputDialog, QFileDialog, QMessageBox,
)

from . import library as library_mod
from . import theme as theme_mod
from . import index as index_mod
from . import log
from .icon import app_icon


_COVER_SIZE = 96


class _RootRow(QFrame):
    def __init__(self, root: dict, parent: "LibraryManagerDialog"):
        super().__init__(parent)
        self._parent = parent
        self._root = root
        self.setObjectName("rootRow")
        theme_mod.bind(self, lambda: f"""
            QFrame#rootRow {{
                background: {theme_mod.hexc('deck')};
                border: 1px solid {theme_mod.hexc('rail')};
                border-radius: {theme_mod.RADIUS}px;
            }}
            QFrame#rootRow:hover {{ border-color: {theme_mod.hexc('glass_edge')}; }}
            QLabel#label {{ font-size: 14px; font-weight: 600; color: {theme_mod.hexc('fg')}; }}
            QLabel#path  {{ color: {theme_mod.hexc('muted')}; font-size: 11px; }}
            QLabel#count {{ color: {theme_mod.hexc('accent_text')}; font-size: 11px; }}
        """)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(12)

        # Cover thumbnail
        self._cover = QLabel()
        self._cover.setFixedSize(_COVER_SIZE, _COVER_SIZE)
        theme_mod.bind(self._cover, lambda: f"background: {theme_mod.hexc('well')};"
                                            f" border-radius: {theme_mod.RADIUS}px;")
        self._cover.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._refresh_cover()
        layout.addWidget(self._cover)

        # Label / path / count
        text_col = QVBoxLayout()
        text_col.setSpacing(2)
        self._label_lbl = QLabel(root.get("label") or os.path.basename(root["path"].rstrip("/\\")))
        self._label_lbl.setObjectName("label")
        text_col.addWidget(self._label_lbl)

        path_lbl = QLabel(root["path"])
        path_lbl.setObjectName("path")
        path_lbl.setWordWrap(True)
        text_col.addWidget(path_lbl)

        try:
            n = index_mod.folder_count_recursive(root["path"])
        except Exception:
            n = 0
        count_lbl = QLabel(f"{n:,} indexed" if n else "not yet indexed")
        count_lbl.setObjectName("count")
        text_col.addWidget(count_lbl)
        layout.addLayout(text_col, 1)

        # Action buttons
        btn_col = QVBoxLayout()
        btn_col.setSpacing(5)
        rename_btn = QPushButton("Rename")
        rename_btn.clicked.connect(self._on_rename)
        cover_btn = QPushButton("Set Cover")
        cover_btn.clicked.connect(self._on_cover)
        remove_btn = QPushButton("Remove")
        remove_btn.setObjectName("danger")
        remove_btn.clicked.connect(self._on_remove)
        btn_col.addWidget(rename_btn)
        btn_col.addWidget(cover_btn)
        btn_col.addWidget(remove_btn)
        btn_col.addStretch()
        layout.addLayout(btn_col)

    def _refresh_cover(self):
        cover = self._root.get("cover")
        pix = None
        if cover and os.path.isfile(cover):
            pix = QPixmap(cover)
        if pix is None or pix.isNull():
            # Try first indexed file for the root
            try:
                rows = index_mod.search(root=self._root["path"], order_by="date_taken", limit=1)
                if rows:
                    pix = QPixmap(rows[0]["path"])
            except Exception:
                pix = None
        if pix is None or pix.isNull():
            # No photo to show: an empty glass well, with the folder mark drawn
            # (never an emoji) so it reads the same on every platform.
            from PyQt6.QtGui import QPainter
            from PyQt6.QtCore import QRectF
            from . import instrument as inst
            pm = QPixmap(_COVER_SIZE, _COVER_SIZE)
            pm.fill(Qt.GlobalColor.transparent)
            qp = QPainter(pm)
            qp.setRenderHint(QPainter.RenderHint.Antialiasing)
            inst.draw_icon(qp, "folder", QRectF(30, 30, 36, 36), inst._c("faint"), 1.6)
            qp.end()
            self._cover.setPixmap(pm)
            theme_mod.bind(self._cover, lambda: f"background: {theme_mod.hexc('well')};"
                                                f" border-radius: {theme_mod.RADIUS}px;")
            return
        self._cover.setPixmap(pix.scaled(
            _COVER_SIZE, _COVER_SIZE,
            Qt.AspectRatioMode.KeepAspectRatioByExpanding,
            Qt.TransformationMode.SmoothTransformation,
        ))
        theme_mod.bind(self._cover, lambda: f"background: {theme_mod.hexc('well')};"
                                            f" border-radius: {theme_mod.RADIUS}px;")

    def _on_rename(self):
        current = self._root.get("label") or ""
        new, ok = QInputDialog.getText(
            self, "Rename Library Folder",
            f"Display name for:\n{self._root['path']}",
            text=current,
        )
        if not ok:
            return
        label = new.strip() or None
        library_mod.update_root(self._root["path"], label=label)
        self._root["label"] = label
        self._label_lbl.setText(label or os.path.basename(self._root["path"].rstrip("/\\")))
        log.info("root renamed", path=self._root["path"], label=label)

    def _on_cover(self):
        start = self._root["path"]
        file, _ = QFileDialog.getOpenFileName(
            self, "Choose Cover Image", start,
            "Images (*.jpg *.jpeg *.png *.webp *.bmp *.tiff *.tif)",
        )
        if not file:
            return
        library_mod.set_cover(self._root["path"], file)
        self._root["cover"] = file
        self._refresh_cover()
        log.info("root cover set", path=self._root["path"], cover=file)

    def _on_remove(self):
        btn = QMessageBox.question(
            self, "Remove Library Folder",
            f"Remove this folder from the library?\n\n{self._root['path']}\n\n"
            f"Indexed metadata will be deleted. Files on disk are untouched.",
        )
        if btn != QMessageBox.StandardButton.Yes:
            return
        library_mod.remove_root(self._root["path"])
        try:
            n = index_mod.remove_root_entries(self._root["path"])
            log.info("root removed", path=self._root["path"], deleted_rows=n)
        except Exception as e:
            log.error("index cleanup failed", err=str(e))
        self._parent._reload()


class LibraryManagerDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Manage Library Folders")
        self.setWindowIcon(app_icon())
        self.setMinimumSize(640, 480)
        self.setModal(True)
        theme_mod.bind(self, lambda: theme_mod.dialog_qss() + f"""
            QLabel#header {{ font-size: 18px; font-weight: 600; color: {theme_mod.hexc('fg')}; }}
            QScrollArea {{ background: transparent; border: 0; }}
            QScrollArea > QWidget > QWidget {{ background: transparent; }}
        """)

        root = QVBoxLayout(self)
        root.setContentsMargins(22, 20, 22, 20)
        root.setSpacing(12)

        header = QLabel("Library Folders")
        header.setObjectName("header")
        root.addWidget(header)

        hint = QLabel("These folders are scanned and indexed. Set a cover image to represent each one on the library home screen.")
        hint.setObjectName("hint")
        hint.setWordWrap(True)
        root.addWidget(hint)

        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(True)
        self._list_host = QWidget()
        self._list_layout = QVBoxLayout(self._list_host)
        self._list_layout.setContentsMargins(0, 4, 0, 4)
        self._list_layout.setSpacing(8)
        self._scroll.setWidget(self._list_host)
        root.addWidget(self._scroll, 1)

        footer = QHBoxLayout()
        add_btn = QPushButton("+  Add Folder")
        add_btn.setObjectName("primary")
        add_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        add_btn.clicked.connect(self._add)
        footer.addWidget(add_btn)
        footer.addStretch()
        close_btn = QPushButton("Close")
        close_btn.clicked.connect(self.accept)
        footer.addWidget(close_btn)
        root.addLayout(footer)

        self._reload()

    def _reload(self):
        # Clear
        while self._list_layout.count():
            item = self._list_layout.takeAt(0)
            w = item.widget()
            if w:
                w.deleteLater()

        roots = library_mod.roots()
        if not roots:
            empty = QLabel("No library folders yet. Click “Add Folder” to get started.")
            theme_mod.bind(empty, lambda: f"color: {theme_mod.hexc('muted')}; padding: 32px; font-size: 13px;")
            empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
            self._list_layout.addWidget(empty)
            self._list_layout.addStretch()
            return

        for r in roots:
            self._list_layout.addWidget(_RootRow(r, self))
        self._list_layout.addStretch()

    def _add(self):
        start = os.path.expanduser("~")
        folder = QFileDialog.getExistingDirectory(self, "Add Library Folder", start)
        if not folder:
            return
        if library_mod.get_root(folder):
            QMessageBox.information(self, "Already in Library", "That folder is already in your library.")
            return
        library_mod.add_root(folder)
        log.info("root added", path=folder)
        self._reload()
