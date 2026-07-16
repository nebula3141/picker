"""Video playback widget for the slideshow.

Built on QtMultimedia (QMediaPlayer + QVideoWidget). The slideshow swaps this
in place of the still-image canvas when the current media item is a video.
Chrome layout, key behaviour, and visual style mirror the image canvas so the
two feel like the same view.

Controls
--------
Bottom toolbar: play/pause, current/total time, scrub bar, speed dropdown,
mute toggle, volume slider. Always rendered — keyboard shortcuts also work
from the slideshow (Space, arrows, M, etc.).

Defensive design
----------------
- QtMultimedia is an optional Qt module. Both `available()` (module-level
  function) and `_have_qtmultimedia` (constant) report whether the import
  succeeded so the slideshow can fall back to a friendly placeholder.
- Resource cleanup is explicit (`cleanup()`); QMediaPlayer holds open file
  handles on Windows that block moves/renames if you forget to stop it.
"""
from __future__ import annotations

import os

from PyQt6.QtCore import Qt, QUrl, pyqtSignal, QTimer
from PyQt6.QtGui import QPainter, QColor, QFont, QMouseEvent
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QPushButton, QSlider, QLabel, QFrame,
    QSizePolicy, QComboBox, QStyle, QStyleOptionSlider,
)


class _JumpSlider(QSlider):
    """QSlider variant where a click on the track immediately moves the handle
    to the click position (instead of the default page-step behavior). Works
    for both press and drag; the parent wires `sliderMoved`/`sliderReleased`
    to perform the actual seek."""
    def mousePressEvent(self, event: QMouseEvent):
        if event.button() == Qt.MouseButton.LeftButton:
            opt = QStyleOptionSlider()
            self.initStyleOption(opt)
            handle = self.style().subControlRect(
                QStyle.ComplexControl.CC_Slider, opt,
                QStyle.SubControl.SC_SliderHandle, self,
            )
            if not handle.contains(event.position().toPoint()):
                if self.orientation() == Qt.Orientation.Horizontal:
                    new_val = QStyle.sliderValueFromPosition(
                        self.minimum(), self.maximum(),
                        int(event.position().x()), self.width(),
                    )
                else:
                    new_val = QStyle.sliderValueFromPosition(
                        self.minimum(), self.maximum(),
                        int(event.position().y()), self.height(),
                    )
                self.setValue(new_val)
                self.sliderMoved.emit(new_val)
        super().mousePressEvent(event)

try:
    from PyQt6.QtMultimedia import QMediaPlayer, QAudioOutput
    from PyQt6.QtMultimediaWidgets import QVideoWidget
    _HAVE_QTMM = True
except ImportError:
    _HAVE_QTMM = False
    QMediaPlayer = None  # type: ignore
    QAudioOutput = None  # type: ignore
    QVideoWidget = None  # type: ignore


def available() -> bool:
    return _HAVE_QTMM


def _fmt_time(ms: int) -> str:
    if ms is None or ms < 0:
        return "0:00"
    s = ms // 1000
    h, rem = divmod(s, 3600)
    m, sec = divmod(rem, 60)
    if h:
        return f"{h}:{m:02d}:{sec:02d}"
    return f"{m}:{sec:02d}"


_SPEED_STEPS = [0.25, 0.5, 0.75, 1.0, 1.25, 1.5, 2.0, 3.0, 4.0]
_FRAME_STEP_MS = 33  # ~30fps frame step


