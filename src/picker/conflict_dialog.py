"""Modal shown when send_to destination already has a file of that name.
Displays existing vs incoming side by side so the user can pick an action."""
import os
from pathlib import Path

from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QFrame, QSizePolicy
)
from PyQt6.QtCore import Qt, QSize
from PyQt6.QtGui import QPixmap, QImageReader


DIALOG_QSS = """
QDialog { background: #1a1a1a; }
QLabel { color: #e5e5e5; }
QLabel#title { color: #fff; font-size: 16px; font-weight: 700; }
QLabel#subtitle { color: #9ca3af; font-size: 11px; }
QLabel#caption { color: #bbb; font-size: 12px; font-weight: 600; }
QLabel#meta { color: #6b7280; font-size: 11px; }
QLabel#preview { background: #0d0d0d; border: 1px solid #2a2a2a; border-radius: 6px; }
QPushButton {
    background: #2a2a2a; color: #e5e5e5;
    border: 1px solid #3a3a3a; border-radius: 6px;
    padding: 8px 16px;
}
QPushButton:hover { background: #353535; border-color: #505050; }
QPushButton#primary {
    background: #2a82da; color: white; border: 1px solid #2a82da; font-weight: 600;
}
QPushButton#primary:hover { background: #3a92ea; }
QPushButton#danger { background: #c0392b; border: 1px solid #c0392b; color: white; }
QPushButton#danger:hover { background: #d44638; }
"""


class ConflictDialog(QDialog):
    """Returns one of: 'replace', 'rename', 'skip', 'cancel'."""

    def __init__(self, src_path: str, dest_path: str, parent=None):
        super().__init__(parent)
        self._choice = "cancel"
        self.setWindowTitle("File already exists")
        self.setStyleSheet(DIALOG_QSS)
        self.setModal(True)
        self.setMinimumWidth(780)
        self._build(src_path, dest_path)

    def _build(self, src: str, dest: str):
        root = QVBoxLayout(self)
        root.setContentsMargins(22, 18, 22, 18)
        root.setSpacing(14)

        title = QLabel(f"A file named “{os.path.basename(dest)}” already exists at the destination.")
        title.setObjectName("title")
        title.setWordWrap(True)
        root.addWidget(title)

        subtitle = QLabel("Compare below and choose how to proceed.")
        subtitle.setObjectName("subtitle")
        root.addWidget(subtitle)

        # Side-by-side previews
        row = QHBoxLayout()
        row.setSpacing(16)
        row.addWidget(self._panel("Existing at destination", dest), stretch=1)
        row.addWidget(self._panel("Incoming (your current image)", src), stretch=1)
        root.addLayout(row)

        # Buttons
        btns = QHBoxLayout()
        btns.setSpacing(10)
        btns.addStretch()
        skip = QPushButton("Skip")
        skip.clicked.connect(lambda: self._done("skip"))
        btns.addWidget(skip)
        rename = QPushButton("Keep Both (rename)")
        rename.clicked.connect(lambda: self._done("rename"))
        btns.addWidget(rename)
        replace = QPushButton("Replace")
        replace.setObjectName("danger")
        replace.clicked.connect(lambda: self._done("replace"))
        btns.addWidget(replace)
        cancel = QPushButton("Cancel")
        cancel.clicked.connect(lambda: self._done("cancel"))
        btns.addWidget(cancel)
        root.addLayout(btns)

    def _panel(self, caption: str, path: str) -> QFrame:
        frame = QFrame()
        lay = QVBoxLayout(frame)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(6)

        cap = QLabel(caption)
        cap.setObjectName("caption")
        lay.addWidget(cap)

        preview = QLabel()
        preview.setObjectName("preview")
        preview.setAlignment(Qt.AlignmentFlag.AlignCenter)
        preview.setMinimumSize(340, 260)
        preview.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        pm = self._load_thumb(path, QSize(340, 260))
        if pm is not None and not pm.isNull():
            preview.setPixmap(pm)
        else:
            preview.setText("(no preview)")
            preview.setStyleSheet(preview.styleSheet() + " color: #666;")
        lay.addWidget(preview)

        meta = QLabel(self._meta_for(path))
        meta.setObjectName("meta")
        meta.setWordWrap(True)
        lay.addWidget(meta)
        return frame

    def _load_thumb(self, path: str, box: QSize) -> QPixmap | None:
        try:
            reader = QImageReader(path)
            reader.setAutoTransform(True)
            sz = reader.size()
            if sz.isValid() and sz.width() > 0:
                # Scale to fit within box preserving aspect
                scale = min(box.width() / sz.width(), box.height() / sz.height(), 1.0)
                reader.setScaledSize(QSize(
                    max(1, int(sz.width() * scale)),
                    max(1, int(sz.height() * scale)),
                ))
            img = reader.read()
            if img.isNull():
                return None
            return QPixmap.fromImage(img)
        except Exception:
            return None

    def _meta_for(self, path: str) -> str:
        try:
            st = os.stat(path)
            size_mb = st.st_size / (1024 * 1024)
            import datetime
            mtime = datetime.datetime.fromtimestamp(st.st_mtime).strftime("%Y-%m-%d %H:%M")
            return f"{Path(path).name}  ·  {size_mb:.2f} MB  ·  modified {mtime}"
        except Exception:
            return Path(path).name

    def _done(self, choice: str):
        self._choice = choice
        self.accept()

    def choice(self) -> str:
        return self._choice


def ask(parent, src_path: str, dest_path: str) -> str:
    dlg = ConflictDialog(src_path, dest_path, parent=parent)
    dlg.exec()
    return dlg.choice()
