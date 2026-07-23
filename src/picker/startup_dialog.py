import os
from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit, QPushButton,
    QGroupBox, QFileDialog, QMessageBox, QFrame
)
from PyQt6.QtCore import Qt, QEvent, pyqtSignal

from . import recent as recent_mod
from . import settings as settings_mod
from . import theme as theme_mod


DEST_ACCENTS = [
    "#4ade80", "#60a5fa", "#fbbf24", "#f472b6", "#a78bfa",
    "#f87171", "#34d399", "#fb923c", "#22d3ee",
]
MAX_DESTS = 9


class DestinationRow(QFrame):
    # (row, global_y) while the grip is dragged — the dialog does the reordering.
    drag_move = pyqtSignal(object, int)

    def __init__(self, name: str = "", path: str = "", on_remove=None, parent=None):
        super().__init__(parent)
        self.setObjectName("destRow")
        self._on_remove = on_remove
        self._dragging = False
        layout = QHBoxLayout(self)
        layout.setContentsMargins(10, 4, 8, 4)
        layout.setSpacing(8)

        self._grip = QLabel("⠿")
        self._grip.setObjectName("grip")
        self._grip.setFixedWidth(14)
        self._grip.setCursor(Qt.CursorShape.OpenHandCursor)
        self._grip.setToolTip("Drag to reorder — order sets the 1 / 2 / 3 keys")
        self._grip.installEventFilter(self)
        layout.addWidget(self._grip)

        self._dot = QLabel()
        self._dot.setFixedSize(6, 6)
        layout.addWidget(self._dot)

        self.name_edit = QLineEdit()
        self.name_edit.setPlaceholderText("Label")
        self.name_edit.setFixedWidth(110)
        self.name_edit.setText(name)
        layout.addWidget(self.name_edit)

        self.path_edit = QLineEdit()
        self.path_edit.setPlaceholderText("Folder path")
        self.path_edit.setText(path)
        layout.addWidget(self.path_edit)

        browse_btn = QPushButton("…")
        browse_btn.setObjectName("rowIcon")
        browse_btn.setFixedSize(28, 26)
        browse_btn.setToolTip("Browse")
        browse_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        browse_btn.clicked.connect(self._browse)
        layout.addWidget(browse_btn)

        self._remove_btn = QPushButton("×")
        self._remove_btn.setObjectName("rowIcon")
        self._remove_btn.setFixedSize(28, 26)
        self._remove_btn.setToolTip("Remove")
        self._remove_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._remove_btn.clicked.connect(self._remove)
        layout.addWidget(self._remove_btn)

    def eventFilter(self, obj, ev):
        if obj is self._grip:
            et = ev.type()
            if et == QEvent.Type.MouseButtonPress:
                self._dragging = True
                self._grip.setCursor(Qt.CursorShape.ClosedHandCursor)
                return True
            if et == QEvent.Type.MouseMove and self._dragging:
                self.drag_move.emit(self, ev.globalPosition().toPoint().y())
                return True
            if et == QEvent.Type.MouseButtonRelease:
                self._dragging = False
                self._grip.setCursor(Qt.CursorShape.OpenHandCursor)
                return True
        return super().eventFilter(obj, ev)

    def set_accent_index(self, idx: int):
        c = DEST_ACCENTS[idx % len(DEST_ACCENTS)]
        self._dot.setStyleSheet(f"background:{c}; border-radius:3px;")
        # Order drives the number-key shortcut, so show it.
        self._grip.setToolTip(f"Drag to reorder — currently key {idx + 1}")

    def _browse(self):
        folder = QFileDialog.getExistingDirectory(self, "Select folder")
        if folder:
            self.path_edit.setText(folder)
            if not self.name_edit.text():
                self.name_edit.setText(os.path.basename(folder) or "Folder")

    def _remove(self):
        if self._on_remove:
            self._on_remove(self)

    def get_dest(self) -> dict | None:
        path = self.path_edit.text().strip()
        name = self.name_edit.text().strip() or (os.path.basename(path) or "Folder")
        if path:
            return {"name": name, "path": path}
        return None


