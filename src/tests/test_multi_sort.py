"""Multi Sort: gallery painting, per-file undo, live destinations panel."""
import os
import sys
import time

import pytest


def _jpegs(folder, n, size=(60, 40)):
    from PIL import Image
    folder.mkdir(parents=True, exist_ok=True)
    out = []
    for i in range(n):
        p = folder / f"img_{i:03d}.jpg"
        Image.new("RGB", size, (40 + i * 20, 90, 120)).save(p, quality=60)
        out.append(str(p))
    return out


def _pump(app, secs=0.3):
    t = time.time()
    while time.time() - t < secs:
        app.processEvents()
        time.sleep(0.005)


@pytest.fixture
def manager(tmp_path, settings_dir):
    from picker.image_manager import ImageManager
    src = tmp_path / "src"
    _jpegs(src, 4)
    dests = [{"name": "Best", "path": str(tmp_path / "best")},
             {"name": "Maybe", "path": str(tmp_path / "maybe")}]
    return ImageManager(source_folder=str(src), destinations=dests, mode="copy",
                        resolution_pct=50)


def test_gallery_paints_thumbnails_without_error(qapp, manager, monkeypatch):
    """Regression: drawPixmap got a QRect target with a QRectF source and threw
    on every paint of the Sort-Multi gallery."""
    from PyQt6.QtGui import QColor, QPixmap
    from picker import thumb_store
    from picker.gallery_view import GalleryView
    errors = []
    monkeypatch.setattr(sys, "excepthook", lambda *a: errors.append(a))
    g = GalleryView(manager)
    g.resize(900, 700)
    g.show()
    pm = QPixmap(90, 60)
    pm.fill(QColor(200, 50, 50))
    for rec in manager.images:
        thumb_store.store().put(rec.path, pm)
    _pump(qapp)
    g.grab()
    assert not errors, errors
    g.cleanup()


def test_undo_one_specific_send(manager):
    events = []
    manager.add_listener(lambda *e: events.append(e[:2]))
    assert manager.send_to(0, 0) is None
    assert manager.send_to(1, 0) is None
    assert manager.send_to(2, 1) is None
    first = os.path.join(manager.destinations[0]["path"], "img_000.jpg")
    assert os.path.isfile(first)
    # not the latest send — the panel can undo any of this session's sends
    assert manager.undo_send(first) is None
    assert not os.path.exists(first)
    assert manager.images[0].status == "unreviewed"
    assert manager.images[1].status == "dest_0"          # others untouched
    assert ("undo", 0) in events and events.count(("send", 0)) == 2


def test_undo_never_touches_files_that_were_already_there(manager):
    pre = os.path.join(manager.destinations[0]["path"], "older.jpg")
    os.makedirs(os.path.dirname(pre), exist_ok=True)
    open(pre, "wb").write(b"x")
    assert manager.undo_send(pre) is not None             # refused
    assert os.path.exists(pre)


def test_panel_fills_live_and_undo_removes(qapp, manager):
    from picker.dest_panel import DestinationsPanel
    panel = DestinationsPanel(manager)
    panel.resize(300, 600)
    panel.show()
    _pump(qapp, 0.5)                                      # background listing
    manager.send_to(3, 1)
    sec = panel._sections[1]
    landed = os.path.join(manager.destinations[1]["path"], "img_003.jpg")
    assert sec.has(landed) and sec.undoable(landed)       # same tick, no wait
    assert sec._count.text() == "1"
    manager.undo_send(landed)
    assert not sec.has(landed) and sec._count.text() == "0"
    panel.cleanup()


@pytest.fixture
def move_manager(tmp_path, settings_dir):
    from picker.image_manager import ImageManager
    src = tmp_path / "src"
    _jpegs(src, 5)
    dests = [{"name": "Best", "path": str(tmp_path / "best"), "key": "1"},
             {"name": "Maybe", "path": str(tmp_path / "maybe"), "key": "2"},
             {"name": "Later", "path": str(tmp_path / "later"), "key": "Q"}]
    return ImageManager(source_folder=str(src), destinations=dests, mode="move",
                        resolution_pct=50)


def _key(app, w, key, ctrl=False):
    from PyQt6.QtCore import QEvent, Qt
    from PyQt6.QtGui import QKeyEvent
    mods = Qt.KeyboardModifier.ControlModifier if ctrl else Qt.KeyboardModifier.NoModifier
    app.sendEvent(w, QKeyEvent(QEvent.Type.KeyPress, key, mods))


