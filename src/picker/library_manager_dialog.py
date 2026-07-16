"""Dialog: Manage Library Folders.

Each row shows: cover thumbnail · label · path, with Rename / Cover / Remove
buttons. "Add" at the footer opens a folder picker. Changes apply immediately.
"""
import os

from PyQt6.QtCore import Qt, QSize
from PyQt6.QtGui import QPixmap, QIcon
from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QFrame,
    QScrollArea, QWidget, QInputDialog, QFileDialog, QMessageBox,
)

from . import library as library_mod
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
        self.setStyleSheet("""
            QFrame#rootRow {
                background: #1e1e1e;
                border: 1px solid #2a2a2a;
                border-radius: 8px;
            }
            QFrame#rootRow:hover { border-color: #3a3a3a; }
            QLabel#label { font-size: 14px; font-weight: 600; color: #eaeaea; }
            QLabel#path  { color: #8a8a8a; font-size: 11px; }
            QLabel#count { color: #5a9bff; font-size: 11px; }
            QPushButton {
                background: #2a2a2a; color: #ddd; border: 1px solid #3a3a3a;
                border-radius: 4px; padding: 5px 12px; font-size: 11px;
            }
            QPushButton:hover { background: #333; border-color: #4a4a4a; }
            QPushButton#danger:hover { background: #5a1f1f; border-color: #7a2a2a; color: #fff; }
        """)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(12)

        # Cover thumbnail
        self._cover = QLabel()
        self._cover.setFixedSize(_COVER_SIZE, _COVER_SIZE)
        self._cover.setStyleSheet("background: #0f0f0f; border-radius: 6px;")
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
            self._cover.setText("📁")
            self._cover.setStyleSheet(
                "background:#0f0f0f; border-radius:6px; color:#555; font-size:32px;"
            )
            return
        self._cover.setPixmap(pix.scaled(
            _COVER_SIZE, _COVER_SIZE,
            Qt.AspectRatioMode.KeepAspectRatioByExpanding,
            Qt.TransformationMode.SmoothTransformation,
        ))
        self._cover.setStyleSheet("background:#0f0f0f; border-radius:6px;")

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
        self.setStyleSheet("""
            QDialog { background: #121212; color: #eaeaea; }
            QLabel#header { font-size: 18px; font-weight: 700; color: #fff; }
            QLabel#hint   { color: #8a8a8a; font-size: 11px; }
            QScrollArea { background: #121212; border: 0; }
            QPushButton#primary {
                background: #3b82f6; color: #fff; border: 0;
                border-radius: 4px; padding: 8px 20px; font-weight: 600;
            }
            QPushButton#primary:hover { background: #3b93eb; }
            QPushButton {
                background: #1e1e1e; color: #eaeaea; border: 1px solid #2a2a2a;
                border-radius: 4px; padding: 8px 18px;
            }
            QPushButton:hover { background: #262626; border-color: #3a3a3a; }
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
            empty = QLabel("No library folders yet. Click “+ Add Folder” to get started.")
            empty.setStyleSheet("color: #6a6a6a; padding: 32px; font-size: 13px;")
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
