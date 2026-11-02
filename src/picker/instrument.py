"""PICker's controls: glass buttons, readouts, knob, switch — and their painters.

One place owns the visual vocabulary so every screen speaks it the same way:

* **Button** (`KeyCap`, name kept for call sites) — a translucent chip with a
  hairline edge and an ink label. The one committing action per screen is a
  solid accent fill; destructive actions are outlined in red. Nothing else is
  coloured. Hover eases in over a motion token (instant when motion is off).
* **Plate** (`draw_deck`) — a panel is glass over the ground: fill at a few
  percent opacity, 1px edge, 6px radius. No shadows, no gradients.
* **Readout** (`Screen`) — live values only, set in steady-width digits on a
  glass plate. Labels are muted, values are ink.
* **Knob** — anything continuous. An arc shows the range and the value; it is
  fully keyboard-driven.
* **Switch** (`ToggleSwitch`) — a binary setting, e.g. Dark/Light, Animation.
* **Brackets** — the focus/selection mark on photographs: corner brackets,
  never a coloured wash over the picture.

Accessibility is part of the vocabulary: every control is a real focusable
widget with an accessible name, Space/Enter work, the focus ring is never
removed, and every lit state also changes shape or position so it never
depends on colour alone.
"""
from __future__ import annotations

from PyQt6.QtCore import (
    QEasingCurve, QPointF, QRect, QRectF, QSize, Qt, QVariantAnimation, pyqtSignal,
)
from PyQt6.QtGui import (
    QColor, QFontMetrics, QPainter, QPainterPath, QPen, QPolygonF,
)
from PyQt6.QtWidgets import QAbstractButton, QLabel, QPushButton, QSizePolicy, QWidget

from . import motion
from . import theme as theme_mod

# ── Constants ────────────────────────────────────────────────────────────────
KEY_R = theme_mod.RADIUS   # corner radius for every control
TRAVEL = 0                 # retired: controls no longer have key travel
RAIL = 1                   # hairline weight
KEY_MS = motion.MICRO
DETENT_MS = motion.SHORT
GRID = 8                   # everything lands on this module

ACCENT_FILL = QColor(theme_mod.ACCENT_SOLID)   # filled buttons: white text >= 4.5:1


def _c(key: str) -> QColor:
    v = theme_mod.c(key)
    return v if isinstance(v, QColor) else QColor(v)


def _mix(a: QColor, b: QColor, t: float) -> QColor:
    """Blend two colours (alpha included) — used for eased hover."""
    t = max(0.0, min(1.0, t))
    return QColor(round(a.red() + (b.red() - a.red()) * t),
                  round(a.green() + (b.green() - a.green()) * t),
                  round(a.blue() + (b.blue() - a.blue()) * t),
                  round(a.alpha() + (b.alpha() - a.alpha()) * t))


# ── Painters ─────────────────────────────────────────────────────────────────

def draw_deck(p: QPainter, rect: QRect, *, top_light: bool = True,
              fill: str = "deck") -> None:
    """A glass plate: translucent fill, hairline edge, one radius."""
    p.save()
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.setPen(QPen(_c("rail"), RAIL))
    p.setBrush(_c(fill))
    p.drawRoundedRect(QRectF(rect).adjusted(0.5, 0.5, -0.5, -0.5), KEY_R, KEY_R)
    p.restore()


def draw_rail(p: QPainter, x1: int, y: int, x2: int) -> None:
    """A single hairline — the app's only divider."""
    p.setPen(QPen(_c("rail"), RAIL))
    p.drawLine(x1, y, x2, y)


def _faces(variant: str) -> tuple[QColor, QColor, QColor | None, QColor]:
    """(rest fill, hover fill, edge or None, ink) for a button variant."""
    if variant == "commit":
        return ACCENT_FILL, QColor(theme_mod.ACCENT_HOVER), None, QColor(255, 255, 255)
    if variant == "danger":
        return QColor(0, 0, 0, 0), _c("danger"), _c("danger"), _c("danger")
    rest = {"bone": "cap", "putty": "cap_alt", "dark": "cap_dark"}.get(variant, "cap_alt")
    return _c(rest), _c("cap_dark_hover") if variant == "dark" else _c("cap"), _c("rail"), _c("fg")


def draw_keycap(p: QPainter, rect: QRect, *, variant: str = "putty",
                pressed: bool = False, focused: bool = False,
                hover: float = 0.0) -> QRect:
    """Paint a button face and return the rect its label should sit in."""
    rest, hot, edge, _ink = _faces(variant)
    fill = _mix(rest, hot, hover)
    if pressed:
        fill = _mix(fill, _c("fg"), 0.10) if variant != "commit" else QColor(theme_mod.ACCENT_PRESSED)
    p.save()
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    r = QRectF(rect).adjusted(0.5, 0.5, -0.5, -0.5)
    p.setPen(QPen(edge, RAIL) if edge is not None else Qt.PenStyle.NoPen)
    p.setBrush(fill)
    p.drawRoundedRect(r, KEY_R, KEY_R)
    if focused:
        p.setPen(QPen(_c("focus"), 2))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawRoundedRect(QRectF(rect).adjusted(1, 1, -1, -1), KEY_R, KEY_R)
    p.restore()
    return QRect(rect)


