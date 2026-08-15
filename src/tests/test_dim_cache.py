"""Tests for picker.dim_cache — the persistent image-dimension cache that lets
the justified views lay out correctly on first paint without a header pass."""
import os

import pytest

from picker import dim_cache


@pytest.fixture(autouse=True)
def _clear_state():
    """Each test starts with empty in-memory state."""
    dim_cache._folders.clear()
    dim_cache._loaded.clear()
    dim_cache._dirty.clear()
    yield
    dim_cache._folders.clear()
    dim_cache._loaded.clear()
    dim_cache._dirty.clear()


def _make_file(path, size=100):
    with open(path, "wb") as f:
        f.write(b"x" * size)


def test_cold_miss(tmp_path):
    f = tmp_path / "a.jpg"; _make_file(f)
    assert dim_cache.get(str(tmp_path), str(f)) is None


def test_put_then_get_in_memory(tmp_path):
    f = tmp_path / "a.jpg"; _make_file(f)
    dim_cache.put(str(tmp_path), str(f), 4000, 3000)
    assert dim_cache.get(str(tmp_path), str(f)) == (4000, 3000)


def test_persists_across_sessions(tmp_path):
    f = tmp_path / "a.jpg"; _make_file(f)
    dim_cache.put(str(tmp_path), str(f), 1920, 1080)
    dim_cache.flush(str(tmp_path))
    # Simulate a fresh process: drop all in-memory state, read from disk.
    dim_cache._folders.clear(); dim_cache._loaded.clear(); dim_cache._dirty.clear()
    assert dim_cache.get(str(tmp_path), str(f)) == (1920, 1080)
    # The index file lives in .picker_cache next to the thumbnails.
    assert (tmp_path / ".picker_cache" / "dimensions.json").is_file()


def test_invalidates_on_content_change(tmp_path):
    f = tmp_path / "a.jpg"; _make_file(f, size=100)
    dim_cache.put(str(tmp_path), str(f), 800, 600)
    assert dim_cache.get(str(tmp_path), str(f)) == (800, 600)
    # Size/mtime change (a re-saved or replaced file) must miss, not return stale.
    _make_file(f, size=250)
    os.utime(f, (os.path.getmtime(f) + 5, os.path.getmtime(f) + 5))
    assert dim_cache.get(str(tmp_path), str(f)) is None


def test_missing_file_is_safe(tmp_path):
    ghost = tmp_path / "gone.jpg"
    assert dim_cache.get(str(tmp_path), str(ghost)) is None
    dim_cache.put(str(tmp_path), str(ghost), 100, 100)   # no crash, no entry
    assert dim_cache.get(str(tmp_path), str(ghost)) is None


def test_rejects_bad_dimensions(tmp_path):
    f = tmp_path / "a.jpg"; _make_file(f)
    dim_cache.put(str(tmp_path), str(f), 0, 500)
    dim_cache.put(str(tmp_path), str(f), -1, -1)
    assert dim_cache.get(str(tmp_path), str(f)) is None


def test_flush_all_dirty_folders(tmp_path):
    a = tmp_path / "A"; a.mkdir(); fa = a / "x.jpg"; _make_file(fa)
    b = tmp_path / "B"; b.mkdir(); fb = b / "y.jpg"; _make_file(fb)
    dim_cache.put(str(a), str(fa), 10, 20)
    dim_cache.put(str(b), str(fb), 30, 40)
    dim_cache.flush()   # no arg → every dirty folder
    assert (a / ".picker_cache" / "dimensions.json").is_file()
    assert (b / ".picker_cache" / "dimensions.json").is_file()