class VideoView(QWidget):
    """Self-contained video player. Emits `playback_finished` when EOF reaches
    so the slideshow can auto-advance under user control."""

    playback_finished = pyqtSignal()
    error_occurred = pyqtSignal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._path: str | None = None
        self._suppress_seek = False  # block user scrub events during programmatic moves
        self._duration_ms = 0
        self._loop = False

        self.setAutoFillBackground(True)
        pal = self.palette()
        pal.setColor(self.backgroundRole(), QColor(8, 8, 8))
        self.setPalette(pal)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        if not _HAVE_QTMM:
            self._build_unavailable_placeholder(outer)
            return

        # ── Video surface ─────────────────────────────────────────────────────
        self._video = QVideoWidget(self)
        self._video.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        outer.addWidget(self._video, stretch=1)

        # ── Player + audio ────────────────────────────────────────────────────
        self._player = QMediaPlayer(self)
        self._audio = QAudioOutput(self)
        self._audio.setVolume(0.7)
        self._player.setAudioOutput(self._audio)
        self._player.setVideoOutput(self._video)

        self._player.positionChanged.connect(self._on_position)
        self._player.durationChanged.connect(self._on_duration)
        self._player.playbackStateChanged.connect(self._on_state)
        self._player.errorOccurred.connect(self._on_error)
        self._player.mediaStatusChanged.connect(self._on_media_status)

        # ── Bottom chrome ─────────────────────────────────────────────────────
        chrome = self._build_chrome()
        outer.addWidget(chrome)

    # ── UI builders ──────────────────────────────────────────────────────────

    def _build_unavailable_placeholder(self, outer: QVBoxLayout):
        msg = QLabel(
            "Video playback requires the QtMultimedia module.\n\n"
            "Install with:  pip install PyQt6-Multimedia"
        )
        msg.setAlignment(Qt.AlignmentFlag.AlignCenter)
        msg.setStyleSheet("color: #c0c0c0; font-size: 14px; padding: 60px;")
        outer.addWidget(msg, stretch=1)

    def _build_chrome(self) -> QFrame:
        frame = QFrame()
        frame.setObjectName("videoChrome")
        frame.setStyleSheet("""
            QFrame#videoChrome {
                background: rgba(15, 15, 15, 230);
                border-top: 1px solid rgba(255, 255, 255, 0.06);
            }
            QPushButton {
                background: #2a2a2a; color: #f0f0f0;
                border: 1px solid #3a3a3a; border-radius: 6px;
                padding: 6px 12px; font-size: 13px; font-weight: 600;
                min-width: 38px;
            }
            QPushButton:hover { background: #353535; border-color: #4a4a4a; }
            QPushButton:checked { background: #2f6fe0; border-color: #3b82f6; }
            QPushButton:disabled { color: #666; background: #1f1f1f; }
            QLabel { color: #d0d0d0; font-size: 12px; }
            QComboBox {
                background: #2a2a2a; color: #f0f0f0;
                border: 1px solid #3a3a3a; border-radius: 6px;
                padding: 4px 10px; font-size: 12px;
            }
            QComboBox::drop-down { border: 0; width: 16px; }
            QSlider::groove:horizontal {
                height: 6px; background: #2a2a2a; border-radius: 3px;
            }
            QSlider::sub-page:horizontal { background: #3b82f6; border-radius: 3px; }
            QSlider::handle:horizontal {
                background: #ffffff; width: 12px; margin: -4px 0;
                border-radius: 6px;
            }
        """)
        lay = QHBoxLayout(frame)
        lay.setContentsMargins(12, 8, 12, 10)
        lay.setSpacing(10)

        self._btn_play = QPushButton("▶")
        self._btn_play.setCursor(Qt.CursorShape.PointingHandCursor)
        self._btn_play.setToolTip("Play / pause (Space)")
        self._btn_play.clicked.connect(self.toggle_play)
        lay.addWidget(self._btn_play)

        self._lbl_time = QLabel("0:00 / 0:00")
        self._lbl_time.setMinimumWidth(110)
        lay.addWidget(self._lbl_time)

        self._scrub = _JumpSlider(Qt.Orientation.Horizontal)
        self._scrub.setRange(0, 0)
        # `sliderMoved` fires on every drag tick AND on track-clicks (via
        # _JumpSlider). Live-seek so playback jumps immediately as the user
        # scrubs — much more useful than waiting for release.
        self._scrub.sliderPressed.connect(lambda: setattr(self, "_suppress_seek", True))
        self._scrub.sliderMoved.connect(self._on_scrub_moved)
        self._scrub.sliderReleased.connect(self._on_scrub_released)
        lay.addWidget(self._scrub, stretch=1)

        self._btn_skip_back = QPushButton("⏪")
        self._btn_skip_back.setCursor(Qt.CursorShape.PointingHandCursor)
        self._btn_skip_back.setToolTip("Back 10s (J)")
        self._btn_skip_back.clicked.connect(lambda: self.step_seconds(-10))
        lay.addWidget(self._btn_skip_back)

        self._btn_skip_fwd = QPushButton("⏩")
        self._btn_skip_fwd.setCursor(Qt.CursorShape.PointingHandCursor)
        self._btn_skip_fwd.setToolTip("Forward 10s (L)")
        self._btn_skip_fwd.clicked.connect(lambda: self.step_seconds(10))
        lay.addWidget(self._btn_skip_fwd)

        self._cmb_speed = QComboBox()
        for s in _SPEED_STEPS:
            label = f"{s:g}x"
            self._cmb_speed.addItem(label)
        self._cmb_speed.setCurrentText("1x")
        self._cmb_speed.currentTextChanged.connect(self._on_speed_changed)
        self._cmb_speed.setToolTip("Playback speed ([ / ])")
        lay.addWidget(self._cmb_speed)

        self._btn_loop = QPushButton("🔁")
        self._btn_loop.setCursor(Qt.CursorShape.PointingHandCursor)
        self._btn_loop.setToolTip("Loop (Ctrl+L)")
        self._btn_loop.setCheckable(True)
        self._btn_loop.clicked.connect(self._on_loop_toggled)
        lay.addWidget(self._btn_loop)

        self._btn_mute = QPushButton("🔊")
        self._btn_mute.setCursor(Qt.CursorShape.PointingHandCursor)
        self._btn_mute.setToolTip("Mute (M)")
        self._btn_mute.clicked.connect(self.toggle_mute)
        lay.addWidget(self._btn_mute)

        self._slider_vol = QSlider(Qt.Orientation.Horizontal)
        self._slider_vol.setRange(0, 100)
        self._slider_vol.setValue(70)
        self._slider_vol.setFixedWidth(90)
        self._slider_vol.valueChanged.connect(self._on_volume_changed)
        lay.addWidget(self._slider_vol)

        return frame

    # ── Public API ───────────────────────────────────────────────────────────

    def load(self, path: str, *, autoplay: bool = True):
        """Open a new file. Stops any current playback first."""
        if not _HAVE_QTMM:
            return
        self._path = path
        self._duration_ms = 0
        self._scrub.setRange(0, 0)
        self._lbl_time.setText("0:00 / 0:00")
        url = QUrl.fromLocalFile(os.path.abspath(path))
        self._player.setSource(url)
        if autoplay:
            self._player.play()

    def play(self):
        if _HAVE_QTMM:
            self._player.play()

    def pause(self):
        if _HAVE_QTMM:
            self._player.pause()

    def toggle_play(self):
        if not _HAVE_QTMM:
            return
        st = self._player.playbackState()
        if st == QMediaPlayer.PlaybackState.PlayingState:
            self._player.pause()
        else:
            self._player.play()

    def toggle_mute(self):
        if not _HAVE_QTMM:
            return
        muted = not self._audio.isMuted()
        self._audio.setMuted(muted)
        self._btn_mute.setText("🔇" if muted else "🔊")

    def step_seconds(self, delta: float):
        """Seek by ±N seconds."""
        if not _HAVE_QTMM:
            return
        new_pos = max(0, min(self._duration_ms,
                             self._player.position() + int(delta * 1000)))
        self._player.setPosition(new_pos)

    def frame_step(self, forward: bool = True):
        """Step one frame forward or backward (pauses playback first)."""
        if not _HAVE_QTMM:
            return
        if self._player.playbackState() == QMediaPlayer.PlaybackState.PlayingState:
            self._player.pause()
        delta = _FRAME_STEP_MS if forward else -_FRAME_STEP_MS
        new_pos = max(0, min(self._duration_ms,
                             self._player.position() + delta))
        self._player.setPosition(new_pos)

    def speed_up(self):
        """Increase playback speed to next step."""
        self._cycle_speed(1)

    def speed_down(self):
        """Decrease playback speed to previous step."""
        self._cycle_speed(-1)

    def _cycle_speed(self, direction: int):
        if not _HAVE_QTMM:
            return
        current = self._player.playbackRate()
        idx = 0
        for i, s in enumerate(_SPEED_STEPS):
            if abs(s - current) < abs(_SPEED_STEPS[idx] - current):
                idx = i
        idx = max(0, min(len(_SPEED_STEPS) - 1, idx + direction))
        self._cmb_speed.setCurrentText(f"{_SPEED_STEPS[idx]:g}x")

    def toggle_loop(self):
        if not _HAVE_QTMM:
            return
        self._loop = not self._loop
        self._btn_loop.setChecked(self._loop)

    def is_looping(self) -> bool:
        return self._loop

    def skip_to_start(self):
        if _HAVE_QTMM:
            self._player.setPosition(0)

    def skip_to_end(self):
        if _HAVE_QTMM and self._duration_ms > 0:
            self._player.setPosition(max(0, self._duration_ms - 100))

    def current_speed(self) -> float:
        if _HAVE_QTMM:
            return self._player.playbackRate()
        return 1.0

    def set_volume(self, pct: int):
        """Set volume 0-100."""
        if not _HAVE_QTMM:
            return
        self._slider_vol.setValue(max(0, min(100, pct)))

    def cleanup(self):
        if not _HAVE_QTMM:
            return
        try:
            self._player.stop()
            self._player.setSource(QUrl())
        except Exception:
            pass

    # ── Slots ────────────────────────────────────────────────────────────────

    def _on_position(self, ms: int):
        if not self._suppress_seek:
            self._scrub.blockSignals(True)
            self._scrub.setValue(ms)
            self._scrub.blockSignals(False)
        self._lbl_time.setText(f"{_fmt_time(ms)} / {_fmt_time(self._duration_ms)}")

    def _on_duration(self, ms: int):
        self._duration_ms = ms
        self._scrub.setRange(0, max(1, ms))
        self._lbl_time.setText(f"{_fmt_time(self._player.position())} / {_fmt_time(ms)}")

    def _on_state(self, state):
        playing = state == QMediaPlayer.PlaybackState.PlayingState
        self._btn_play.setText("⏸" if playing else "▶")

    def _on_error(self, err):
        if err == QMediaPlayer.Error.NoError:
            return
        msg = self._player.errorString() or str(err)
        self.error_occurred.emit(msg)

    def _on_media_status(self, status):
        if status == QMediaPlayer.MediaStatus.EndOfMedia:
            if self._loop:
                self._player.setPosition(0)
                self._player.play()
            else:
                self.playback_finished.emit()

    def _on_loop_toggled(self, checked: bool):
        self._loop = checked

    def _on_scrub_moved(self, value: int):
        # Live seek — jump the video to the dragged/clicked position so the
        # preview frame matches the slider as it moves. _suppress_seek blocks
        # the player's own positionChanged signal from fighting the user.
        self._suppress_seek = True
        self._lbl_time.setText(f"{_fmt_time(value)} / {_fmt_time(self._duration_ms)}")
        self._player.setPosition(value)

    def _on_scrub_released(self):
        # User let go; resume listening to player.positionChanged.
        try:
            self._player.setPosition(self._scrub.value())
        finally:
            self._suppress_seek = False

    def _on_speed_changed(self, text: str):
        try:
            rate = float(text.rstrip("x"))
        except ValueError:
            rate = 1.0
        self._player.setPlaybackRate(rate)

    def _on_volume_changed(self, value: int):
        self._audio.setVolume(value / 100.0)
        if value == 0 and not self._audio.isMuted():
            self._audio.setMuted(True)
            self._btn_mute.setText("🔇")
        elif value > 0 and self._audio.isMuted():
            self._audio.setMuted(False)
            self._btn_mute.setText("🔊")
