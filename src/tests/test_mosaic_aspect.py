"""Mosaic tiles must take each photo's real shape, and never stretch it."""
import os
import time

import pytest



def _jpeg(path, w, h, orientation=1):
    from PIL import Image
    ex = Image.Exif()
    ex[0x0112] = orientation
    Image.new("RGB", (w, h), (120, 90, 60)).save(path, quality=60, exif=ex.tobytes())


@pytest.mark.parametrize("orientation", [6, 8])
def test_header_read_applies_exif_rotation(qapp, tmp_path, orientation):
    """A camera portrait is stored landscape + 'rotate 90'. PyQt6 enums don't
    convert with int(), which silently disabled the swap."""
    from picker import thumb_store
    p = tmp_path / "portrait.jpg"
    _jpeg(p, 600, 400, orientation)
    w, h, exact = thumb_store.read_dimensions(str(p))
    assert exact and (w, h) == (400, 600)


def test_undecodable_image_is_flagged_placeholder(qapp, tmp_path):
    from picker import thumb_store
    bad = tmp_path / "broken.jpg"
    bad.write_bytes(b"not a jpeg")
    img = thumb_store.decode_thumbnail(str(bad), str(tmp_path))
    assert img.text(thumb_store.PLACEHOLDER_KEY) == "1"


def _settle(app, secs=2.0):
    t = time.time()
    while time.time() - t < secs:
        app.processEvents()
        time.sleep(0.005)


def test_tiles_match_photos_on_revisit(qapp, tmp_path, settings_dir):
    """Revisit = thumbnails already in RAM, so nothing 'arrives' to correct a
    wrong cached size. Tiles must still match the photos."""
    from picker import album_browser_view as abv, theme
    theme.apply(qapp, "dark")
    lib = tmp_path / "lib"
    lib.mkdir()
    _jpeg(lib / "a_land.jpg", 600, 400)
    _jpeg(lib / "b_port_rot.jpg", 600, 400, 6)
    _jpeg(lib / "c_port.jpg", 400, 600)
    for visit in range(2):
        v = abv.AlbumBrowserView(str(lib))
        v.resize(1200, 800)
        v.show()
        _settle(qapp)
        m = v._mosaic
        for i, it in enumerate(m.items()):
            pm = m._store.get(it.path)
            assert pm is not None
            tile = m._rects[i].width() / m._rects[i].height()
            photo = pm.width() / pm.height()
            assert abs(tile - photo) / photo < 0.06, (visit, it.name, tile, photo)
        v.cleanup()
        v.deleteLater()


def test_scaled_copy_crops_never_stretches(qapp):
    """Even while a tile's shape is an estimate, the photo is cropped to fill,
    not squashed: the scaled copy keeps the source proportions."""
    from PyQt6.QtCore import QRect, Qt
    from PyQt6.QtGui import QColor, QImage, QPixmap
    from picker.album_browser_view import _ImageMosaic
    m = _ImageMosaic([], "")
    img = QImage(200, 300, QImage.Format.Format_RGB32)   # portrait
    img.fill(QColor(0, 0, 0))
    for y in range(300):                                  # red top third marker
        for x in range(200):
            if y < 100:
                img.setPixelColor(x, y, QColor(255, 0, 0))
    out = m._scaled_for("p", QPixmap.fromImage(img), QRect(0, 0, 300, 200))  # landscape tile
    assert (out.width(), out.height()) == (300, 200)
    # stretched, the red band would cover the top third of the tile (row 60);
    # cropped to fill (centre), the band is mostly cut away and row 60 is black.
    assert out.toImage().pixelColor(150, 60).red() < 60
    m.cleanup()


def test_rows_fill_the_width_edge_to_edge(qapp):
    """Every row but the last spans the full width exactly (no side slack),
    and scaling a row keeps each photo's proportions."""
    from picker.album import ImageItem
    from picker.album_browser_view import _ImageMosaic
    items = [ImageItem(path=f"p{i}.jpg", name=f"p{i}.jpg") for i in range(40)]
    m = _ImageMosaic(items, "", cached_dims={f"p{i}.jpg": [(3, 2), (2, 3), (1, 1)][i % 3]
                                              for i in range(40)})
    m.set_viewport_width(1000)
    rows = m._row_starts + [len(items)]
    for r in range(len(m._row_starts) - 1):               # all but the last row
        a, b = rows[r], rows[r + 1]
        assert m._rects[a].left() == 0
        assert m._rects[b - 1].right() == 999              # lands on the edge
        for i in range(a, b):
            want = m._aspects[i]
            got = m._rects[i].width() / m._rects[i].height()
            assert abs(got - want) / want < 0.03
    m.cleanup()


def test_scope_toggle_is_keyboard_driven(qapp):
    from PyQt6.QtCore import QEvent, Qt
    from PyQt6.QtGui import QKeyEvent
    from picker import instrument as inst
    seg = inst.Segmented([("folder", "This folder"), ("library", "Whole library")], "folder",
                         name="Search scope")
    got = []
    seg.changed.connect(got.append)
    qapp.sendEvent(seg, QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_Right, Qt.KeyboardModifier.NoModifier))
    assert seg.value() == "library" and got == ["library"]
    assert "Whole library selected" in seg.accessibleDescription()
    qapp.sendEvent(seg, QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_Space, Qt.KeyboardModifier.NoModifier))
    assert seg.value() == "folder"