def draw_brackets(p: QPainter, rect: QRect, color: QColor, *,
                  arm: int = 12, width: int = 2, inset: int = 3) -> None:
    """Corner brackets — how a selection is marked without putting colour
    over the photograph."""
    r = QRect(rect).adjusted(inset, inset, -inset, -inset)
    pen = QPen(color, width)
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    p.setPen(pen)
    p.setBrush(Qt.BrushStyle.NoBrush)
    a = min(arm, r.width() // 3, r.height() // 3)
    for (cx, cy, dx, dy) in ((r.left(), r.top(), 1, 1), (r.right(), r.top(), -1, 1),
                             (r.left(), r.bottom(), 1, -1), (r.right(), r.bottom(), -1, -1)):
        p.drawLine(cx, cy, cx + dx * a, cy)
        p.drawLine(cx, cy, cx, cy + dy * a)


def draw_screen(p: QPainter, rect: QRect) -> None:
    """The readout plate: the same glass as every panel, nothing warmer."""
    p.save()
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.setPen(QPen(_c("screen_edge"), RAIL))
    p.setBrush(_c("screen_bg"))
    p.drawRoundedRect(QRectF(rect).adjusted(0.5, 0.5, -0.5, -0.5), KEY_R, KEY_R)
    p.restore()


def draw_icon(p: QPainter, name: str, rect: QRectF, color: QColor,
              weight: float = 1.6) -> None:
    """Chrome glyphs, one stroke weight, drawn — never a text character."""
    pen = QPen(color, weight)
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
    p.setPen(pen)
    p.setBrush(Qt.BrushStyle.NoBrush)
    x, y, w, h = rect.x(), rect.y(), rect.width(), rect.height()

    def pt(fx, fy):
        return QPointF(x + fx * w, y + fy * h)

    if name == "back":
        p.drawPolyline(QPolygonF([pt(.62, .18), pt(.34, .5), pt(.62, .82)]))
    elif name == "forward":
        p.drawPolyline(QPolygonF([pt(.38, .18), pt(.66, .5), pt(.38, .82)]))
    elif name == "up":
        p.drawPolyline(QPolygonF([pt(.18, .62), pt(.5, .34), pt(.82, .62)]))
    elif name == "rescan":
        ring = QRectF(x + .18 * w, y + .18 * h, .64 * w, .64 * h)
        path = QPainterPath()
        path.arcMoveTo(ring, 110)
        path.arcTo(ring, 110, -290)          # open at the top-right
        p.drawPath(path)
        p.setBrush(color)
        p.setPen(Qt.PenStyle.NoPen)
        # arrowhead closing the loop, pointing clockwise
        p.drawPolygon(QPolygonF([pt(.50, .04), pt(.50, .32), pt(.76, .18)]))
    elif name == "sort":      # two opposed arrows — the transfer mark
        p.drawLine(pt(.18, .36), pt(.74, .36))
        p.drawPolyline(QPolygonF([pt(.60, .22), pt(.78, .36), pt(.60, .50)]))
        p.drawLine(pt(.82, .66), pt(.26, .66))
        p.drawPolyline(QPolygonF([pt(.40, .52), pt(.22, .66), pt(.40, .80)]))
    elif name == "undo":
        path = QPainterPath()
        path.moveTo(pt(.30, .40))
        path.lineTo(pt(.62, .40))
        path.cubicTo(pt(.90, .40), pt(.90, .80), pt(.62, .80))
        path.lineTo(pt(.40, .80))
        p.drawPath(path)
        p.drawPolyline(QPolygonF([pt(.42, .24), pt(.26, .40), pt(.42, .56)]))
    elif name == "palette":
        p.drawEllipse(QRectF(x + .16 * w, y + .16 * h, .68 * w, .68 * h))
        p.setBrush(color)
        p.setPen(Qt.PenStyle.NoPen)
        for fx, fy in ((.38, .36), (.60, .36), (.34, .56)):
            p.drawEllipse(QRectF(x + fx * w - 1.8, y + fy * h - 1.8, 3.6, 3.6))
    elif name == "check":
        p.drawPolyline(QPolygonF([pt(.22, .52), pt(.42, .72), pt(.78, .28)]))
    elif name == "close":
        p.drawLine(pt(.26, .26), pt(.74, .74))
        p.drawLine(pt(.74, .26), pt(.26, .74))
    elif name == "plus":
        p.drawLine(pt(.5, .22), pt(.5, .78))
        p.drawLine(pt(.22, .5), pt(.78, .5))
    elif name == "search":
        p.drawEllipse(QRectF(x + .18 * w, y + .18 * h, .46 * w, .46 * h))
        p.drawLine(pt(.62, .62), pt(.82, .82))
    elif name == "grip":
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(color)
        for row in (.28, .5, .72):
            for col in (.38, .62):
                p.drawEllipse(QRectF(x + col * w - 1.3, y + row * h - 1.3, 2.6, 2.6))
    elif name == "star":
        path = QPainterPath()
        import math
        for i in range(10):
            ang = -math.pi / 2 + i * math.pi / 5
            rad = (.46 if i % 2 == 0 else .20)
            px = x + w / 2 + math.cos(ang) * rad * w
            py = y + h / 2 + math.sin(ang) * rad * h
            path.lineTo(px, py) if i else path.moveTo(px, py)
        path.closeSubpath()
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(color)
        p.drawPath(path)
    elif name == "folder":
        p.drawPolyline(QPolygonF([
            pt(.16, .76), pt(.16, .26), pt(.42, .26), pt(.50, .38),
            pt(.84, .38), pt(.84, .76), pt(.16, .76)]))
    elif name == "play":
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(color)
        p.drawPolygon(QPolygonF([pt(.32, .22), pt(.32, .78), pt(.80, .50)]))
    elif name == "grid":
        for gx in (.18, .44, .70):
            for gy in (.18, .44, .70):
                p.drawRect(QRectF(x + gx * w, y + gy * h, .12 * w, .12 * h))
    elif name in ("zoom_in", "zoom_out"):
        # magnifier with a + or - in the lens
        lens = QRectF(x + .12 * w, y + .12 * h, .56 * w, .56 * h)
        p.drawEllipse(lens)
        p.drawLine(pt(.62, .62), pt(.86, .86))
        c = lens.center()
        arm = .14 * w
        p.drawLine(QPointF(c.x() - arm, c.y()), QPointF(c.x() + arm, c.y()))
        if name == "zoom_in":
            p.drawLine(QPointF(c.x(), c.y() - arm), QPointF(c.x(), c.y() + arm))
    elif name in ("fullscreen", "exit_fullscreen"):
        # four corner brackets: pointing out = enter, pointing in = leave
        out = name == "fullscreen"
        for sx, sy in ((0, 0), (1, 0), (0, 1), (1, 1)):
            cx = x + (.16 if sx == 0 else .84) * w
            cy = y + (.16 if sy == 0 else .84) * h
            dx = (1 if sx == 0 else -1) * (.24 * w)
            dy = (1 if sy == 0 else -1) * (.24 * h)
            if out:
                p.drawPolyline(QPolygonF([QPointF(cx, cy + dy), QPointF(cx, cy), QPointF(cx + dx, cy)]))
            else:
                ix, iy = cx + dx, cy + dy
                p.drawPolyline(QPolygonF([QPointF(ix, cy), QPointF(ix, iy), QPointF(cx, iy)]))
    elif name == "timeline":
        # a rail with date ticks: the timeline scrollbar
        p.drawLine(pt(.72, .12), pt(.72, .88))
        for fy, fx in ((.24, .30), (.50, .44), (.76, .22)):
            p.drawLine(pt(fx, fy), pt(.62, fy))
        p.setBrush(color)
        p.setPen(Qt.PenStyle.NoPen)
        p.drawRoundedRect(QRectF(x + .64 * w, y + .40 * h, .16 * w, .22 * h), 1.5, 1.5)
    elif name == "pause":
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(color)
        p.drawRoundedRect(QRectF(x + .28 * w, y + .22 * h, .15 * w, .56 * h), 1, 1)
        p.drawRoundedRect(QRectF(x + .57 * w, y + .22 * h, .15 * w, .56 * h), 1, 1)
    elif name in ("skip_back", "skip_fwd"):
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(color)
        if name == "skip_fwd":
            p.drawPolygon(QPolygonF([pt(.14, .26), pt(.14, .74), pt(.50, .50)]))
            p.drawPolygon(QPolygonF([pt(.50, .26), pt(.50, .74), pt(.86, .50)]))
        else:
            p.drawPolygon(QPolygonF([pt(.86, .26), pt(.86, .74), pt(.50, .50)]))
            p.drawPolygon(QPolygonF([pt(.50, .26), pt(.50, .74), pt(.14, .50)]))
    elif name == "loop":
        path = QPainterPath()
        path.moveTo(pt(.24, .56))
        path.lineTo(pt(.24, .40))
        path.quadTo(pt(.24, .28), pt(.36, .28))
        path.lineTo(pt(.76, .28))
        p.drawPath(path)
        p.drawPolyline(QPolygonF([pt(.66, .16), pt(.78, .28), pt(.66, .40)]))
        path = QPainterPath()
        path.moveTo(pt(.76, .44))
        path.lineTo(pt(.76, .60))
        path.quadTo(pt(.76, .72), pt(.64, .72))
        path.lineTo(pt(.24, .72))
        p.drawPath(path)
        p.drawPolyline(QPolygonF([pt(.34, .60), pt(.22, .72), pt(.34, .84)]))
    elif name in ("volume", "mute"):
        p.setBrush(color)
        p.setPen(Qt.PenStyle.NoPen)
        p.drawPolygon(QPolygonF([pt(.12, .38), pt(.28, .38), pt(.48, .20),
                                 pt(.48, .80), pt(.28, .62), pt(.12, .62)]))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.setPen(pen)
        if name == "volume":
            p.drawArc(QRectF(x + .44 * w, y + .32 * h, .22 * w, .36 * h), -60 * 16, 120 * 16)
            p.drawArc(QRectF(x + .44 * w, y + .18 * h, .38 * w, .64 * h), -60 * 16, 120 * 16)
        else:
            p.drawLine(pt(.62, .36), pt(.86, .64))
            p.drawLine(pt(.86, .36), pt(.62, .64))



# ── Hover easing ─────────────────────────────────────────────────────────────

class _HoverEase:
    """A 0..1 hover amount that eases over motion.MICRO, or snaps when motion
    is off. One small animation per control, only while the pointer moves."""

    def __init__(self, widget: QWidget):
        self._w = widget
        self.value = 0.0
        self._anim = QVariantAnimation(widget)
        self._anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._anim.valueChanged.connect(self._on_step)

    def _on_step(self, v):
        self.value = float(v)
        self._w.update()

    def to(self, target: float) -> None:
        self._anim.stop()
        ms = motion.dur(motion.MICRO)
        if ms <= 0:
            self.value = target
            self._w.update()
            return
        self._anim.setDuration(ms)
        self._anim.setStartValue(self.value)
        self._anim.setEndValue(target)
        self._anim.start()


# ── Widgets ──────────────────────────────────────────────────────────────────

class KeyCap(QPushButton):
    """A button as a glass chip. `variant`: putty | bone | dark | commit | danger.

    Keeps every QPushButton affordance — focus, Space/Enter, accessible name,
    shortcuts. `hint` prints the key that does the same thing, quietly, after
    the label (e.g. Back · Esc).
    """

    def __init__(self, text: str = "", *, variant: str = "putty", icon: str = "",
                 hint: str = "", units: float = 0, parent=None):
        super().__init__(text, parent)
        self._variant = variant
        self._icon = icon
        self._hint = hint
        self._hover = _HoverEase(self)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFlat(True)
        self.setAttribute(Qt.WidgetAttribute.WA_Hover, True)
        self.setFont(theme_mod.legend_font(9, tracking=0.6))
        self.setAccessibleName(text or icon or "button")
        self.setMinimumHeight(32)
        if units:
            self.setFixedWidth(int(units * 34))
        self.setSizePolicy(QSizePolicy.Policy.Minimum, QSizePolicy.Policy.Fixed)

    def set_variant(self, variant: str) -> None:
        if variant != self._variant:
            self._variant = variant
            self.update()

    def set_icon(self, name: str, accessible_name: str = "") -> None:
        """Swap the drawn icon (e.g. play <-> pause) and, optionally, the name
        a screen reader announces for it."""
        self._icon = name
        if accessible_name:
            self.setAccessibleName(accessible_name)
        self.update()

    def enterEvent(self, e):
        if self.isEnabled():
            self._hover.to(1.0)
        super().enterEvent(e)

    def leaveEvent(self, e):
        self._hover.to(0.0)
        super().leaveEvent(e)

    def sizeHint(self) -> QSize:
        fm = QFontMetrics(self.font())
        w = fm.horizontalAdvance(self.text()) + 28
        if self._icon:
            w += 22 if self.text() else 6
        if self._hint:
            w += QFontMetrics(theme_mod.legend_font(8, bold=False)).horizontalAdvance(self._hint) + 12
        return QSize(max(w, 34), 32)

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        variant = self._variant if self.isEnabled() else "dark"
        if self.isCheckable() and self.isChecked():
            variant = "commit"          # a latched toggle reads as lit
        body = draw_keycap(p, self.rect(), variant=variant, pressed=self.isDown(),
                           focused=self.hasFocus(), hover=self._hover.value)
        _rest, _hot, _edge, ink = _faces(variant)
        if variant == "danger" and self._hover.value > 0.5:
            ink = QColor(255, 255, 255)
        if not self.isEnabled():
            ink = _c("faint")
        inner = body.adjusted(12, 0, -12, 0)
        if self._icon:
            side = min(15, inner.height() - 12)
            if not self.text() and not self._hint:
                ix = body.center().x() - side / 2 + 0.5
            else:
                ix = inner.left()
            draw_icon(p, self._icon, QRectF(ix, body.center().y() - side / 2 + 0.5, side, side),
                      ink, 1.6)
            inner.setLeft(inner.left() + side + 8)
        hint_w = 0
        if self._hint:
            hf = theme_mod.legend_font(8, bold=False, tracking=0.4)
            p.setFont(hf)
            hint_w = QFontMetrics(hf).horizontalAdvance(self._hint) + 10
            hr = QRect(inner.right() - hint_w + 4, inner.top(), hint_w - 4, inner.height())
            hint_ink = QColor(255, 255, 255, 215) if variant == "commit" else _c("muted")
            p.setPen(hint_ink)
            p.drawText(hr, Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignRight,
                       self._hint)
        if self.text():
            p.setFont(self.font())
            p.setPen(ink)
            p.drawText(inner.adjusted(0, 0, -hint_w, 0),
                       Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft
                       if (self._icon or hint_w) else Qt.AlignmentFlag.AlignCenter,
                       self.text())
        p.end()


class Screen(QWidget):
    """The readout. Fields are (label, value) pairs; it reports live values
    only — never decoration."""

    def __init__(self, parent=None, *, height: int = 40):
        super().__init__(parent)
        self._fields: list[tuple[str, str]] = []
        self._note = ""
        self.setFixedHeight(min(height, 40))
        self.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Fixed)

    def sizeHint(self) -> QSize:
        lf = QFontMetrics(theme_mod.legend_font(7, tracking=1.0))
        vf = QFontMetrics(theme_mod.readout_font(11))
        w = 14
        for label, value in self._fields:
            w += max(lf.horizontalAdvance(label), vf.horizontalAdvance(value)) + 24
        if self._note:
            w += QFontMetrics(theme_mod.legend_font(8, bold=False, tracking=0.6)
                              ).horizontalAdvance(self._note) + 8
        return QSize(max(w, 120), self.height())

    def minimumSizeHint(self) -> QSize:
        return self.sizeHint()

    def set_fields(self, fields: list[tuple[str, str]], note: str = "") -> None:
        if fields != self._fields or note != self._note:
            self._fields = list(fields)
            self._note = note
            self.setAccessibleName(
                "; ".join(f"{k} {v}" for k, v in self._fields) + (f"; {note}" if note else ""))
            self.updateGeometry()
            self.update()

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        draw_screen(p, self.rect())
        h = self.height()
        x = 14
        lf = theme_mod.legend_font(7, tracking=1.0)
        vf = theme_mod.readout_font(11)
        for label, value in self._fields:
            p.setFont(lf)
            p.setPen(_c("screen_dim"))
            lw = QFontMetrics(lf).horizontalAdvance(label)
            p.drawText(QRect(x, 4, lw + 4, h // 2 - 3),
                       Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignBottom, label)
            p.setFont(vf)
            p.setPen(_c("screen_fg"))
            vw = QFontMetrics(vf).horizontalAdvance(value)
            p.drawText(QRect(x, h // 2 - 1, vw + 6, h // 2 - 2),
                       Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop, value)
            x += max(lw, vw) + 24
        if self._note:
            nf = theme_mod.legend_font(8, bold=False, tracking=0.6)
            p.setFont(nf)
            p.setPen(_c("screen_dim"))
            p.drawText(self.rect().adjusted(0, 0, -14, 0),
                       Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
                       self._note)
        p.end()


class Knob(QWidget):
    """Rotary for a continuous value. Drag or wheel to turn; arrows, Home and
    End drive it from the keyboard with the same steps."""

    valueChanged = pyqtSignal(int)

    def __init__(self, minimum: int, maximum: int, value: int, *, label: str = "",
                 step: int = 1, parent=None):
        super().__init__(parent)
        self._min, self._max = minimum, maximum
        self._value = max(minimum, min(maximum, value))
        self._label = label
        self._step = step
        self._drag_y = None
        self._hover = _HoverEase(self)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setCursor(Qt.CursorShape.SizeVerCursor)
        self.setFixedSize(34, 34)
        self.setAccessibleName(label or "value knob")
        self.setAccessibleDescription(str(self._value))
        self.setToolTip(f"{label}: drag, scroll or use arrow keys")

    def value(self) -> int:
        return self._value

    def set_value(self, v: int) -> None:
        v = max(self._min, min(self._max, int(v)))
        if v != self._value:
            self._value = v
            self.setAccessibleDescription(str(v))
            self.update()
            self.valueChanged.emit(v)

    def enterEvent(self, e):
        self._hover.to(1.0)

    def leaveEvent(self, e):
        self._hover.to(0.0)

    def wheelEvent(self, e):
        self.set_value(self._value + (self._step if e.angleDelta().y() > 0 else -self._step))
        e.accept()

    def mousePressEvent(self, e):
        self._drag_y = e.position().y()
        self.setFocus(Qt.FocusReason.MouseFocusReason)

    def mouseMoveEvent(self, e):
        if self._drag_y is None:
            return
        dy = self._drag_y - e.position().y()
        if abs(dy) >= 4:
            self.set_value(self._value + self._step * (1 if dy > 0 else -1))
            self._drag_y = e.position().y()

    def mouseReleaseEvent(self, e):
        self._drag_y = None

    def keyPressEvent(self, e):
        k = e.key()
        if k in (Qt.Key.Key_Up, Qt.Key.Key_Right, Qt.Key.Key_Plus, Qt.Key.Key_Equal):
            self.set_value(self._value + self._step)
        elif k in (Qt.Key.Key_Down, Qt.Key.Key_Left, Qt.Key.Key_Minus):
            self.set_value(self._value - self._step)
        elif k == Qt.Key.Key_Home:
            self.set_value(self._min)
        elif k == Qt.Key.Key_End:
            self.set_value(self._max)
        else:
            super().keyPressEvent(e)

    def paintEvent(self, event):
        import math
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = QRectF(self.rect()).adjusted(4, 4, -4, -4)
        frac = (self._value - self._min) / max(1, (self._max - self._min))
        # glass disc
        p.setPen(QPen(_c("rail"), 1))
        p.setBrush(_mix(_c("cap_dark"), _c("cap_dark_hover"), self._hover.value))
        p.drawEllipse(r)
        # track + value arc (Qt angles: 1/16 degree, counter-clockwise from 3 o'clock)
        arc = r.adjusted(3, 3, -3, -3)
        start, span = 225, -270
        track = QPen(_c("rail_light"), 2)
        track.setCapStyle(Qt.PenCapStyle.RoundCap)
        p.setPen(track)
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawArc(arc, start * 16, span * 16)
        lit = QPen(_c("focus") if self.hasFocus() else _c("accent"), 2)
        lit.setCapStyle(Qt.PenCapStyle.RoundCap)
        p.setPen(lit)
        p.drawArc(arc, start * 16, round(span * frac * 16))
        # pointer dot at the value
        ang = math.radians(start + span * frac)
        cx, cy = arc.center().x(), arc.center().y()
        rad = arc.width() / 2 - 5
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(_c("fg"))
        p.drawEllipse(QPointF(cx + math.cos(ang) * rad, cy - math.sin(ang) * rad), 1.8, 1.8)
        if self.hasFocus():
            p.setPen(QPen(_c("focus"), 1.5))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawEllipse(QRectF(self.rect()).adjusted(1, 1, -1, -1))
        p.end()


class ToggleSwitch(QAbstractButton):
    """A binary setting as a switch, with its label on the right.

    A real checkable button: Tab focuses it, Space flips it, `toggled(bool)`
    reports it and the accessible name is the label. State shows by the thumb's
    *position* as well as the track colour, so it never depends on colour."""

    TRACK_W, TRACK_H = 36, 20

    def __init__(self, text: str = "", checked: bool = False, parent=None):
        super().__init__(parent)
        self.setText(text)
        self.setCheckable(True)
        self.setChecked(checked)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setAccessibleName(text or "switch")
        self.setFont(theme_mod.legend_font(10, bold=False, tracking=0.0))
        self._pos = 1.0 if checked else 0.0
        self._anim = QVariantAnimation(self)
        self._anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._anim.valueChanged.connect(self._on_step)
        self.toggled.connect(self._animate_to)
        self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)

    def _on_step(self, v):
        self._pos = float(v)
        self.update()

    def _animate_to(self, on: bool):
        target = 1.0 if on else 0.0
        self._anim.stop()
        ms = motion.dur(motion.MICRO)
        if ms <= 0:
            self._pos = target
            self.update()
            return
        self._anim.setDuration(ms)
        self._anim.setStartValue(self._pos)
        self._anim.setEndValue(target)
        self._anim.start()

    def sizeHint(self) -> QSize:
        w = self.TRACK_W + 4
        if self.text():
            w += 12 + QFontMetrics(self.font()).horizontalAdvance(self.text())
        return QSize(w, 28)

    def hitButton(self, pos) -> bool:
        return self.rect().contains(pos)

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        y = (self.height() - self.TRACK_H) / 2
        track = QRectF(2, y, self.TRACK_W, self.TRACK_H)
        on = _c("accent")
        off = _c("cap")
        p.setPen(QPen(_c("glass_edge"), 1) if self._pos < 0.5 else Qt.PenStyle.NoPen)
        p.setBrush(_mix(off, on, self._pos))
        p.drawRoundedRect(track, self.TRACK_H / 2, self.TRACK_H / 2)
        d = self.TRACK_H - 6
        x = track.left() + 3 + (track.width() - d - 6) * self._pos
        # white thumb; on the light ground it needs an edge while off
        p.setPen(QPen(_c("glass_edge"), 1) if (self._pos < 0.5 and theme_mod.is_light())
                 else Qt.PenStyle.NoPen)
        p.setBrush(QColor(255, 255, 255))
        p.drawEllipse(QRectF(x, track.top() + 3, d, d))
        if self.hasFocus():
            p.setPen(QPen(_c("focus"), 2))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawRoundedRect(track.adjusted(-2, -2, 2, 2), self.TRACK_H / 2 + 2, self.TRACK_H / 2 + 2)
        if self.text():
            p.setPen(_c("fg") if self.isEnabled() else _c("faint"))
            p.setFont(self.font())
            p.drawText(QRectF(track.right() + 12, 0, self.width() - track.right() - 12, self.height()),
                       Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, self.text())
        p.end()


# ── Label factories ──────────────────────────────────────────────────────────

def legend(text: str, *, size: int = 9, color: str = "muted",
           tracking: float = 1.6) -> QLabel:
    """A small uppercase panel label, restyled live on a mode change."""
    lb = QLabel(text.upper())
    lb.setFont(theme_mod.legend_font(size, tracking=tracking))
    theme_mod.bind(lb, lambda: f"color: {theme_mod.hexc(color)}; background: transparent;")
    return lb


def value_label(text: str = "", *, size: int = 12, color: str = "fg") -> QLabel:
    lb = QLabel(text)
    lb.setFont(theme_mod.readout_font(size))
    theme_mod.bind(lb, lambda: f"color: {theme_mod.hexc(color)}; background: transparent;")
    return lb


def deck_qss(name: str) -> str:
    """Glass plate styling for a named container widget."""
    return (f"#{name} {{ background: {theme_mod.hexc('deck')};"
            f" border: 1px solid {theme_mod.hexc('rail')};"
            f" border-radius: {theme_mod.RADIUS}px; }}")



class StatusLine(QLabel):
    """Quiet one-line readout (e.g. "3 folders · 24 photos · In memory").
    Same ``set_fields`` API as the old Screen, so callers didn't change."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._fields: list[tuple[str, str]] = []
        self._note = ""
        self.setFont(theme_mod.legend_font(9, bold=False, tracking=0.0))
        theme_mod.bind(self, lambda: f"color: {theme_mod.hexc('muted')}; background: transparent;")

    def set_fields(self, fields: list[tuple[str, str]], note: str = "") -> None:
        if fields == self._fields and note == self._note:
            return
        self._fields, self._note = list(fields), note
        parts = [f"{v} {k.lower()}" for k, v in fields]
        if note:
            parts.append(note)
        text = "  ·  ".join(parts)
        self.setText(text)
        self.setAccessibleName(text)


class ZoomSlider(QWidget):
    """Tile size: zoom-out magnifier, a slider, zoom-in magnifier.

    Keeps the Knob API (``value``, ``set_value``, ``valueChanged``) so callers
    swap one for the other. The slider takes focus and answers arrows, Page
    Up/Down, Home/End; the magnifier buttons step by one notch."""

    valueChanged = pyqtSignal(int)

    def __init__(self, minimum: int, maximum: int, value: int, *, step: int = 26,
                 label: str = "Tile size", parent=None):
        super().__init__(parent)
        from PyQt6.QtWidgets import QHBoxLayout, QSlider
        self._step = step
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(2)
        self._out = KeyCap("", variant="dark", icon="zoom_out")
        self._out.setAccessibleName("Smaller tiles")
        self._out.setToolTip("Smaller tiles (Ctrl+wheel or - on the grid)")
        self._out.setFixedSize(28, 28)
        self._in = KeyCap("", variant="dark", icon="zoom_in")
        self._in.setAccessibleName("Larger tiles")
        self._in.setToolTip("Larger tiles (Ctrl+wheel or + on the grid)")
        self._in.setFixedSize(28, 28)
        for b in (self._out, self._in):
            b.setMinimumHeight(28)
            b.setFocusPolicy(Qt.FocusPolicy.NoFocus)   # the slider is the focus stop
        self._slider = QSlider(Qt.Orientation.Horizontal)
        self._slider.setRange(minimum, maximum)
        self._slider.setValue(max(minimum, min(maximum, value)))
        self._slider.setSingleStep(max(1, step // 4))
        self._slider.setPageStep(step)
        self._slider.setFixedWidth(120)
        self._slider.setAccessibleName(label)
        self._slider.setToolTip(label)
        self._slider.setCursor(Qt.CursorShape.PointingHandCursor)
        theme_mod.bind(self._slider, lambda: (
            f"QSlider::groove:horizontal {{ height: 4px; border-radius: 2px;"
            f" background: {theme_mod.hexc('rail_light')}; }}"
            f"QSlider::sub-page:horizontal {{ border-radius: 2px;"
            f" background: {theme_mod.ACCENT_HEX}; }}"
            f"QSlider::handle:horizontal {{ width: 14px; height: 14px; margin: -5px 0;"
            f" border-radius: 7px; background: #ffffff;"
            f" border: 1px solid {theme_mod.hexc('glass_edge')}; }}"
            f"QSlider::handle:horizontal:hover {{ border: 1px solid {theme_mod.ACCENT_HEX}; }}"
            f"QSlider:focus {{ outline: none; }}"
            f"QSlider::handle:horizontal:focus {{ border: 2px solid {theme_mod.hexc('focus')}; }}"))
        self._slider.valueChanged.connect(self.valueChanged.emit)
        self._out.clicked.connect(lambda: self._nudge(-1))
        self._in.clicked.connect(lambda: self._nudge(+1))
        lay.addWidget(self._out)
        lay.addWidget(self._slider)
        lay.addWidget(self._in)
        # A compact unit: containers (e.g. a status bar) must not stretch it
        # and push the magnifiers away from the slider.
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        self.setFixedWidth(28 + 2 + 120 + 2 + 28)

    def _nudge(self, direction: int) -> None:
        self._slider.setValue(self._slider.value() + direction * self._step)

    def value(self) -> int:
        return self._slider.value()

    def set_value(self, v: int) -> None:
        self._slider.setValue(int(v))

    def blockSignals(self, b: bool) -> bool:          # noqa: N802 — Qt name
        self._slider.blockSignals(b)
        return super().blockSignals(b)



class Segmented(QWidget):
    """A two-or-more option switch shown as one pill with the chosen segment
    lit — every option visible at once, so the current choice is never a
    guess. Click a segment, or Tab to it and use Left/Right (Space/Enter also
    advance). Emits ``changed(key)``."""

    changed = pyqtSignal(str)

    def __init__(self, options: list[tuple[str, str]], value: str | None = None,
                 *, name: str = "", parent=None):
        super().__init__(parent)
        self._opts = list(options)
        self._keys = [k for k, _ in self._opts]
        self._value = value if value in self._keys else self._keys[0]
        self._hover = -1
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setMouseTracking(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFont(theme_mod.legend_font(9, tracking=0.2))
        self.setAccessibleName(name or "options")
        self._sync_a11y()
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)

    def value(self) -> str:
        return self._value

    def set_value(self, key: str, *, emit: bool = False) -> None:
        if key not in self._keys or key == self._value:
            return
        self._value = key
        self._sync_a11y()
        self.update()
        if emit:
            self.changed.emit(key)

    def _sync_a11y(self):
        label = dict(self._opts)[self._value]
        self.setAccessibleDescription(f"{label} selected")
        self.setToolTip(" / ".join(t for _, t in self._opts) + f" (now: {label})")

    def _seg_widths(self) -> list[int]:
        fm = QFontMetrics(self.font())
        return [fm.horizontalAdvance(t) + 22 for _, t in self._opts]

    def sizeHint(self) -> QSize:
        return QSize(sum(self._seg_widths()) + 4, 30)

    def _index_at(self, x: float) -> int:
        acc = 2
        for i, w in enumerate(self._seg_widths()):
            if x < acc + w:
                return i
            acc += w
        return len(self._opts) - 1

    def mousePressEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton:
            self.setFocus(Qt.FocusReason.MouseFocusReason)
            self.set_value(self._keys[self._index_at(e.position().x())], emit=True)

    def mouseMoveEvent(self, e):
        i = self._index_at(e.position().x())
        if i != self._hover:
            self._hover = i
            self.update()

    def leaveEvent(self, e):
        self._hover = -1
        self.update()

    def keyPressEvent(self, e):
        i = self._keys.index(self._value)
        k = e.key()
        if k in (Qt.Key.Key_Left, Qt.Key.Key_Up):
            i = max(0, i - 1)
        elif k in (Qt.Key.Key_Right, Qt.Key.Key_Down):
            i = min(len(self._keys) - 1, i + 1)
        elif k in (Qt.Key.Key_Space, Qt.Key.Key_Return, Qt.Key.Key_Enter):
            i = (i + 1) % len(self._keys)
        else:
            super().keyPressEvent(e)
            return
        self.set_value(self._keys[i], emit=True)

    def focusInEvent(self, e):
        self.update()

    def focusOutEvent(self, e):
        self.update()

    def paintEvent(self, _ev):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        p.setPen(QPen(_c("rail"), 1))
        p.setBrush(_c("cap_dark"))
        p.drawRoundedRect(r, KEY_R, KEY_R)
        x = 2.0
        p.setFont(self.font())
        for i, (key, text) in enumerate(self._opts):
            w = self._seg_widths()[i]
            seg = QRectF(x, 2, w, self.height() - 4)
            on = key == self._value
            if on:
                p.setPen(Qt.PenStyle.NoPen)
                p.setBrush(ACCENT_FILL)
                p.drawRoundedRect(seg, KEY_R - 2, KEY_R - 2)
            elif i == self._hover:
                p.setPen(Qt.PenStyle.NoPen)
                p.setBrush(_c("cap_dark_hover"))
                p.drawRoundedRect(seg, KEY_R - 2, KEY_R - 2)
            p.setPen(QColor(255, 255, 255) if on else _c("fg"))
            p.drawText(seg, Qt.AlignmentFlag.AlignCenter, text)
            x += w
        if self.hasFocus():
            p.setPen(QPen(_c("focus"), 2))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawRoundedRect(QRectF(self.rect()).adjusted(1, 1, -1, -1), KEY_R, KEY_R)
        p.end()
