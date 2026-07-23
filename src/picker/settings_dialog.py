from pathlib import Path

from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QGridLayout, QLabel, QLineEdit, QPushButton,
    QGroupBox, QFileDialog, QFrame, QComboBox, QSpinBox, QDoubleSpinBox, QCheckBox,
    QScrollArea, QWidget, QMessageBox, QSizePolicy, QListWidget, QStackedWidget
)
from PyQt6.QtCore import Qt

from . import settings as settings_mod
from . import external
from . import recent as recent_mod
from . import theme as theme_mod
from .sysfeatures import windows_features_enabled


POSITION_LABELS = [
    ("Top Left", "tl"),
    ("Top Right", "tr"),
    ("Bottom Left", "bl"),
    ("Bottom Right", "br"),
]

FILE_TYPE_LABELS = [
    ("JPEG", "jpeg"),
    ("PNG", "png"),
    ("TIFF", "tiff"),
    ("WEBP", "webp"),
    ("BMP", "bmp"),
    ("SVG", "svg"),
    ("RAW (CR2/NEF/ARW/DNG/…)", "raw"),
]


# Only the rules unique to this dialog (paged scroll area + sidebar nav); the
# base (cards, inputs, toggles, buttons, #primary/#danger/#hint/#detected,
# scrollbars, #divider) comes from theme.dialog_qss().
_EXTRA_QSS = """
QScrollArea { background: #141416; border: 0; }
QScrollArea > QWidget > QWidget { background: #141416; }

QListWidget#nav {
    background: #1a1a1e;
    border: 1px solid #26262c;
    border-radius: 12px;
    padding: 6px;
    outline: 0;
    font-size: 13px;
}
QListWidget#nav::item {
    color: #b6b6bf;
    padding: 10px 12px;
    border-radius: 8px;
    margin: 2px 0;
}
QListWidget#nav::item:hover { background: #24242a; color: #eaeaef; }
QListWidget#nav::item:selected {
    background: #3b82f6;
    color: #ffffff;
    font-weight: 600;
}
"""


class SettingsDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("PICker — Settings")
        from .icon import app_icon
        self.setWindowIcon(app_icon())
        self.setMinimumSize(760, 680)
        self.resize(820, 720)
        self.setStyleSheet(theme_mod.dialog_qss() + _EXTRA_QSS)
        self._build_ui()
        self._set_accessible_names()
        self._load()

    # ── UI construction ───────────────────────────────────────────────────────

    def _build_ui(self):
        outer = QVBoxLayout(self)
        outer.setContentsMargins(28, 24, 28, 20)
        outer.setSpacing(14)

        header = QVBoxLayout()
        header.setSpacing(2)
        title = QLabel("Settings")
        title.setStyleSheet("color:#fff; font-size:22px; font-weight:700; letter-spacing:-0.3px;")
        header.addWidget(title)
        subtitle = QLabel("Configure defaults, performance, and appearance.")
        subtitle.setStyleSheet("color:#8a8a8a; font-size:12px;")
        header.addWidget(subtitle)
        outer.addLayout(header)

        divider = QFrame()
        divider.setObjectName("divider")
        divider.setFrameShape(QFrame.Shape.HLine)
        divider.setFixedHeight(1)
        outer.addWidget(divider)

        # Category sidebar + stacked pages — keeps each screen short and focused
        # instead of one long scroll through every group at once.
        body = QHBoxLayout()
        body.setSpacing(16)

        self._nav = QListWidget()
        self._nav.setObjectName("nav")
        self._nav.setFixedWidth(170)
        self._nav.setFrameShape(QFrame.Shape.NoFrame)
        self._nav.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        body.addWidget(self._nav)

        self._stack = QStackedWidget()
        body.addWidget(self._stack, 1)

        # File associations + shell context menu are Windows-only; on Linux/macOS
        # the Integrations page shows just the external-editor settings.
        self._win_features = windows_features_enabled()
        integrations = [self._build_editors()]
        if self._win_features:
            integrations.append(self._build_system())

        pages = [
            ("General",      [self._build_defaults(), self._build_appearance()]),
            ("Gallery",      [self._build_gallery()]),
            ("Slideshow",    [self._build_slideshow(), self._build_overlays()]),
            ("Editing",      [self._build_editing()]),
            ("Scanning",     [self._build_scanning()]),
            ("Integrations", integrations),
            ("Cache",        [self._build_maintenance()]),
            ("Advanced",     [self._build_advanced()]),
        ]
        for label, groups in pages:
            self._stack.addWidget(self._make_page(groups))
            self._nav.addItem(label)
        self._nav.currentRowChanged.connect(self._switch_page)
        self._nav.setCurrentRow(0)

        outer.addLayout(body, 1)

        bottom_div = QFrame()
        bottom_div.setObjectName("divider")
        bottom_div.setFrameShape(QFrame.Shape.HLine)
        bottom_div.setFixedHeight(1)
        outer.addWidget(bottom_div)

        btn_row = QHBoxLayout()
        btn_row.setSpacing(10)
        btn_row.setContentsMargins(0, 4, 0, 0)
        btn_row.addStretch()
        cancel = QPushButton("Cancel")
        cancel.setFixedWidth(100)
        cancel.clicked.connect(self.reject)
        btn_row.addWidget(cancel)
        save = QPushButton("Save")
        save.setObjectName("primary")
        save.setDefault(True)
        save.setFixedWidth(120)
        save.clicked.connect(self._save)
        btn_row.addWidget(save)
        outer.addLayout(btn_row)

    def _switch_page(self, idx: int):
        """Change page with a quick fade so navigation feels fluid."""
        self._stack.setCurrentIndex(idx)
        from PyQt6.QtWidgets import QGraphicsOpacityEffect
        from PyQt6.QtCore import QPropertyAnimation, QEasingCurve
        page = self._stack.currentWidget()
        if page is None:
            return
        eff = QGraphicsOpacityEffect(page)
        page.setGraphicsEffect(eff)
        anim = QPropertyAnimation(eff, b"opacity", page)
        anim.setDuration(150)
        anim.setStartValue(0.25)
        anim.setEndValue(1.0)
        anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        anim.finished.connect(lambda: page.setGraphicsEffect(None))
        anim.start()
        self._page_anim = anim  # keep ref

    def _make_page(self, groups) -> QScrollArea:
        """Wrap a list of group boxes in a scrollable page for the stack."""
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        inner = QWidget()
        inner.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        lay = QVBoxLayout(inner)
        lay.setContentsMargins(2, 2, 8, 2)
        lay.setSpacing(12)
        for grp in groups:
            lay.addWidget(grp)
        lay.addStretch(1)
        scroll.setWidget(inner)
        return scroll

    def _set_accessible_names(self):
        """Set accessible names on interactive widgets for screen readers."""
        name_map = {
            "mode_combo": "File mode",
            "res_combo": "Resolution percentage",
            "full_res_cb": "Display full resolution",
            "thumb_size_combo": "Thumbnail row height",
            "group_by_combo": "Group images by",
            "group_gran_combo": "Date group granularity",
            "preload_spin": "Preload count",
            "auto_advance_cb": "Auto advance on send",
            "filmstrip_cb": "Show filmstrip",
            "animation_cb": "Slideshow animation",
            "confirm_delete_cb": "Ask before deleting",
            "send_feedback_cb": "Send feedback flash",
            "conflict_combo": "Conflict handling",
            "explorer_esc_combo": "Escape action for Explorer-opened photo",
            "edit_save_combo": "Edit save mode",
            "edit_suffix_edit": "Edit new file suffix",
            "edit_quality_spin": "JPEG quality",
            "subfolders_cb": "Include subfolders",
            "hidden_cb": "Exclude hidden files",
            "videos_cb": "Include videos",
            "cache_mb_spin": "Thumbnail cache cap",
            "theme_combo": "Theme",
            "exif_pos_combo": "EXIF overlay position",
            "hist_pos_combo": "Histogram overlay position",
            "hist_style_combo": "Histogram style",
            "raw_combo": "RAW preference",
            "zoom_spin": "Zoom factor",
            "peaking_spin": "Focus peaking threshold",
            "ps_edit": "Photoshop path",
            "lr_edit": "Lightroom path",
            "file_assoc_cb": "Register file associations",
            "dir_context_cb": "Folder context menu",
            "scan_recursive_cb": "Scan recursive",
            "auto_scan_cb": "Auto scan on launch",
            "sort_combo": "Sort order",
            "log_cb": "Enable logging",
            "check_updates_cb": "Check for updates",
        }
        for attr, name in name_map.items():
            w = getattr(self, attr, None)
            if w is not None:
                w.setAccessibleName(name)

    # Defaults — top priority per user
    def _build_defaults(self) -> QGroupBox:
        grp = QGroupBox("Defaults")
        g = QGridLayout(grp)
        g.setSpacing(12)
        g.setColumnStretch(2, 1)

        g.addWidget(QLabel("File mode"), 0, 0)
        self.mode_combo = QComboBox()
        self.mode_combo.addItem("Copy (keep original)", "copy")
        self.mode_combo.addItem("Move (remove from source)", "move")
        self.mode_combo.setFixedWidth(240)
        g.addWidget(self.mode_combo, 0, 1, Qt.AlignmentFlag.AlignLeft)

        # Checkbox first — when ticked the dropdown below disappears entirely
        # (no point showing a percentage when we're decoding at native size).
        self.full_res_cb = QCheckBox(
            "Disable resolution limit (decode at native size — uses much more RAM on RAW)"
        )
        self.full_res_cb.toggled.connect(self._sync_res_combo_visible)
        g.addWidget(self.full_res_cb, 1, 0, 1, 2)

        self.res_label = QLabel("Display resolution")
        g.addWidget(self.res_label, 2, 0)
        self.res_combo = QComboBox()
        for pct in (10, 25, 50, 100):
            self.res_combo.addItem(f"{pct}%", pct)
        self.res_combo.setFixedWidth(120)
        g.addWidget(self.res_combo, 2, 1, Qt.AlignmentFlag.AlignLeft)

        hint = QLabel("Applied when opening a new source folder. A fraction of the original "
                      "pixels are decoded for the viewer to save memory. Smart-scaling never "
                      "shrinks an image below your screen resolution, so small photos stay sharp.")
        hint.setObjectName("hint")
        hint.setWordWrap(True)
        g.addWidget(hint, 3, 0, 1, 2)
        return grp

    def _sync_res_combo_visible(self, full_res_on: bool):
        # Hide instead of just disable so the panel collapses cleanly when the
        # override is active — no greyed-out dropdown taking visual space.
        self.res_combo.setVisible(not full_res_on)
        self.res_label.setVisible(not full_res_on)

    # Gallery
    def _build_gallery(self) -> QGroupBox:
        grp = QGroupBox("Gallery")
        g = QGridLayout(grp)
        g.setSpacing(12)
        g.setColumnStretch(2, 1)

        g.addWidget(QLabel("Thumbnail size"), 0, 0)
        self.thumb_size_combo = QComboBox()
        for px, label in [(120, "Small (120)"), (170, "Medium (170)"),
                          (210, "Large (210)"), (260, "Extra Large (260)")]:
            self.thumb_size_combo.addItem(label, px)
        self.thumb_size_combo.setFixedWidth(200)
        g.addWidget(self.thumb_size_combo, 0, 1, Qt.AlignmentFlag.AlignLeft)

        g.addWidget(QLabel("Group images by"), 1, 0)
        self.group_by_combo = QComboBox()
        self.group_by_combo.addItem("None (flat grid)", "flat")
        self.group_by_combo.addItem("Date taken", "date")
        self.group_by_combo.addItem("Folder", "folder")
        self.group_by_combo.addItem("Camera", "camera")
        self.group_by_combo.setFixedWidth(200)
        self.group_by_combo.currentIndexChanged.connect(self._sync_group_gran_enabled)
        g.addWidget(self.group_by_combo, 1, 1, Qt.AlignmentFlag.AlignLeft)

        self.group_gran_label = QLabel("Date granularity")
        g.addWidget(self.group_gran_label, 2, 0)
        self.group_gran_combo = QComboBox()
        self.group_gran_combo.addItem("Day", "day")
        self.group_gran_combo.addItem("Month", "month")
        self.group_gran_combo.addItem("Year", "year")
        self.group_gran_combo.setFixedWidth(200)
        g.addWidget(self.group_gran_combo, 2, 1, Qt.AlignmentFlag.AlignLeft)
        return grp

    def _sync_group_gran_enabled(self):
        on = self.group_by_combo.currentData() == "date"
        self.group_gran_label.setEnabled(on)
        self.group_gran_combo.setEnabled(on)

    # Slideshow
    def _build_slideshow(self) -> QGroupBox:
        grp = QGroupBox("Slideshow")
        g = QGridLayout(grp)
        g.setSpacing(12)
        g.setColumnStretch(2, 1)

        g.addWidget(QLabel("Preload neighbors"), 0, 0)
        self.preload_spin = QSpinBox()
        self.preload_spin.setRange(1, 5)
        self.preload_spin.setFixedWidth(80)
        g.addWidget(self.preload_spin, 0, 1, Qt.AlignmentFlag.AlignLeft)
        hint = QLabel("Higher = snappier nav, more RAM.")
        hint.setObjectName("hint")
        g.addWidget(hint, 0, 2)

        self.auto_advance_cb = QCheckBox("Auto-advance to next unreviewed after send")
        g.addWidget(self.auto_advance_cb, 1, 0, 1, 3)

        self.filmstrip_cb = QCheckBox("Show filmstrip bar")
        g.addWidget(self.filmstrip_cb, 2, 0, 1, 3)

        self.animation_cb = QCheckBox("Animate image transitions in fullscreen")
        g.addWidget(self.animation_cb, 3, 0, 1, 3)

        self.confirm_delete_cb = QCheckBox("Ask before deleting (otherwise sends straight to the Recycle Bin)")
        g.addWidget(self.confirm_delete_cb, 4, 0, 1, 3)

        self.send_feedback_cb = QCheckBox("Flash a subtle accent glow when you send / move / copy")
        g.addWidget(self.send_feedback_cb, 8, 0, 1, 3)

        g.addWidget(QLabel("On filename conflict"), 5, 0)
        self.conflict_combo = QComboBox()
        self.conflict_combo.addItem("Ask every time", "ask")
        self.conflict_combo.addItem("Keep both (rename)", "rename")
        self.conflict_combo.addItem("Replace", "replace")
        self.conflict_combo.addItem("Skip", "skip")
        self.conflict_combo.setFixedWidth(200)
        g.addWidget(self.conflict_combo, 5, 1, Qt.AlignmentFlag.AlignLeft)

        g.addWidget(QLabel("Esc on a photo opened from Explorer"), 6, 0)
        self.explorer_esc_combo = QComboBox()
        self.explorer_esc_combo.addItem("Open the folder (mosaic)", "mosaic")
        self.explorer_esc_combo.addItem("Close PICker", "close")
        self.explorer_esc_combo.setFixedWidth(200)
        g.addWidget(self.explorer_esc_combo, 6, 1, Qt.AlignmentFlag.AlignLeft)
        hint = QLabel("When you double-click a photo in Explorer and press Esc: browse that "
                      "folder as a mosaic, or quit.")
        hint.setObjectName("hint")
        hint.setWordWrap(True)
        g.addWidget(hint, 7, 0, 1, 3)
        return grp

    # Editing
    def _build_editing(self) -> QGroupBox:
        grp = QGroupBox("Editing (Crop / Rotate Save)")
        g = QGridLayout(grp)
        g.setSpacing(12)
        g.setColumnStretch(2, 1)

        g.addWidget(QLabel("When saving edits"), 0, 0)
        self.edit_save_combo = QComboBox()
        self.edit_save_combo.addItem("Ask every time", "ask")
        self.edit_save_combo.addItem("Always save as new file", "new")
        self.edit_save_combo.addItem("Always overwrite original", "overwrite")
        self.edit_save_combo.setFixedWidth(260)
        g.addWidget(self.edit_save_combo, 0, 1, Qt.AlignmentFlag.AlignLeft)

        hint = QLabel(
            "Applies after crop or save-rotation. Choose \"Ask\" to see a prompt with a "
            "\"Don't ask again\" checkbox — ticking it updates this setting automatically. "
            "RAW files always save as a sibling JPEG (original never overwritten)."
        )
        hint.setObjectName("hint")
        hint.setWordWrap(True)
        g.addWidget(hint, 1, 0, 1, 3)

        g.addWidget(QLabel("New-file suffix"), 2, 0)
        self.edit_suffix_edit = QLineEdit()
        self.edit_suffix_edit.setFixedWidth(140)
        self.edit_suffix_edit.setPlaceholderText("_edit")
        g.addWidget(self.edit_suffix_edit, 2, 1, Qt.AlignmentFlag.AlignLeft)

        g.addWidget(QLabel("JPEG/WEBP quality"), 3, 0)
        self.edit_quality_spin = QSpinBox()
        self.edit_quality_spin.setRange(50, 100)
        self.edit_quality_spin.setFixedWidth(100)
        g.addWidget(self.edit_quality_spin, 3, 1, Qt.AlignmentFlag.AlignLeft)
        return grp

    # Scanning
    def _build_scanning(self) -> QGroupBox:
        grp = QGroupBox("Source Scanning")
        g = QGridLayout(grp)
        g.setSpacing(12)
        g.setColumnStretch(2, 1)

        self.subfolders_cb = QCheckBox("Include subfolders (recursive scan)")
        g.addWidget(self.subfolders_cb, 0, 0, 1, 3)

        self.scan_recursive_cb = QCheckBox("Scan library roots recursively")
        g.addWidget(self.scan_recursive_cb, 1, 0, 1, 3)

        self.auto_scan_cb = QCheckBox("Auto-scan library on launch (rescan changed folders)")
        g.addWidget(self.auto_scan_cb, 2, 0, 1, 3)

        self.hidden_cb = QCheckBox("Exclude hidden / system folders (dot-prefixed, __pycache__)")
        g.addWidget(self.hidden_cb, 3, 0, 1, 3)

        self.videos_cb = QCheckBox("Include videos (.mp4 / .mov / .mkv / …) — needs ffmpeg for thumbs")
        g.addWidget(self.videos_cb, 4, 0, 1, 3)

        g.addWidget(QLabel("Sort order"), 5, 0)
        self.sort_combo = QComboBox()
        self.sort_combo.addItem("Date taken", "date_taken")
        self.sort_combo.addItem("Filename", "filename")
        self.sort_combo.addItem("Date modified", "mtime")
        self.sort_combo.addItem("File size", "size")
        self.sort_combo.setFixedWidth(180)
        g.addWidget(self.sort_combo, 5, 1, Qt.AlignmentFlag.AlignLeft)

        g.addWidget(QLabel("File types"), 6, 0, Qt.AlignmentFlag.AlignTop)
        types_box = QWidget()
        types_lay = QGridLayout(types_box)
        types_lay.setContentsMargins(0, 0, 0, 0)
        types_lay.setSpacing(6)
        self.file_type_checks: dict[str, QCheckBox] = {}
        for i, (label, code) in enumerate(FILE_TYPE_LABELS):
            cb = QCheckBox(label)
            self.file_type_checks[code] = cb
            types_lay.addWidget(cb, i // 2, i % 2)
        g.addWidget(types_box, 6, 1, 1, 2)
        return grp

    # Maintenance
    def _build_maintenance(self) -> QGroupBox:
        grp = QGroupBox("Cache & Recents")
        g = QGridLayout(grp)
        g.setSpacing(12)
        g.setColumnStretch(2, 1)

        g.addWidget(QLabel("Thumbnail cache cap"), 0, 0)
        self.cache_mb_spin = QSpinBox()
        self.cache_mb_spin.setRange(64, 65536)
        self.cache_mb_spin.setSingleStep(128)
        self.cache_mb_spin.setSuffix(" MB")
        self.cache_mb_spin.setFixedWidth(140)
        g.addWidget(self.cache_mb_spin, 0, 1, Qt.AlignmentFlag.AlignLeft)

        self.cache_size_label = QLabel()
        self.cache_size_label.setObjectName("hint")
        g.addWidget(self.cache_size_label, 0, 2)

        clear_cache_btn = QPushButton("Clear thumbnail cache")
        clear_cache_btn.setObjectName("danger")
        clear_cache_btn.clicked.connect(self._clear_thumb_cache)
        g.addWidget(clear_cache_btn, 1, 0, 1, 2)

        clear_recent_btn = QPushButton("Clear recent folders list")
        clear_recent_btn.setObjectName("danger")
        clear_recent_btn.clicked.connect(self._clear_recents)
        g.addWidget(clear_recent_btn, 2, 0, 1, 2)

        clear_all_btn = QPushButton("Clear all cache && database")
        clear_all_btn.setObjectName("danger")
        clear_all_btn.clicked.connect(self._clear_all_cache_db)
        g.addWidget(clear_all_btn, 3, 0, 1, 2)

        reset_btn = QPushButton("Reset settings to defaults")
        reset_btn.setObjectName("danger")
        reset_btn.clicked.connect(self._reset_defaults)
        g.addWidget(reset_btn, 4, 0, 1, 2)

        hint = QLabel("Cache lives under each source folder in `.picker_cache/`. Oldest thumbs evict when over cap. "
                      "Clearing the database wipes the indexed image metadata (rebuilt on next scan).")
        hint.setObjectName("hint")
        hint.setWordWrap(True)
        g.addWidget(hint, 5, 0, 1, 3)
        return grp

    # Appearance
    def _build_appearance(self) -> QGroupBox:
        grp = QGroupBox("Appearance")
        g = QGridLayout(grp)
        g.setSpacing(12)
        g.setColumnStretch(2, 1)
        g.addWidget(QLabel("Theme"), 0, 0)
        self.theme_combo = QComboBox()
        self.theme_combo.addItem("Dark", "dark")
        self.theme_combo.addItem("Light", "light")
        self.theme_combo.setFixedWidth(140)
        g.addWidget(self.theme_combo, 0, 1, Qt.AlignmentFlag.AlignLeft)
        return grp

    # Overlays
    def _build_overlays(self) -> QGroupBox:
        grp = QGroupBox("Overlays (Slideshow)")
        g = QGridLayout(grp)
        g.setSpacing(12)
        g.setColumnStretch(2, 1)
        g.addWidget(QLabel("EXIF position"), 0, 0)
        self.exif_pos_combo = self._pos_combo()
        g.addWidget(self.exif_pos_combo, 0, 1, Qt.AlignmentFlag.AlignLeft)
        g.addWidget(QLabel("Histogram position"), 1, 0)
        self.hist_pos_combo = self._pos_combo()
        g.addWidget(self.hist_pos_combo, 1, 1, Qt.AlignmentFlag.AlignLeft)
        g.addWidget(QLabel("Histogram style"), 2, 0)
        self.hist_style_combo = QComboBox()
        self.hist_style_combo.addItem("RGB additive", "additive")
        self.hist_style_combo.addItem("Luminance only", "luminance")
        self.hist_style_combo.setFixedWidth(180)
        g.addWidget(self.hist_style_combo, 2, 1, Qt.AlignmentFlag.AlignLeft)
        return grp

    # Editors
    def _build_editors(self) -> QGroupBox:
        grp = QGroupBox("External Editors")
        g = QVBoxLayout(grp)
        g.setSpacing(10)
        hint = QLabel("Leave blank to auto-detect (registry + Program Files).")
        hint.setObjectName("hint")
        g.addWidget(hint)
        self.ps_edit, self.ps_detected = self._make_path_row(g, "Photoshop", "Path to Photoshop.exe")
        self.lr_edit, self.lr_detected = self._make_path_row(g, "Lightroom", "Path to Lightroom.exe")
        return grp

    # System Integration
    def _build_system(self) -> QGroupBox:
        grp = QGroupBox("System Integration")
        g = QGridLayout(grp)
        g.setSpacing(12)
        g.setColumnStretch(2, 1)

        self.file_assoc_cb = QCheckBox("Register PICker as image file handler (Open With / double-click)")
        g.addWidget(self.file_assoc_cb, 0, 0, 1, 3)

        self.dir_context_cb = QCheckBox("Add \"Browse with PICker\" to folder right-click menu")
        self.dir_context_cb.setEnabled(False)
        g.addWidget(self.dir_context_cb, 1, 0, 1, 3)

        hint = QLabel("Per-user registration (no admin needed). Changes take effect after Save.")
        hint.setObjectName("hint")
        hint.setWordWrap(True)
        g.addWidget(hint, 2, 0, 1, 3)

        self._assoc_status = QLabel()
        self._assoc_status.setObjectName("detected")
        g.addWidget(self._assoc_status, 3, 0, 1, 3)

        self.file_assoc_cb.toggled.connect(lambda on: self.dir_context_cb.setEnabled(on))
        self.file_assoc_cb.toggled.connect(lambda on: self.dir_context_cb.setChecked(on))
        return grp

    # Advanced
    def _build_advanced(self) -> QGroupBox:
        grp = QGroupBox("Advanced")
        g = QGridLayout(grp)
        g.setSpacing(12)
        g.setColumnStretch(2, 1)

        g.addWidget(QLabel("RAW decode preference"), 0, 0)
        self.raw_combo = QComboBox()
        self.raw_combo.addItem("Embedded JPEG (fast)", "embedded")
        self.raw_combo.addItem("Full demosaic (accurate, slow)", "full")
        self.raw_combo.setFixedWidth(260)
        g.addWidget(self.raw_combo, 0, 1, Qt.AlignmentFlag.AlignLeft)

        g.addWidget(QLabel("Zoom wheel factor"), 1, 0)
        self.zoom_spin = QDoubleSpinBox()
        self.zoom_spin.setRange(1.05, 1.5)
        self.zoom_spin.setSingleStep(0.01)
        self.zoom_spin.setDecimals(2)
        self.zoom_spin.setFixedWidth(100)
        g.addWidget(self.zoom_spin, 1, 1, Qt.AlignmentFlag.AlignLeft)

        g.addWidget(QLabel("Focus peaking sensitivity"), 2, 0)
        self.peaking_spin = QSpinBox()
        self.peaking_spin.setRange(5, 80)
        self.peaking_spin.setFixedWidth(100)
        g.addWidget(self.peaking_spin, 2, 1, Qt.AlignmentFlag.AlignLeft)
        hint = QLabel("Lower = more edges highlighted.")
        hint.setObjectName("hint")
        g.addWidget(hint, 2, 2)

        sep = QFrame()
        sep.setObjectName("divider")
        sep.setFrameShape(QFrame.Shape.HLine)
        sep.setFixedHeight(1)
        g.addWidget(sep, 3, 0, 1, 3)

        self.log_cb = QCheckBox("Enable verbose logging to stderr")
        g.addWidget(self.log_cb, 4, 0, 1, 3)

        self.check_updates_cb = QCheckBox("Check for updates on launch")
        g.addWidget(self.check_updates_cb, 5, 0, 1, 3)

        return grp

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _pos_combo(self) -> QComboBox:
        combo = QComboBox()
        for text, code in POSITION_LABELS:
            combo.addItem(text, userData=code)
        combo.setFixedWidth(160)
        return combo

    def _make_path_row(self, parent_layout, label_text, placeholder):
        container = QVBoxLayout()
        container.setSpacing(4)
        label = QLabel(label_text)
        label.setStyleSheet("color:#ddd; font-weight:600;")
        container.addWidget(label)
        row = QHBoxLayout()
        row.setSpacing(8)
        edit = QLineEdit()
        edit.setPlaceholderText(placeholder)
        row.addWidget(edit)
        browse = QPushButton("Browse")
        browse.setFixedWidth(80)
        browse.clicked.connect(lambda: self._browse_exe(edit, label_text))
        row.addWidget(browse)
        clear = QPushButton("Clear")
        clear.setFixedWidth(70)
        clear.clicked.connect(lambda: edit.setText(""))
        row.addWidget(clear)
        container.addLayout(row)
        detected = QLabel()
        detected.setObjectName("detected")
        container.addWidget(detected)
        parent_layout.addLayout(container)
        return edit, detected

    def _browse_exe(self, edit: QLineEdit, name: str):
        filt = "Executable (*.exe)"
        path, _ = QFileDialog.getOpenFileName(self, f"Select {name} executable", "", filt)
        if path:
            edit.setText(path)

    # ── Clear actions ─────────────────────────────────────────────────────────

    def _clear_recents(self):
        recent_mod.clear()
        QMessageBox.information(self, "Cleared", "Recent folders list cleared.")

    def _clear_thumb_cache(self):
        # Best-effort: scan %APPDATA% cache + any parent of recent sources.
        import shutil
        deleted = 0
        for src in recent_mod.load():
            p = Path(src) / ".picker_cache"
            if p.is_dir():
                try:
                    shutil.rmtree(p, ignore_errors=True)
                    deleted += 1
                except Exception:
                    pass
        QMessageBox.information(
            self, "Cache cleared",
            f"Cleared `.picker_cache/` from {deleted} recent source folder(s)."
        )
        self._refresh_cache_size()

    def _clear_all_cache_db(self):
        confirm = QMessageBox.question(
            self, "Clear all cache & database",
            "This wipes all thumbnail caches, the image metadata database, and "
            "the move/position history.\n\nYour photos are NOT touched. Continue?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No)
        if confirm != QMessageBox.StandardButton.Yes:
            return
        import shutil
        from pathlib import Path as _Path
        # 1) Per-source thumbnail caches
        for src in recent_mod.load():
            p = _Path(src) / ".picker_cache"
            if p.is_dir():
                shutil.rmtree(p, ignore_errors=True)
        # 2) Index database
        try:
            from . import index as index_mod
            index_mod.clear_all()
        except Exception:
            pass
        # 3) In-memory scan caches (folder browser)
        try:
            from . import album_browser_view as abv
            abv.invalidate_scan_cache()
        except Exception:
            pass
        # 4) Sidecar state files under the config dir
        try:
            cfg = settings_mod._config_dir()
            for name in ("move-journal.json", "positions.json"):
                f = cfg / name
                if f.exists():
                    f.unlink()
        except Exception:
            pass
        QMessageBox.information(
            self, "Cleared",
            "All caches and the database were cleared. They rebuild automatically "
            "as you browse.")
        self._refresh_cache_size()

    def _reset_defaults(self):
        confirm = QMessageBox.question(
            self, "Reset settings",
            "Reset every setting on this screen back to its default value?\n\n"
            "(External-editor paths and file associations are left as-is.)",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No)
        if confirm != QMessageBox.StandardButton.Yes:
            return
        defaults = dict(settings_mod.DEFAULTS)
        # Preserve things the user shouldn't lose on a settings reset.
        for keep in ("photoshop_path", "lightroom_path",
                     "file_associations_registered", "recent_target_folders",
                     "last_folder"):
            defaults[keep] = settings_mod.get(keep)
        settings_mod.save(defaults)
        self._load()   # repaint every control from the fresh defaults
        QMessageBox.information(
            self, "Settings reset",
            "Settings restored to defaults. Some changes apply after you click Save.")

    def _refresh_cache_size(self):
        total = 0
        count = 0
        for src in recent_mod.load():
            p = Path(src) / ".picker_cache"
            if not p.is_dir():
                continue
            try:
                for f in p.iterdir():
                    if f.is_file():
                        total += f.stat().st_size
                        count += 1
            except OSError:
                continue
        mb = total / (1024 * 1024)
        self.cache_size_label.setText(f"Current: {mb:.1f} MB ({count:,} files)")

    # ── Load / Save ───────────────────────────────────────────────────────────

    def _load(self):
        data = settings_mod.load()

        self._select_data(self.mode_combo, data.get("default_mode", "copy"))
        self._select_data(self.res_combo, int(data.get("default_resolution_pct", 50)))
        self.full_res_cb.setChecked(bool(data.get("display_full_resolution", False)))
        # Reflect the checkbox state in dropdown visibility on open.
        self._sync_res_combo_visible(self.full_res_cb.isChecked())
        self._select_data(self.thumb_size_combo, int(data.get("thumbnail_row_height", 170)))
        self._select_data(self.group_by_combo, data.get("group_by", "flat"))
        self._select_data(self.group_gran_combo, data.get("date_group_granularity", "day"))
        self._sync_group_gran_enabled()

        self.preload_spin.setValue(int(data.get("preload_count", 2)))
        self.auto_advance_cb.setChecked(bool(data.get("auto_advance_on_send", True)))
        self.filmstrip_cb.setChecked(bool(data.get("show_filmstrip", True)))
        self.animation_cb.setChecked(bool(data.get("slideshow_animation", True)))
        self.confirm_delete_cb.setChecked(bool(data.get("confirm_delete", True)))
        self.send_feedback_cb.setChecked(bool(data.get("send_feedback", True)))
        self._select_data(self.conflict_combo, data.get("conflict_default", "ask"))
        self._select_data(self.explorer_esc_combo, data.get("explorer_escape_action", "mosaic"))

        self._select_data(self.edit_save_combo, data.get("edit_save_mode", "ask"))
        self.edit_suffix_edit.setText(data.get("edit_new_suffix", "_edit"))
        self.edit_quality_spin.setValue(int(data.get("edit_jpeg_quality", 95)))

        self.subfolders_cb.setChecked(bool(data.get("include_subfolders", True)))
        self.scan_recursive_cb.setChecked(bool(data.get("scan_recursive", True)))
        self.auto_scan_cb.setChecked(bool(data.get("auto_scan_on_launch", False)))
        self.hidden_cb.setChecked(bool(data.get("exclude_hidden", True)))
        self.videos_cb.setChecked(bool(data.get("include_videos", True)))
        self._select_data(self.sort_combo, data.get("sort_order", "date_taken"))
        enabled = set(data.get("file_types") or [])
        for code, cb in self.file_type_checks.items():
            cb.setChecked(code in enabled if enabled else True)

        self.cache_mb_spin.setValue(int(data.get("thumb_cache_mb", 1024)))
        self._refresh_cache_size()

        self._select_data(self.theme_combo, data.get("theme", "dark"))

        self._select_data(self.exif_pos_combo, data.get("exif_position", "tr"))
        self._select_data(self.hist_pos_combo, data.get("histogram_position", "br"))
        self._select_data(self.hist_style_combo, data.get("histogram_style", "additive"))

        self.ps_edit.setText(data.get("photoshop_path", ""))
        self.lr_edit.setText(data.get("lightroom_path", ""))
        self._refresh_detected()

        self._select_data(self.raw_combo, data.get("raw_preference", "embedded"))
        self.zoom_spin.setValue(float(data.get("zoom_factor", 1.18)))
        self.peaking_spin.setValue(int(data.get("peaking_threshold", 28)))

        self.log_cb.setChecked(bool(data.get("log_enabled", False)))
        self.check_updates_cb.setChecked(bool(data.get("check_updates", True)))

        if getattr(self, "_win_features", False):
            from .file_assoc import is_registered
            registered = is_registered()
            self.file_assoc_cb.setChecked(registered)
            self.dir_context_cb.setEnabled(registered)
            self.dir_context_cb.setChecked(registered)
            self._assoc_status.setText(
                "Status: Registered" if registered else "Status: Not registered"
            )

    def _refresh_detected(self):
        external.invalidate_cache()
        # Try detectors without manual override: stash user values, clear, detect, restore.
        saved_ps = settings_mod.get("photoshop_path")
        saved_lr = settings_mod.get("lightroom_path")
        try:
            settings_mod.set_value("photoshop_path", "")
            settings_mod.set_value("lightroom_path", "")
            external.invalidate_cache()
            ps = external.photoshop_path()
            lr = external.lightroom_path()
        finally:
            settings_mod.set_value("photoshop_path", saved_ps)
            settings_mod.set_value("lightroom_path", saved_lr)
            external.invalidate_cache()
        self.ps_detected.setText(f"Auto-detected: {ps}" if ps else "Auto-detected: (none)")
        self.lr_detected.setText(f"Auto-detected: {lr}" if lr else "Auto-detected: (none)")

    def _select_data(self, combo: QComboBox, value):
        for i in range(combo.count()):
            if combo.itemData(i) == value:
                combo.setCurrentIndex(i)
                return
        combo.setCurrentIndex(0)

    def _save(self):
        enabled_types = [code for code, cb in self.file_type_checks.items() if cb.isChecked()]
        if not enabled_types:
            enabled_types = [c for c, _ in [(code, label) for label, code in FILE_TYPE_LABELS]]

        settings_mod.save({
            "default_mode": self.mode_combo.currentData() or "copy",
            "default_resolution_pct": int(self.res_combo.currentData() or 50),
            "display_full_resolution": bool(self.full_res_cb.isChecked()),

            "thumbnail_row_height": int(self.thumb_size_combo.currentData() or 170),
            "group_by": self.group_by_combo.currentData() or "flat",
            "date_group_granularity": self.group_gran_combo.currentData() or "day",

            "preload_count": int(self.preload_spin.value()),
            "auto_advance_on_send": bool(self.auto_advance_cb.isChecked()),
            "show_filmstrip": bool(self.filmstrip_cb.isChecked()),
            "slideshow_animation": bool(self.animation_cb.isChecked()),
            "confirm_delete": bool(self.confirm_delete_cb.isChecked()),
            "send_feedback": bool(self.send_feedback_cb.isChecked()),
            "conflict_default": self.conflict_combo.currentData() or "ask",
            "explorer_escape_action": self.explorer_esc_combo.currentData() or "mosaic",

            "edit_save_mode": self.edit_save_combo.currentData() or "ask",
            "edit_new_suffix": (self.edit_suffix_edit.text().strip() or "_edit"),
            "edit_jpeg_quality": int(self.edit_quality_spin.value()),

            "include_subfolders": bool(self.subfolders_cb.isChecked()),
            "scan_recursive": bool(self.scan_recursive_cb.isChecked()),
            "auto_scan_on_launch": bool(self.auto_scan_cb.isChecked()),
            "exclude_hidden": bool(self.hidden_cb.isChecked()),
            "include_videos": bool(self.videos_cb.isChecked()),
            "sort_order": self.sort_combo.currentData() or "date_taken",
            "file_types": enabled_types,

            "thumb_cache_mb": int(self.cache_mb_spin.value()),

            "theme": self.theme_combo.currentData() or "dark",

            "exif_position": self.exif_pos_combo.currentData() or "tr",
            "histogram_position": self.hist_pos_combo.currentData() or "br",
            "histogram_style": self.hist_style_combo.currentData() or "additive",

            "photoshop_path": self.ps_edit.text().strip(),
            "lightroom_path": self.lr_edit.text().strip(),

            "raw_preference": self.raw_combo.currentData() or "embedded",
            "zoom_factor": float(self.zoom_spin.value()),
            "peaking_threshold": int(self.peaking_spin.value()),

            "log_enabled": bool(self.log_cb.isChecked()),
            "check_updates": bool(self.check_updates_cb.isChecked()),
        })
        external.invalidate_cache()

        if getattr(self, "_win_features", False):
            from .file_assoc import is_registered, register, unregister
            want_assoc = self.file_assoc_cb.isChecked()
            currently = is_registered()
            if want_assoc and not currently:
                err = register()
                if err:
                    QMessageBox.warning(self, "File Association", f"Failed to register: {err}")
            elif not want_assoc and currently:
                err = unregister()
                if err:
                    QMessageBox.warning(self, "File Association", f"Failed to unregister: {err}")
            settings_mod.set_value("file_associations_registered", want_assoc)

        self.accept()