# Only the rules unique to this dialog; the base (QDialog, QGroupBox, inputs,
# buttons, #primary, radios) comes from theme.dialog_qss().
_EXTRA_QSS = """
QLabel#tagline { color: #6f6f78; font-style: italic; }
QPushButton#recent {
    background: #1c1c20; color: #bbb; padding: 5px 12px;
    border: 1px solid #2a2a30; border-radius: 13px; font-size: 11px; font-weight: 500;
}
QPushButton#recent:hover { background: #26262c; color: #fff; border-color: #3b82f6; }
QFrame#destRow {
    background: transparent; border: 1px solid transparent; border-radius: 9px;
}
QFrame#destRow:hover { background: #1c1c20; border: 1px solid #2a2a30; }
QLabel#grip { color: #55555f; font-size: 14px; }
QFrame#destRow:hover QLabel#grip { color: #8a8a93; }
QPushButton#rowIcon {
    background: transparent; color: #777; border: 1px solid transparent;
    border-radius: 7px; padding: 0; font-size: 15px;
}
QPushButton#rowIcon:hover { background: #2a2a30; color: #ddd; border-color: #3a3a42; }
QPushButton#addDest {
    background: transparent; color: #7a7a7a; border: 1px dashed #3a3a42;
    border-radius: 9px; padding: 8px 14px; font-size: 12px;
}
QPushButton#addDest:hover { color: #cfcfcf; border-color: #55555f; background: #1a1a1e; }
QPushButton#addDest:disabled { color: #444; border-color: #2a2a30; }
"""


class StartupDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Sort Photos")
        from .icon import app_icon
        self.setWindowIcon(app_icon())
        self.setMinimumWidth(640)
        self.setModal(True)
        self.setStyleSheet(theme_mod.dialog_qss() + _EXTRA_QSS)
        self._result_data: dict | None = None
        self._build_ui()

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(28, 22, 28, 22)
        root.setSpacing(14)

        # Source folder
        src_group = QGroupBox("Source Folder")
        src_outer = QVBoxLayout(src_group)
        src_outer.setSpacing(8)
        src_row = QHBoxLayout()
        src_row.setSpacing(8)
        self.source_edit = QLineEdit()
        self.source_edit.setPlaceholderText("Folder containing your photos")
        src_row.addWidget(self.source_edit)
        src_browse = QPushButton("Browse")
        src_browse.setFixedWidth(80)
        src_browse.setCursor(Qt.CursorShape.PointingHandCursor)
        src_browse.clicked.connect(self._browse_source)
        src_row.addWidget(src_browse)
        src_outer.addLayout(src_row)

        # Recent folders row
        recents = recent_mod.load()
        if recents:
            recent_row = QHBoxLayout()
            recent_row.setContentsMargins(0, 4, 0, 0)
            recent_row.setSpacing(6)
            rlabel = QLabel("Recent")
            rlabel.setStyleSheet("color:#6b7280; font-size:11px; text-transform:uppercase; letter-spacing:1px;")
            recent_row.addWidget(rlabel)
            for path in recents:
                btn = QPushButton(os.path.basename(path.rstrip("/\\")) or path)
                btn.setObjectName("recent")
                btn.setToolTip(path)
                btn.setCursor(Qt.CursorShape.PointingHandCursor)
                btn.clicked.connect(lambda _=False, p=path: self.source_edit.setText(p))
                recent_row.addWidget(btn)
            recent_row.addStretch()
            src_outer.addLayout(recent_row)

        root.addWidget(src_group)

        # Sort targets. Start with one row; user adds up to 9.
        dest_group = QGroupBox("Sort into")
        self._dest_group = dest_group
        dest_outer = QVBoxLayout(dest_group)
        dest_outer.setSpacing(4)
        self._dest_rows_layout = QVBoxLayout()
        self._dest_rows_layout.setSpacing(2)
        dest_outer.addLayout(self._dest_rows_layout)
        self.dest_rows: list[DestinationRow] = []

        self._add_dest_btn = QPushButton("+  Add folder")
        self._add_dest_btn.setObjectName("addDest")
        self._add_dest_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._add_dest_btn.clicked.connect(lambda: self._add_dest_row())
        add_row = QHBoxLayout()
        add_row.setContentsMargins(10, 4, 10, 0)
        add_row.addWidget(self._add_dest_btn)
        add_row.addStretch()
        hint = QLabel("Drag ⠿ to reorder — order sets the 1 / 2 / 3 keys")
        hint.setObjectName("hint")
        add_row.addWidget(hint)
        dest_outer.addLayout(add_row)

        self._add_dest_row(name="Best")
        root.addWidget(dest_group)

        # Buttons
        btn_layout = QHBoxLayout()
        btn_layout.setSpacing(10)
        btn_layout.addStretch()
        cancel_btn = QPushButton("Cancel")
        cancel_btn.setFixedWidth(100)
        cancel_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        cancel_btn.clicked.connect(self.reject)
        btn_layout.addWidget(cancel_btn)
        ok_btn = QPushButton("Start")
        ok_btn.setObjectName("primary")
        ok_btn.setDefault(True)
        ok_btn.setFixedWidth(120)
        ok_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        ok_btn.clicked.connect(self._on_ok)
        btn_layout.addWidget(ok_btn)
        root.addLayout(btn_layout)

    def _add_dest_row(self, name: str = "", path: str = ""):
        if len(self.dest_rows) >= MAX_DESTS:
            return
        row = DestinationRow(name=name, path=path, on_remove=self._remove_dest_row)
        row.drag_move.connect(self._on_row_drag)
        self._dest_rows_layout.addWidget(row)
        self.dest_rows.append(row)
        self._refresh_dest_accents()
        self._update_add_btn()

    def _on_row_drag(self, row, global_y: int):
        """Live-reorder rows as the grip is dragged past neighbours."""
        if row not in self.dest_rows or len(self.dest_rows) < 2:
            return
        cur = self.dest_rows.index(row)
        target = cur
        for i, r in enumerate(self.dest_rows):
            if r is row:
                continue
            centre = r.mapToGlobal(r.rect().center()).y()
            if i < cur and global_y < centre:
                target = min(target, i)
            elif i > cur and global_y > centre:
                target = max(target, i)
        if target == cur:
            return
        self.dest_rows.insert(target, self.dest_rows.pop(cur))
        for r in self.dest_rows:                 # re-stack in the new order
            self._dest_rows_layout.removeWidget(r)
        for r in self.dest_rows:
            self._dest_rows_layout.addWidget(r)
        self._refresh_dest_accents()

    def _remove_dest_row(self, row: DestinationRow):
        if row not in self.dest_rows:
            return
        self.dest_rows.remove(row)
        self._dest_rows_layout.removeWidget(row)
        row.setParent(None)
        row.deleteLater()
        if not self.dest_rows:
            self._add_dest_row()
        self._refresh_dest_accents()
        self._update_add_btn()

    def _refresh_dest_accents(self):
        for i, r in enumerate(self.dest_rows):
            r.set_accent_index(i)

    def _update_add_btn(self):
        n = len(self.dest_rows)
        self._add_dest_btn.setEnabled(n < MAX_DESTS)
        self._add_dest_btn.setText(
            "+  Add folder" if n < MAX_DESTS else f"Max {MAX_DESTS} folders"
        )

    def _browse_source(self):
        folder = QFileDialog.getExistingDirectory(self, "Select Source Folder")
        if folder:
            self.source_edit.setText(folder)

    def _on_ok(self):
        source = self.source_edit.text().strip()
        if not source or not os.path.isdir(source):
            QMessageBox.warning(self, "Invalid Source", "Please select a valid source folder.")
            return

        destinations = [row.get_dest() for row in self.dest_rows]
        destinations = [d for d in destinations if d is not None]

        if destinations:
            from .image_manager import ImageManager
            err = ImageManager.check_dest_writable(destinations)
            if err:
                QMessageBox.critical(
                    self, "Destination not writable",
                    f"Can't write to one of the destination folders:\n\n{err}"
                )
                return

        self._result_data = {
            "source_folder": source,
            "destinations": destinations,
            "mode": settings_mod.get("default_mode") or "copy",
            "resolution_pct": int(settings_mod.get("default_resolution_pct") or 25),
        }
        recent_mod.add(source)
        self.accept()

    def result_data(self) -> dict | None:
        return self._result_data

    def prefill(self, data: dict) -> None:
        """Prefill form from a config dict (e.g. after Change Source Folder)."""
        if "source_folder" in data:
            self.source_edit.setText(data["source_folder"])
        dests = data.get("destinations", [])
        if dests:
            # Clear existing rows, rebuild from data.
            for r in list(self.dest_rows):
                self._dest_rows_layout.removeWidget(r)
                r.setParent(None)
                r.deleteLater()
            self.dest_rows.clear()
            for d in dests[:MAX_DESTS]:
                self._add_dest_row(name=d.get("name", ""), path=d.get("path", ""))
            if not self.dest_rows:
                self._add_dest_row()
