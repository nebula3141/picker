"""Timeline scrollbar: the scroll track labelled with when the photos were taken.

The rail replaces the grid's vertical scrollbar. Labels sit at the rows those
photos occupy, at a granularity that fits the folder:

* every photo on one day  -> clock times ("12:45 AM"), with the date on top
* within one year         -> days ("Sep 12")
* up to about two years   -> months ("Sep 2026")
* longer                  -> years ("2026")

Interaction is direct: drag anywhere and the grid follows the pointer with no
delay; click and it glides there; hover or scroll and a bubble shows the exact
date and time at that point. The mouse wheel scrolls smoothly (eased, and
additive, so fast spins accumulate instead of stuttering). With Animation off
every movement is immediate.

Costs are kept off the hot path: labels are laid out once per layout change,
painting is a few lines of text, and scrolling only repaints the rail.
"""
from __future__ import annotations

import bisect
from datetime import datetime

from PyQt6.QtCore import QEvent, QObject, QPointF, QRectF, Qt, QTimer, QVariantAnimation
from PyQt6.QtGui import QColor, QFontMetrics, QPainter, QPen
from PyQt6.QtWidgets import QScrollArea, QWidget

from . import motion
from . import theme as theme_mod


def _c(key: str) -> QColor:
    v = theme_mod.c(key)
    return v if isinstance(v, QColor) else QColor(v)


def _clock(dt: datetime) -> str:
    return dt.strftime("%I:%M %p").lstrip("0")


def label_for(ts: float, mode: str) -> str:
    dt = datetime.fromtimestamp(ts)
    if mode == "time":
        return _clock(dt)
    if mode == "day":
        return f"{dt:%b} {dt.day}"
    if mode == "month":
        return f"{dt:%b %Y}"
    return f"{dt.year}"


def bubble_for(ts: float, mode: str) -> str:
    dt = datetime.fromtimestamp(ts)
    day = f"{dt:%b} {dt.day}, {dt.year}"
    return f"{day}  ·  {_clock(dt)}" if mode in ("time", "day") else day


def pick_mode(stamps: list[float]) -> str:
    if not stamps:
        return "day"
    lo, hi = min(stamps), max(stamps)
    a, b = datetime.fromtimestamp(lo), datetime.fromtimestamp(hi)
    if a.date() == b.date():
        return "time"
    if a.year == b.year:
        return "day"
    if (hi - lo) <= 730 * 86400:
        return "month"
    return "year"


# ── Smooth wheel scrolling ────────────────────────────────────────────────────

class SmoothScroll(QObject):
    """Eased, additive wheel scrolling for a QScrollArea.

    A notch sets a target a fixed distance away and glides there; notches that
    arrive mid-glide extend the target, so a fast spin is one continuous
    motion. Touchpads (which already send pixel deltas) and Ctrl+wheel (zoom)
    pass straight through. Off entirely when Animation is off."""

    STEP = 120        # px per wheel notch
    MS = 200

    def __init__(self, area: QScrollArea):
        super().__init__(area)
        self._sb = area.verticalScrollBar()
        self._target = None
        self._anim = QVariantAnimation(self)
        self._anim.setEasingCurve(motion.ease())
        self._anim.valueChanged.connect(lambda v: self._sb.setValue(int(round(v))))
        self._anim.finished.connect(self._done)
        area.viewport().installEventFilter(self)

    def _done(self):
        self._target = None

    def eventFilter(self, obj, ev):
        if ev.type() != QEvent.Type.Wheel:
            return False
        if ev.modifiers() & Qt.KeyboardModifier.ControlModifier:
            return False
        if not motion.enabled() or not ev.pixelDelta().isNull():
            self.stop()
            return False
        dy = ev.angleDelta().y()
        if dy == 0:
            return False
        self.scroll_by(-dy / 120 * self.STEP)
        return True

    def stop(self):
        self._anim.stop()
        self._target = None

    def scroll_by(self, dy: float):
        base = self._target if self._target is not None else self._sb.value()
        self.scroll_to(base + dy)

    def scroll_to(self, value: float, *, ms: int | None = None):
        sb = self._sb
        value = max(sb.minimum(), min(sb.maximum(), value))
        dur = motion.dur(self.MS if ms is None else ms)
        self._anim.stop()
        if dur <= 0 or abs(value - sb.value()) < 1:
            self._target = None
            sb.setValue(int(round(value)))
            return
        self._target = value
        self._anim.setDuration(dur)
        self._anim.setStartValue(float(sb.value()))
        self._anim.setEndValue(float(value))
        self._anim.start()


