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
