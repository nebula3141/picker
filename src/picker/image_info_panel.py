"""Right-side panel showing detailed image metadata (resolution, EXIF, etc).

Toggled in the slideshow with `I`. Pulls EXIF on demand and reads pixel
dimensions via QImageReader so RAW headers are decoded cheaply."""
import os
from datetime import datetime

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QFrame, QScrollArea, QSizePolicy
)
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QImageReader, QFont

from . import exif as exif_mod
from . import theme as theme_mod


def _fmt_size(bytes_: int) -> str:
    if bytes_ < 1024:
        return f"{bytes_} B"
    if bytes_ < 1024 ** 2:
        return f"{bytes_ / 1024:.1f} KB"
    if bytes_ < 1024 ** 3:
        return f"{bytes_ / 1024 ** 2:.1f} MB"
    return f"{bytes_ / 1024 ** 3:.2f} GB"


def _fmt_megapixels(w: int, h: int) -> str:
    mp = (w * h) / 1_000_000
    if mp < 1:
        return f"{mp:.2f} MP"
    return f"{mp:.1f} MP"


def _fmt_aspect(w: int, h: int) -> str:
    if h == 0:
        return "—"
    from math import gcd
    g = gcd(w, h)
    a, b = w // g, h // g
    # Collapse near-standard ratios
    if (a, b) in {(3, 2), (2, 3), (4, 3), (3, 4), (16, 9), (9, 16), (1, 1), (5, 4), (4, 5)}:
        return f"{a}:{b}"
    if a > 100 or b > 100:
        return f"{w/h:.3f}"
    return f"{a}:{b}"


def _fmt_mtime(ts: float) -> str:
    try:
        return datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M:%S")
    except (OSError, ValueError, OverflowError):
        return "—"


class _Row(QWidget):
    """Label/value pair with consistent typography."""
    def __init__(self, label: str, value: str, value_color: str | None = None, parent=None):
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 4, 0, 4)
        layout.setSpacing(10)

        l = QLabel(label)
        muted = theme_mod.c("muted").name() if hasattr(theme_mod.c("muted"), "name") else "#888"
        l.setStyleSheet(f"color: {muted}; font-size: 11px; font-weight: 500;")
        l.setFixedWidth(100)
        l.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)

        v = QLabel(value or "—")
        v.setWordWrap(True)
        v.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        color = value_color or "#e6e6e6"
        v.setStyleSheet(f"color: {color}; font-size: 12px;")
        v.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)

        layout.addWidget(l)
        layout.addWidget(v, 1)


class _Section(QFrame):
    """Heading + grouped rows."""
    def __init__(self, title: str, parent=None):
        super().__init__(parent)
        self.setFrameShape(QFrame.Shape.NoFrame)
        self._layout = QVBoxLayout(self)
        self._layout.setContentsMargins(0, 0, 0, 14)
        self._layout.setSpacing(1)

        head = QLabel(title.upper())
        head.setStyleSheet(
            "color: #5a9bff; font-size: 11px; font-weight: 700;"
            "letter-spacing: 1.2px; padding: 6px 0 6px 0;"
        )
        self._layout.addWidget(head)

    def add_row(self, label: str, value: str, value_color: str | None = None):
        self._layout.addWidget(_Row(label, value, value_color))