# ── Bubble ────────────────────────────────────────────────────────────────────

class _Bubble(QWidget):
    """The floating date/time readout beside the rail."""

    def __init__(self, parent: QWidget):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground, True)
        self._text = ""
        self._alpha = 0.0
        self._fade = QVariantAnimation(self)
        self._fade.setEasingCurve(motion.ease())
        self._fade.valueChanged.connect(self._step)
        self._fade.finished.connect(self._faded)
        self.hide()

    def _step(self, v):
        self._alpha = float(v)
        self.update()

    def _faded(self):
        if self._alpha <= 0.01:
            self.hide()

    def show_at(self, text: str, right: int, cy: int):
        font = theme_mod.legend_font(9, tracking=0.2)
        fm = QFontMetrics(font)
        w = fm.horizontalAdvance(text) + 22
        h = fm.height() + 12
        self._text = text
        self.setFont(font)
        self.setGeometry(right - w, int(cy - h / 2), w, h)
        self.raise_()
        if not self.isVisible() or self._alpha < 1.0:
            self.show()
            self._fade_to(1.0)
        self.update()

    def fade_out(self):
        if self.isVisible():
            self._fade_to(0.0)

    def _fade_to(self, target: float):
        self._fade.stop()
        ms = motion.dur(motion.MICRO)
        if ms <= 0:
            self._alpha = target
            self.update()
            if target <= 0:
                self.hide()
            return
        self._fade.setDuration(ms)
        self._fade.setStartValue(self._alpha)
        self._fade.setEndValue(target)
        self._fade.start()

    def paintEvent(self, _ev):
        if self._alpha <= 0:
            return
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setOpacity(self._alpha)
        r = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        p.setPen(QPen(_c("glass_edge"), 1))
        p.setBrush(theme_mod.solid("deck_high"))
        p.drawRoundedRect(r, theme_mod.RADIUS, theme_mod.RADIUS)
        p.setPen(_c("fg"))
        p.setFont(self.font())
        p.drawText(r, Qt.AlignmentFlag.AlignCenter, self._text)
        p.end()


# ── Rail ──────────────────────────────────────────────────────────────────────

