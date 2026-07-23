from PyQt6.QtCore import Qt, QRectF, QPointF
from PyQt6.QtGui import QIcon, QPixmap, QPainter, QColor, QPen, QBrush, QLinearGradient, QPolygonF
import math


def _make_pixmap(size: int) -> QPixmap:
    """Camera aperture icon. Dark bg circle, 6 angled blades around center."""
    pm = QPixmap(size, size)
    pm.fill(Qt.GlobalColor.transparent)

    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)

    cx, cy = size / 2, size / 2
    r = size * 0.46

    # Background disc with gradient
    grad = QLinearGradient(0, 0, 0, size)
    grad.setColorAt(0.0, QColor(55, 130, 220))
    grad.setColorAt(1.0, QColor(25, 60, 130))
    p.setBrush(QBrush(grad))
    p.setPen(QPen(QColor(10, 20, 40), max(1, size // 64)))
    p.drawEllipse(QPointF(cx, cy), r, r)

    # Aperture blades — 6 triangles rotating around center
    blade_count = 6
    inner_r = r * 0.15
    outer_r = r * 0.88
    p.setPen(Qt.PenStyle.NoPen)

    for i in range(blade_count):
        angle = (i / blade_count) * 2 * math.pi
        next_angle = ((i + 1) / blade_count) * 2 * math.pi

        tip = QPointF(cx + outer_r * math.cos(angle),
                      cy + outer_r * math.sin(angle))
        base_a = QPointF(cx + inner_r * math.cos(angle + 0.5),
                         cy + inner_r * math.sin(angle + 0.5))
        base_b = QPointF(cx + outer_r * math.cos(next_angle - 0.15),
                         cy + outer_r * math.sin(next_angle - 0.15))

        shade = 230 if i % 2 == 0 else 200
        p.setBrush(QColor(shade, shade, shade, 230))
        poly = QPolygonF([tip, base_a, base_b])
        p.drawPolygon(poly)

    # Center hole
    p.setBrush(QColor(15, 30, 55))
    p.setPen(QPen(QColor(0, 0, 0, 120), max(1, size // 80)))
    p.drawEllipse(QPointF(cx, cy), inner_r * 0.9, inner_r * 0.9)

    p.end()
    return pm


def app_icon() -> QIcon:
    icon = QIcon()
    for sz in (16, 24, 32, 48, 64, 128, 256):
        icon.addPixmap(_make_pixmap(sz))
    return icon


# ── Context-menu glyph icons ────────────────────────────────────────────────
# Flat 1-color glyphs sized for QMenu (16px base, multi-res). Drawn in a light
# neutral so they read on the dark menu background. `name` selects the glyph.

_MENU_FG = QColor(220, 224, 230)
_MENU_ACCENT = QColor(90, 150, 230)


def _draw_glyph(p: QPainter, name: str, s: int) -> None:
    """Paint glyph `name` into an s×s canvas (origin 0,0)."""
    fg = _MENU_FG
    pen = QPen(fg, max(1.0, s * 0.085))
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)

    def box(x0, y0, x1, y1):
        return QRectF(x0 * s, y0 * s, (x1 - x0) * s, (y1 - y0) * s)

    if name == "slideshow":
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(fg)
        p.drawPolygon(QPolygonF([
            QPointF(0.30 * s, 0.22 * s),
            QPointF(0.30 * s, 0.78 * s),
            QPointF(0.80 * s, 0.50 * s),
        ]))

    elif name in ("photoshop", "lightroom"):
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(35, 60, 115) if name == "photoshop" else QColor(40, 90, 150))
        p.drawRoundedRect(box(0.12, 0.12, 0.88, 0.88), s * 0.16, s * 0.16)
        f = p.font(); f.setBold(True); f.setPixelSize(int(s * 0.5)); p.setFont(f)
        p.setPen(QColor(120, 190, 255))
        p.drawText(box(0.12, 0.10, 0.88, 0.90),
                   Qt.AlignmentFlag.AlignCenter,
                   "Ps" if name == "photoshop" else "Lr")

    elif name == "system":
        p.setPen(pen); p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawRoundedRect(box(0.15, 0.20, 0.85, 0.70), s * 0.06, s * 0.06)
        p.drawLine(QPointF(0.38 * s, 0.82 * s), QPointF(0.62 * s, 0.82 * s))
        p.drawLine(QPointF(0.50 * s, 0.70 * s), QPointF(0.50 * s, 0.82 * s))

    elif name == "copy_path":
        p.setPen(pen); p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawRoundedRect(box(0.24, 0.16, 0.76, 0.86), s * 0.07, s * 0.07)
        p.setBrush(fg)
        p.drawRoundedRect(box(0.38, 0.10, 0.62, 0.22), s * 0.04, s * 0.04)

    elif name == "reveal":
        # open folder
        p.setPen(Qt.PenStyle.NoPen); p.setBrush(fg)
        p.drawPolygon(QPolygonF([
            QPointF(0.14 * s, 0.30 * s), QPointF(0.42 * s, 0.30 * s),
            QPointF(0.50 * s, 0.40 * s), QPointF(0.86 * s, 0.40 * s),
            QPointF(0.86 * s, 0.78 * s), QPointF(0.14 * s, 0.78 * s),
        ]))

    elif name == "folder":
        p.setPen(Qt.PenStyle.NoPen); p.setBrush(fg)
        p.drawPolygon(QPolygonF([
            QPointF(0.14 * s, 0.28 * s), QPointF(0.44 * s, 0.28 * s),
            QPointF(0.52 * s, 0.38 * s), QPointF(0.86 * s, 0.38 * s),
            QPointF(0.86 * s, 0.78 * s), QPointF(0.14 * s, 0.78 * s),
        ]))

    elif name == "move":
        # folder + right arrow into it
        p.setPen(Qt.PenStyle.NoPen); p.setBrush(QColor(180, 186, 196))
        p.drawPolygon(QPolygonF([
            QPointF(0.42 * s, 0.30 * s), QPointF(0.62 * s, 0.30 * s),
            QPointF(0.68 * s, 0.38 * s), QPointF(0.90 * s, 0.38 * s),
            QPointF(0.90 * s, 0.80 * s), QPointF(0.42 * s, 0.80 * s),
        ]))
        ap = QPen(_MENU_ACCENT, max(1.0, s * 0.11))
        ap.setCapStyle(Qt.PenCapStyle.RoundCap)
        ap.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        p.setPen(ap); p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawLine(QPointF(0.10 * s, 0.58 * s), QPointF(0.46 * s, 0.58 * s))
        p.drawPolyline(QPolygonF([
            QPointF(0.34 * s, 0.46 * s), QPointF(0.48 * s, 0.58 * s),
            QPointF(0.34 * s, 0.70 * s),
        ]))

    elif name == "copy":
        p.setPen(pen); p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawRoundedRect(box(0.22, 0.14, 0.62, 0.62), s * 0.06, s * 0.06)
        p.drawRoundedRect(box(0.40, 0.38, 0.80, 0.86), s * 0.06, s * 0.06)

    elif name == "delete":
        ap = QPen(QColor(225, 110, 110), max(1.0, s * 0.085))
        ap.setCapStyle(Qt.PenCapStyle.RoundCap)
        ap.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        p.setPen(ap); p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawLine(QPointF(0.22 * s, 0.26 * s), QPointF(0.78 * s, 0.26 * s))
        p.drawLine(QPointF(0.42 * s, 0.18 * s), QPointF(0.58 * s, 0.18 * s))
        p.drawPolyline(QPolygonF([
            QPointF(0.28 * s, 0.30 * s), QPointF(0.34 * s, 0.82 * s),
            QPointF(0.66 * s, 0.82 * s), QPointF(0.72 * s, 0.30 * s),
        ]))
        p.drawLine(QPointF(0.43 * s, 0.40 * s), QPointF(0.45 * s, 0.74 * s))
        p.drawLine(QPointF(0.57 * s, 0.40 * s), QPointF(0.55 * s, 0.74 * s))

    elif name == "select_all":
        p.setPen(pen); p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawRoundedRect(box(0.16, 0.16, 0.84, 0.84), s * 0.10, s * 0.10)
        ap = QPen(_MENU_ACCENT, max(1.0, s * 0.11))
        ap.setCapStyle(Qt.PenCapStyle.RoundCap)
        ap.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        p.setPen(ap)
        p.drawPolyline(QPolygonF([
            QPointF(0.32 * s, 0.52 * s), QPointF(0.45 * s, 0.66 * s),
            QPointF(0.70 * s, 0.34 * s),
        ]))

    elif name == "clear":
        p.setPen(pen); p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawRoundedRect(box(0.16, 0.16, 0.84, 0.84), s * 0.10, s * 0.10)
        p.drawLine(QPointF(0.34 * s, 0.34 * s), QPointF(0.66 * s, 0.66 * s))
        p.drawLine(QPointF(0.66 * s, 0.34 * s), QPointF(0.34 * s, 0.66 * s))

    elif name == "rotate":
        # Circular arrow
        p.setPen(pen); p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawArc(box(0.20, 0.20, 0.80, 0.80), 40 * 16, 280 * 16)
        p.setBrush(_MENU_FG); p.setPen(Qt.PenStyle.NoPen)
        p.drawPolygon(QPolygonF([
            QPointF(0.78 * s, 0.16 * s), QPointF(0.86 * s, 0.42 * s),
            QPointF(0.60 * s, 0.36 * s),
        ]))

    elif name == "crop":
        p.setPen(pen); p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawLine(QPointF(0.28 * s, 0.10 * s), QPointF(0.28 * s, 0.72 * s))
        p.drawLine(QPointF(0.28 * s, 0.72 * s), QPointF(0.90 * s, 0.72 * s))
        p.drawLine(QPointF(0.10 * s, 0.28 * s), QPointF(0.72 * s, 0.28 * s))
        p.drawLine(QPointF(0.72 * s, 0.28 * s), QPointF(0.72 * s, 0.90 * s))

    elif name == "zoom":
        p.setPen(pen); p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawEllipse(box(0.14, 0.14, 0.66, 0.66))
        p.drawLine(QPointF(0.62 * s, 0.62 * s), QPointF(0.88 * s, 0.88 * s))

    elif name == "info":
        p.setPen(pen); p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawEllipse(box(0.14, 0.14, 0.86, 0.86))
        p.setPen(QPen(_MENU_ACCENT, max(1.0, s * 0.11)))
        p.drawPoint(QPointF(0.50 * s, 0.33 * s))
        p.drawLine(QPointF(0.50 * s, 0.45 * s), QPointF(0.50 * s, 0.70 * s))

    elif name == "compare":
        # Two panes side by side
        p.setPen(pen); p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawRoundedRect(box(0.10, 0.22, 0.46, 0.78), s * 0.07, s * 0.07)
        p.drawRoundedRect(box(0.54, 0.22, 0.90, 0.78), s * 0.07, s * 0.07)

    elif name == "fullscreen":
        p.setPen(pen); p.setBrush(Qt.BrushStyle.NoBrush)
        for (x1, y1, x2, y2, hx, hy) in (
            (0.14, 0.34, 0.14, 0.14, 0.34, 0.14),
            (0.86, 0.34, 0.86, 0.14, 0.66, 0.14),
            (0.14, 0.66, 0.14, 0.86, 0.34, 0.86),
            (0.86, 0.66, 0.86, 0.86, 0.66, 0.86),
        ):
            p.drawLine(QPointF(x1 * s, y1 * s), QPointF(x2 * s, y2 * s))
            p.drawLine(QPointF(x2 * s, y2 * s), QPointF(hx * s, hy * s))

    elif name == "histogram":
        p.setPen(Qt.PenStyle.NoPen); p.setBrush(_MENU_FG)
        for (x, top) in ((0.20, 0.55), (0.36, 0.30), (0.52, 0.44), (0.68, 0.20)):
            p.drawRect(box(x, top, x + 0.11, 0.84))


_menu_icon_cache: dict[str, QIcon] = {}


def menu_icon(name: str) -> QIcon:
    """Cached QIcon for a context-menu action glyph."""
    if name in _menu_icon_cache:
        return _menu_icon_cache[name]
    icon = QIcon()
    for s in (16, 24, 32):
        pm = QPixmap(s, s)
        pm.fill(Qt.GlobalColor.transparent)
        p = QPainter(pm)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setRenderHint(QPainter.RenderHint.TextAntialiasing)
        try:
            _draw_glyph(p, name, s)
        finally:
            p.end()
        icon.addPixmap(pm)
    _menu_icon_cache[name] = icon
    return icon


def export_ico(path: str) -> None:
    """Write multi-size PNG-embedded .ico for PyInstaller --icon."""
    import struct
    from PyQt6.QtCore import QBuffer, QIODevice, QByteArray

    sizes = [16, 24, 32, 48, 64, 128, 256]
    pngs = []
    for sz in sizes:
        pm = _make_pixmap(sz)
        ba = QByteArray()
        buf = QBuffer(ba)
        buf.open(QIODevice.OpenModeFlag.WriteOnly)
        pm.save(buf, "PNG")
        buf.close()
        pngs.append(bytes(ba))

    # ICONDIR + ICONDIRENTRY*n + PNG blobs
    header = struct.pack("<HHH", 0, 1, len(sizes))
    entries = b""
    offset = 6 + 16 * len(sizes)
    for sz, png in zip(sizes, pngs):
        w = 0 if sz >= 256 else sz
        h = 0 if sz >= 256 else sz
        entries += struct.pack("<BBBBHHII", w, h, 0, 0, 1, 32, len(png), offset)
        offset += len(png)

    with open(path, "wb") as f:
        f.write(header)
        f.write(entries)
        for png in pngs:
            f.write(png)
