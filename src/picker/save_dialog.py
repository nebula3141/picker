"""Save-mode prompt used after a destructive edit (crop, rotate-to-file)."""
from pathlib import Path

from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QRadioButton,
    QButtonGroup, QCheckBox, QFrame
)
from PyQt6.QtCore import Qt

from . import settings as settings_mod
from . import theme as theme_mod


class SaveModeDialog(QDialog):
    """Ask user whether to overwrite or save as new file. Returns "new"|"overwrite"|None."""

    def __init__(self, source_path: str, parent=None):
        super().__init__(parent)
        self._source = Path(source_path)
        self._choice: str | None = None
        self.setWindowTitle("Save changes")
        self.setStyleSheet(theme_mod.dialog_qss())
        self.setModal(True)
        self.setMinimumWidth(460)
        self._build()

    def _build(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(22, 20, 22, 18)
        root.setSpacing(12)

        title = QLabel("Save changes to file")
        title.setObjectName("title")
        root.addWidget(title)

        hint = QLabel(f"Source: <b>{self._source.name}</b>")
        hint.setTextFormat(Qt.TextFormat.RichText)
        hint.setObjectName("hint")
        root.addWidget(hint)

        div = QFrame(); div.setObjectName("divider"); div.setFixedHeight(1)
        root.addWidget(div)

        self._group = QButtonGroup(self)
        self._rb_new = QRadioButton(self._new_label())
        self._rb_ow = QRadioButton("Overwrite original")
        self._group.addButton(self._rb_new)
        self._group.addButton(self._rb_ow)
        self._rb_new.setChecked(True)
        root.addWidget(self._rb_new)
        root.addWidget(self._rb_ow)

        warn = QLabel("Overwrite replaces the original file. Cannot be undone.")
        warn.setObjectName("hint")
        warn.setWordWrap(True)
        root.addWidget(warn)

        self._dont_ask = QCheckBox("Don't ask again (use this choice in future; change in Settings)")
        root.addWidget(self._dont_ask)

        div2 = QFrame(); div2.setObjectName("divider"); div2.setFixedHeight(1)
        root.addWidget(div2)

        btns = QHBoxLayout()
        btns.addStretch()
        cancel = QPushButton("Cancel")
        cancel.clicked.connect(self.reject)
        btns.addWidget(cancel)
        ok = QPushButton("Save")
        ok.setObjectName("primary")
        ok.setDefault(True)
        ok.clicked.connect(self._accept)
        btns.addWidget(ok)
        root.addLayout(btns)

    def _new_label(self) -> str:
        suffix = settings_mod.get("edit_new_suffix") or "_edit"
        new_name = f"{self._source.stem}{suffix}{self._source.suffix}"
        return f"Save as new file  ({new_name})"

    def _accept(self):
        self._choice = "new" if self._rb_new.isChecked() else "overwrite"
        if self._dont_ask.isChecked():
            settings_mod.set_value("edit_save_mode", self._choice)
        self.accept()

    def choice(self) -> str | None:
        return self._choice


def resolve_save_mode(source_path: str, parent=None) -> str | None:
    """Return "new" or "overwrite" per setting; prompt if "ask". None = cancelled."""
    mode = settings_mod.get("edit_save_mode") or "ask"
    if mode in ("new", "overwrite"):
        return mode
    dlg = SaveModeDialog(source_path, parent)
    if dlg.exec() != QDialog.DialogCode.Accepted:
        return None
    return dlg.choice()