class TimelineRail(QWidget):
    """Vertical scroll rail labelled with capture times. See module docstring."""

    WIDTH = 72
    PAD = 10
    MIN_GAP = 26          # px between labels
    IDLE_MS = 900         # bubble lingers this long after scrolling stops

    def __init__(self, area: QScrollArea, bubble_parent: QWidget, scroller: SmoothScroll):
        super().__init__(bubble_parent)
        self._area = area
        self._sb = area.verticalScrollBar()
        self._scroller = scroller
        self._marks: list[tuple[int, float]] = []       # (content y, timestamp)
        self._ys: list[int] = []
        self._labels: list[tuple[float, str]] = []      # (rail y, text)
        self._mode = "day"
        self._header = ""
        self._drag_off: float | None = None
        self._hover_y: float | None = None
        self._active = 0.0                              # thumb emphasis 0..1
        self._bubble = _Bubble(bubble_parent)
        self._idle = QTimer(self)
        self._idle.setSingleShot(True)
        self._idle.setInterval(self.IDLE_MS)
        self._idle.timeout.connect(self._settle)
        self._emph = QVariantAnimation(self)
        self._emph.setEasingCurve(motion.ease())
        self._emph.valueChanged.connect(self._on_emph)
        self._scrub_target = None                       # callable(bool) — the grid
        self._rest = QTimer(self)                       # pointer paused mid-drag
        self._rest.setSingleShot(True)
        self._rest.setInterval(90)
        self._rest.timeout.connect(lambda: self._scrub(False))
        self.setFixedWidth(self.WIDTH)
        self.setMouseTracking(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setAccessibleName("Timeline")
        self.setToolTip("Timeline: drag to scrub through when the photos were taken")
        self._sb.valueChanged.connect(self._on_scrolled)
        self._sb.rangeChanged.connect(lambda *_: self._relabel())

    def set_scrub_target(self, fn):
        """``fn(True)`` while a drag is flying past rows, ``fn(False)`` when it
        rests or ends — lets the grid skip decoding rows it won't stop on."""
        self._scrub_target = fn

    def _scrub(self, on: bool):
        if self._scrub_target is not None:
            try:
                self._scrub_target(on)
            except RuntimeError:
                self._scrub_target = None

    # ── data ──────────────────────────────────────────────────────────────
    def set_marks(self, marks: list[tuple[int, float]]):
        marks = sorted(marks)
        if marks == self._marks:
            return
        self._marks = marks
        self._ys = [y for y, _ in marks]
        stamps = [t for _, t in marks]
        self._mode = pick_mode(stamps)
        self._header = ""
        if self._mode == "time" and stamps:
            d = datetime.fromtimestamp(stamps[0])
            self._header = f"{d:%b} {d.day}"
        self._relabel()

    def _content_h(self) -> int:
        return self._sb.maximum() + self._sb.pageStep()

    def _track(self) -> tuple[float, float]:
        top = self.PAD + (18 if self._header else 0)
        return top, max(1.0, self.height() - top - self.PAD)

    def _to_rail(self, content_y: float) -> float:
        top, h = self._track()
        ch = self._content_h()
        return top + (content_y / ch * h if ch > 0 else 0)

    def _to_content(self, rail_y: float) -> float:
        top, h = self._track()
        return (rail_y - top) / h * self._content_h()

    def _relabel(self):
        labels: list[tuple[float, str]] = []
        last_y, last_text = -1e9, None
        for cy, ts in self._marks:
            ry = self._to_rail(cy)
            text = label_for(ts, self._mode)
            if text != last_text and ry - last_y >= self.MIN_GAP:
                labels.append((ry, text))
                last_y, last_text = ry, text
        self._labels = labels
        self.update()

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self._relabel()

    def _stamp_at(self, content_y: float) -> float | None:
        if not self._marks:
            return None
        i = bisect.bisect_right(self._ys, content_y) - 1
        return self._marks[max(0, min(i, len(self._marks) - 1))][1]

    # ── thumb ─────────────────────────────────────────────────────────────
    def _thumb(self) -> QRectF:
        top, h = self._track()
        ch = self._content_h()
        if ch <= 0:
            return QRectF()
        th = max(26.0, self._sb.pageStep() / ch * h)
        span = self._sb.maximum() or 1
        y = top + (self._sb.value() / span) * (h - th)
        return QRectF(self.WIDTH - 10, y, 5, th)

    def _value_for_thumb_top(self, thumb_top: float) -> int:
        top, h = self._track()
        th = self._thumb().height()
        frac = (thumb_top - top) / max(1.0, h - th)
        return int(round(max(0.0, min(1.0, frac)) * self._sb.maximum()))

    # ── bubble ────────────────────────────────────────────────────────────
    def _show_bubble(self, rail_y: float, content_y: float):
        ts = self._stamp_at(content_y)
        if ts is None:
            return
        tl = self.mapTo(self.parentWidget(), QPointF(0, rail_y).toPoint())
        self._bubble.show_at(bubble_for(ts, self._mode), tl.x() - 6, tl.y())

    def _on_scrolled(self, _v):
        self.update()
        if self._drag_off is None and self._hover_y is None and self._marks:
            t = self._thumb()
            if not t.isNull():
                self._show_bubble(t.center().y(), self._sb.value() + self._sb.pageStep() / 2)
                self._set_active(1.0)
                self._idle.start()

    def _settle(self):
        if self._drag_off is None and self._hover_y is None:
            self._bubble.fade_out()
            self._set_active(0.0)

    def _set_active(self, target: float):
        if abs(target - self._active) < 0.01 and self._emph.state() != QVariantAnimation.State.Running:
            return
        self._emph.stop()
        ms = motion.dur(motion.MICRO)
        if ms <= 0:
            self._on_emph(target)
            return
        self._emph.setDuration(ms)
        self._emph.setStartValue(self._active)
        self._emph.setEndValue(target)
        self._emph.start()

    def _on_emph(self, v):
        self._active = float(v)
        self.update()

    # ── input ─────────────────────────────────────────────────────────────
    def mousePressEvent(self, e):
        if e.button() != Qt.MouseButton.LeftButton:
            return
        y = e.position().y()
        t = self._thumb()
        self._set_active(1.0)
        if not t.isNull() and t.top() - 6 <= y <= t.bottom() + 6:
            self._drag_off = y - t.top()                  # grab the thumb
        else:
            # Glide so the clicked moment lands mid-screen; keep dragging from here.
            self._drag_off = t.height() / 2 if not t.isNull() else 0.0
            target = self._to_content(y) - self._sb.pageStep() / 2
            self._scroller.scroll_to(target, ms=motion.VIEW)
        self._show_bubble(y, self._to_content(y))

    def mouseMoveEvent(self, e):
        y = e.position().y()
        if self._drag_off is not None and e.buttons() & Qt.MouseButton.LeftButton:
            self._scroller.stop()                          # the pointer wins, instantly
            self._scrub(True)
            self._rest.start()
            self._sb.setValue(self._value_for_thumb_top(y - self._drag_off))
            t = self._thumb()
            self._show_bubble(t.center().y(), self._sb.value() + self._sb.pageStep() / 2)
            return
        self._hover_y = y
        self._show_bubble(y, self._to_content(y))
        self._set_active(1.0)
        self.update()

    def mouseReleaseEvent(self, e):
        self._drag_off = None
        self._rest.stop()
        self._scrub(False)
        self._idle.start()

    def leaveEvent(self, e):
        self._hover_y = None
        if self._drag_off is None:
            self._idle.start()
        self.update()

    def wheelEvent(self, e):
        dy = e.angleDelta().y()
        if not e.pixelDelta().isNull():
            self._sb.setValue(self._sb.value() - e.pixelDelta().y())
        elif dy:
            self._scroller.scroll_by(-dy / 120 * SmoothScroll.STEP)
        e.accept()

    def hideEvent(self, e):
        self._bubble.hide()
        super().hideEvent(e)

    # ── paint ─────────────────────────────────────────────────────────────
    def paintEvent(self, _ev):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.fillRect(self.rect(), _c("canvas_bg"))
        top, h = self._track()
        track_x = self.WIDTH - 7.5
        # track
        p.setPen(QPen(_c("glass_edge"), 1))
        p.drawLine(QPointF(track_x, top), QPointF(track_x, top + h))
        ink = _c("canvas_fg")
        if self._header:
            p.setFont(theme_mod.legend_font(8, tracking=0.6))
            p.setPen(ink)
            p.drawText(QRectF(0, self.PAD - 2, self.WIDTH - 14, 16),
                       Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter, self._header)
        # labels + ticks
        p.setFont(theme_mod.legend_font(8, bold=False, tracking=0.0))
        fm = QFontMetrics(p.font())
        half = fm.height() / 2
        for ry, text in self._labels:
            p.setPen(ink)
            p.drawText(QRectF(0, ry - half, self.WIDTH - 16, fm.height()),
                       Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter, text)
            p.setPen(QPen(_c("glass_edge"), 1))
            p.drawLine(QPointF(track_x - 4, ry), QPointF(track_x, ry))
        # thumb: widens and lights while you're using it
        t = self._thumb()
        if not t.isNull():
            grow = 2.0 * self._active
            t = t.adjusted(-grow, 0, grow * 0.4, 0)
            idle, lit = _c("rail_light"), _c("accent")
            col = QColor(
                round(idle.red() + (lit.red() - idle.red()) * self._active),
                round(idle.green() + (lit.green() - idle.green()) * self._active),
                round(idle.blue() + (lit.blue() - idle.blue()) * self._active),
                round(idle.alpha() + (255 - idle.alpha()) * self._active))
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(col)
            p.drawRoundedRect(t, t.width() / 2, t.width() / 2)
        # hover marker
        if self._hover_y is not None and self._drag_off is None:
            p.setPen(QPen(_c("accent"), 1.5))
            p.drawLine(QPointF(track_x - 9, self._hover_y), QPointF(track_x + 3, self._hover_y))
        p.end()