def test_plain_keys_move_photos_and_ctrl_digits_rate(qapp, move_manager, monkeypatch):
    """The reported bug: in Multi Sort, 1 rated the photo instead of sorting it,
    so nothing ever moved. Plain keys sort; Ctrl+1-5 rates."""
    from PyQt6.QtCore import Qt
    from picker import settings
    from picker.slideshow_view import SlideshowView
    settings.set_value("auto_advance_on_send", False)
    m = move_manager
    v = SlideshowView(m, 0)
    v.resize(1200, 800)
    v.show()
    _pump(qapp, 0.4)
    first = m.images[0].path
    _key(qapp, v, Qt.Key.Key_1)
    landed = os.path.join(m.destinations[0]["path"], "img_000.jpg")
    assert os.path.isfile(landed) and not os.path.exists(first)      # really moved
    assert m.images[0].status == "dest_0"

    v._jump_to(1)
    _pump(qapp, 0.2)
    _key(qapp, v, Qt.Key.Key_Q)                                     # a custom key
    assert os.path.isfile(os.path.join(m.destinations[2]["path"], "img_001.jpg"))

    v._jump_to(2)
    _pump(qapp, 0.2)
    _key(qapp, v, Qt.Key.Key_2, ctrl=True)                          # rating, not sorting
    assert m.images[2].status == "unreviewed"
    assert os.path.isfile(m.images[2].path)
    from picker import ratings
    assert ratings.get(m.source_folder, m.images[2].path) == 2
    v.cleanup()


def test_dialog_keys_default_and_swap(qapp, settings_dir):
    from picker.startup_dialog import StartupDialog
    d = StartupDialog()
    d._add_dest_row("B")
    d._add_dest_row("C")
    d._add_dest_row("D")
    assert [r.key() for r in d.dest_rows] == ["1", "2", "3", "4"]  # defaults 1,2,3,4
    rows = d.dest_rows
    rows[0].key_combo.setCurrentIndex(rows[0].key_combo.findData("3"))
    assert rows[0].key() == "3" and rows[2].key() == "1"             # swapped, no duplicates
    rows[0].path_edit.setText("C:/x")
    assert rows[0].get_dest()["key"] == "3"


SVG = ('<svg xmlns="http://www.w3.org/2000/svg" width="120" height="80">'
       '<rect width="120" height="80" fill="#3b82f6"/><circle cx="60" cy="40" r="25" fill="#fff"/></svg>')


def test_svg_folder_lists_and_thumbnails(qapp, tmp_path, settings_dir):
    from picker import album, thumb_store
    folder = tmp_path / "vectors"
    folder.mkdir()
    for i in range(3):
        (folder / f"logo_{i}.svg").write_text(SVG, encoding="utf-8")
    _folders, items = album.scan_path(str(folder), deep=False)
    assert len(items) == 3                                     # not "no photos"
    img = thumb_store.decode_thumbnail(items[0].path, str(folder))
    assert img.text(thumb_store.PLACEHOLDER_KEY) != "1"       # really rendered
    assert abs(img.width() / img.height() - 1.5) < 0.02


def test_sort_window_always_opens_on_copy(qapp, settings_dir):
    from picker.startup_dialog import StartupDialog
    d = StartupDialog()
    assert d._mode.value() == "copy"
    d._mode.set_value("move", emit=True)          # choose Move for one sort…
    d2 = StartupDialog()
    assert d2._mode.value() == "copy"             # …and the next opens on Copy again


def test_destination_photo_opens_and_steps(qapp, manager):
    from PyQt6.QtCore import Qt
    from picker.slideshow_view import SlideshowView
    from picker import settings
    settings.set_value("auto_advance_on_send", False)
    v = SlideshowView(manager, 0)
    v.resize(1200, 800)
    v.show()
    _pump(qapp, 0.3)
    for i in range(3):                             # three photos into "Best"
        v._jump_to(i)
        _pump(qapp, 0.1)
        v._send_to(0)
    _pump(qapp, 0.3)
    sec = v._dest_panel.section(0)
    newest = sec.paths()[0]
    sec._wall.open_clicked.emit(newest)            # click the photo in the list
    pv = v._dest_preview
    assert pv.isVisible() and pv._current() == newest and "1 of 3" in pv._caption.text()
    _key(qapp, pv, Qt.Key.Key_Right)
    assert "2 of 3" in pv._caption.text()
    _key(qapp, pv, Qt.Key.Key_Left)
    assert "1 of 3" in pv._caption.text()
    pv._send_back()                                # undo from the preview
    assert not os.path.exists(newest) and "of 2" in pv._caption.text()
    _key(qapp, pv, Qt.Key.Key_Escape)
    assert not pv.isVisible()
    v.cleanup()
