"""Tests for the loading-speed work: syscall-free dimension lookups, memoised
recursive folder stats, O(n) seeded open, lazy position writes, one-time index
setup, and the RAM thumbnail store."""
import json
import os

import pytest
from PIL import Image


# ── dim_cache batch lookups ────────────────────────────────────────────────────

@pytest.fixture
def clean_dims():
    from picker import dim_cache
    dim_cache.reset()
    yield dim_cache
    dim_cache.reset()


def test_get_many_needs_no_stat(tmp_path, clean_dims):
    dc = clean_dims
    f = tmp_path / "a.jpg"
    f.write_bytes(b"x" * 10)
    st = os.stat(f)
    dc.put_sig(str(tmp_path), str(f), 300, 200, (int(st.st_mtime), st.st_size))
    hits = dc.get_many(str(tmp_path), [(str(f), st.st_mtime, st.st_size),
                                       (str(f), st.st_mtime + 9, st.st_size),   # changed
                                       (str(tmp_path / "gone.jpg"), None, 0)])
    assert hits == [(300, 200), None, None]


def test_preload_then_flush_roundtrip(tmp_path, clean_dims):
    dc = clean_dims
    f = tmp_path / "a.jpg"
    f.write_bytes(b"x")
    dc.put(str(tmp_path), str(f), 10, 20)
    dc.flush(str(tmp_path))
    dc.reset()
    dc.preload(str(tmp_path))
    st = os.stat(f)
    assert dc.get_many(str(tmp_path), [(str(f), st.st_mtime, st.st_size)]) == [(10, 20)]


# ── recursive folder stats ─────────────────────────────────────────────────────

def _jpg(p):
    Image.new("RGB", (4, 4)).save(str(p), "JPEG")


def test_one_walk_memoises_every_subfolder(tmp_path):
    from picker import album
    root = tmp_path / "r"
    (root / "a" / "b").mkdir(parents=True)
    (root / "empty").mkdir()
    _jpg(root / "a" / "1.jpg")
    _jpg(root / "a" / "b" / "2.jpg")
    _jpg(root / "a" / "b" / "3.jpg")
    exts = {".jpg"}
    count, cover = album.folder_stat(str(root), exts, True)
    assert count == 3 and cover.endswith("1.jpg")
    # Descendants were recorded by the same walk — no second walk needed.
    assert album.cached_folder_stat(str(root / "a" / "b"))[0] == 2
    assert album.cached_folder_stat(str(root / "empty"))[0] == 0
    # Invalidating a folder drops it and its ancestors, not its siblings' subtrees.
    album.invalidate_folder_stats(str(root / "a" / "b"))
    assert album.cached_folder_stat(str(root / "a" / "b")) is None
    assert album.cached_folder_stat(str(root)) is None
    assert album.cached_folder_stat(str(root / "empty")) is not None


def test_scan_path_carries_stat(tmp_path):
    from picker.album import scan_path
    _jpg(tmp_path / "x.jpg")
    _, images = scan_path(str(tmp_path), extensions={".jpg"}, exclude_hidden=True, deep=False)
    st = os.stat(tmp_path / "x.jpg")
    assert images[0].size == st.st_size and int(images[0].mtime) == int(st.st_mtime)
    assert images[0].sig == (int(st.st_mtime), st.st_size)


# ── seeded open ────────────────────────────────────────────────────────────────

def test_create_seeded_is_complete_and_positioned(tmp_path, settings_dir):
    from picker.image_manager import ImageManager
    for n in ("b.jpg", "A.jpg", "c.jpg", "notes.txt"):
        (tmp_path / n).write_bytes(b"x")
    mgr, idx = ImageManager.create_seeded(str(tmp_path), str(tmp_path / "c.jpg"))
    assert mgr.scan_complete
    assert [r.filename for r in mgr.images] == ["A.jpg", "b.jpg", "c.jpg"]   # case-insensitive
    assert idx == 2
    done = []
    mgr._on_scan_complete = lambda: done.append(True)
    mgr.start_background_scan()
    assert done == [True]


def test_create_seeded_reuses_supplied_listing(tmp_path, settings_dir):
    from picker.image_manager import ImageManager
    mgr, idx = ImageManager.create_seeded(str(tmp_path), str(tmp_path / "y.jpg"),
                                          names=["z.jpg", "y.jpg"])
    assert [r.filename for r in mgr.images] == ["y.jpg", "z.jpg"] and idx == 0


# ── positions: memory first, disk later ────────────────────────────────────────

def test_save_position_is_lazy_then_flushes(settings_dir):
    from picker import settings as s
    s.clear_positions()
    s.save_position("/p/one", 7)
    assert s.get_position("/p/one") == 7            # visible immediately
    s.flush_positions()
    data = json.loads((settings_dir / "positions.json").read_text(encoding="utf-8"))
    assert 7 in data.values()
    s.clear_positions()
    assert s.get_position("/p/one") == 0
    assert not (settings_dir / "positions.json").exists()


# ── index: backup/schema once per process ──────────────────────────────────────

def test_connect_backs_up_only_once(index_dir):
    from picker import index
    index._schema_ready.clear()
    index.connect().close()
    bak = index_dir / "index.sqlite.bak"
    if bak.exists():
        bak.unlink()
    index.connect().close()
    assert not bak.exists()


# ── RAM thumbnail store ────────────────────────────────────────────────────────

@pytest.fixture(scope="module")
def qapp():
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PyQt6.QtWidgets import QApplication
    return QApplication.instance() or QApplication([])


def test_store_lru_budget_and_signature(qapp):
    from PyQt6.QtGui import QPixmap
    from picker.thumb_store import ThumbStore
    st = ThumbStore()
    st._budget = 3 * 100 * 100 * 4 + 1            # room for three 100x100 ARGB pixmaps
    st.FLOOR = st.CEILING = st._budget
    for i in range(4):
        pm = QPixmap(100, 100)
        pm.fill()
        st.put(f"/f/{i}.jpg", pm, (i, 10))
    assert st.get("/f/0.jpg") is None               # evicted, oldest first
    assert st.get("/f/3.jpg", (3, 10)) is not None
    assert st.get("/f/3.jpg", (99, 10)) is None     # file changed → dropped
    assert not st.contains("/f/3.jpg")
    st.alias("/f/2.jpg", "/dst/2.jpg")
    assert st.get("/dst/2.jpg") is not None