class ImageInfoPanel(QWidget):
    PANEL_WIDTH = 320

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedWidth(self.PANEL_WIDTH)
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Expanding)
        self.setAutoFillBackground(True)
        self._apply_bg()

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        # Header
        header = QLabel("Image info")
        header.setStyleSheet(
            "background: rgba(255,255,255,0.04);"
            "color: #ffffff; font-size: 13px; font-weight: 700;"
            "padding: 10px 14px; border-bottom: 1px solid rgba(255,255,255,0.06);"
        )
        outer.addWidget(header)

        # Scroll area for body (long lens names, etc)
        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(True)
        self._scroll.setFrameShape(QFrame.Shape.NoFrame)
        self._scroll.setStyleSheet("background: transparent; border: 0;")
        self._scroll.viewport().setAutoFillBackground(False)
        outer.addWidget(self._scroll, 1)

        # Body container — refreshed on each set_image()
        self._body = QWidget()
        self._body_layout = QVBoxLayout(self._body)
        self._body_layout.setContentsMargins(14, 10, 14, 14)
        self._body_layout.setSpacing(10)
        self._body_layout.addStretch(1)
        self._scroll.setWidget(self._body)

    def _apply_bg(self):
        from PyQt6.QtGui import QPalette, QColor
        pal = self.palette()
        # Darker than canvas for visual separation
        pal.setColor(self.backgroundRole(), QColor(22, 22, 24))
        self.setPalette(pal)

    def _clear_body(self):
        while self._body_layout.count():
            item = self._body_layout.takeAt(0)
            w = item.widget()
            if w is not None:
                w.deleteLater()
        self._body_layout.addStretch(1)

    def set_image(self, rec, manager):
        """Populate the panel for the current media record (image or video)."""
        self._clear_body()
        path = rec.path

        from . import media as media_mod
        is_video = media_mod.is_video(path)

        # File section
        file_sec = _Section("Video file" if is_video else "File")
        file_sec.add_row("Name", rec.filename)
        try:
            st = os.stat(path)
            file_sec.add_row("Size", _fmt_size(st.st_size))
            file_sec.add_row("Modified", _fmt_mtime(st.st_mtime))
        except OSError:
            file_sec.add_row("Size", "—")
        ext = os.path.splitext(path)[1].lstrip(".").upper() or "—"
        file_sec.add_row("Format", ext)
        file_sec.add_row("Path", path, value_color="#9ab8e6")
        self._insert(file_sec)

        if is_video:
            self._insert_video_sections(path, media_mod)
        else:
            self._insert_image_sections(path, rec, manager)

        # Status section (if destinations are configured)
        if manager.has_destinations:
            st_sec = _Section("Sort")
            st_sec.add_row("Status", manager.dest_name_for_status(rec.status))
            st_sec.add_row("Mode", manager.mode)
            self._insert(st_sec)

    def _insert_image_sections(self, path: str, rec, manager):
        # Image section — read header without decoding pixels
        img_sec = _Section("Image")
        reader = QImageReader(path)
        reader.setAutoTransform(True)
        size = reader.size()
        if size.isValid() and size.width() > 0 and size.height() > 0:
            w, h = size.width(), size.height()
            img_sec.add_row("Resolution", f"{w} × {h} px")
            img_sec.add_row("Megapixels", _fmt_megapixels(w, h))
            img_sec.add_row("Aspect", _fmt_aspect(w, h))
            img_sec.add_row("Orientation", "Landscape" if w >= h else "Portrait")
        else:
            img_sec.add_row("Resolution", "—")
        rotation = manager.rotations.get(rec.filename, 0)
        if rotation:
            img_sec.add_row("Rotation", f"{rotation}° (unsaved)")
        self._insert(img_sec)

        # EXIF — pulled lazily through exif_mod's LRU cache
        ex = exif_mod.read_exif(path)
        if ex:
            cam_sec = _Section("Camera")
            cam_sec.add_row("Camera", ex.get("camera") or "—")
            cam_sec.add_row("Lens", ex.get("lens") or "—")
            self._insert(cam_sec)

            exp_sec = _Section("Exposure")
            exp_sec.add_row("Shutter", ex.get("shutter") or "—")
            exp_sec.add_row("Aperture", ex.get("aperture") or "—")
            exp_sec.add_row("ISO", ex.get("iso") or "—")
            exp_sec.add_row("Focal length", ex.get("focal") or "—")
            self._insert(exp_sec)

            time_sec = _Section("Captured")
            time_sec.add_row("Date/time", ex.get("datetime") or "—")
            self._insert(time_sec)
        else:
            no_exif = _Section("EXIF")
            no_exif.add_row("Status", "Not available")
            self._insert(no_exif)

    def _insert_video_sections(self, path: str, media_mod):
        # Video section — pulled via ffprobe (subprocess; cached by the OS).
        vid_sec = _Section("Video")
        if not media_mod.have_ffprobe():
            vid_sec.add_row("Status", "ffprobe not found", value_color="#e08a8a")
            vid_sec.add_row(
                "Hint",
                "Drop ffmpeg.exe/ffprobe.exe next to PICker.exe (or on PATH) to see codec, duration, fps.",
                value_color="#888",
            )
            self._insert(vid_sec)
            return
        probe = media_mod.probe_video(path)
        if not probe:
            vid_sec.add_row("Status", "Could not read metadata")
            self._insert(vid_sec)
            return
        w, h = probe.get("width"), probe.get("height")
        if w and h:
            vid_sec.add_row("Resolution", f"{w} × {h} px")
            vid_sec.add_row("Megapixels", _fmt_megapixels(w, h))
            vid_sec.add_row("Aspect", _fmt_aspect(w, h))
            vid_sec.add_row("Orientation", "Landscape" if w >= h else "Portrait")
        vid_sec.add_row("Duration", media_mod.fmt_duration(probe.get("duration_ms")))
        if probe.get("fps"):
            vid_sec.add_row("Frame rate", f"{probe['fps']:g} fps")
        if probe.get("codec"):
            vid_sec.add_row("Codec", probe["codec"])
        if probe.get("bitrate"):
            mbps = probe["bitrate"] / 1_000_000
            vid_sec.add_row("Bitrate", f"{mbps:.2f} Mbps")
        self._insert(vid_sec)

    def _insert(self, widget: QWidget):
        # Insert before the trailing stretch
        idx = max(0, self._body_layout.count() - 1)
        self._body_layout.insertWidget(idx, widget)
