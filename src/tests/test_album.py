"""Tests for picker.album — folder scanning and album discovery."""
import json
import os

import pytest
from PIL import Image


@pytest.fixture
def mock_settings(tmp_path, monkeypatch):
    cfg = tmp_path / "cfg"
    cfg.mkdir()
    monkeypatch.setattr("picker.settings._config_dir", lambda: cfg)
    (cfg / "settings.json").write_text(json.dumps({
        "include_subfolders": True,
        "exclude_hidden": True,
        "file_types": ["jpeg", "png"],
        "include_videos": False,
    }))


def _make_img(path):
    img = Image.new("RGB", (10, 10), color=(100, 100, 100))
    img.save(str(path), "JPEG")


@pytest.fixture
def tree(tmp_path, mock_settings):
    """Create folder tree:
    root/
      a.jpg
      sub1/
        b.jpg
        c.jpg
      sub2/
        deep/
          d.jpg
      .hidden/
        e.jpg
      empty/
    """
    root = tmp_path / "root"
    root.mkdir()
    _make_img(root / "a.jpg")
    sub1 = root / "sub1"
    sub1.mkdir()
    _make_img(sub1 / "b.jpg")
    _make_img(sub1 / "c.jpg")
    sub2 = root / "sub2"
    sub2.mkdir()
    deep = sub2 / "deep"
    deep.mkdir()
    _make_img(deep / "d.jpg")
    hidden = root / ".hidden"
    hidden.mkdir()
    _make_img(hidden / "e.jpg")
    empty = root / "empty"
    empty.mkdir()
    return root


def test_scan_path_folders(tree):
    from picker.album import scan_path
    exts = {".jpg", ".jpeg", ".png"}
    folders, images = scan_path(str(tree), extensions=exts, exclude_hidden=True)
    folder_names = {f.name for f in folders}
    assert "sub1" in folder_names
    assert "sub2" in folder_names
    assert ".hidden" not in folder_names
    assert "empty" not in folder_names


def test_scan_path_images(tree):
    from picker.album import scan_path
    exts = {".jpg", ".jpeg", ".png"}
    folders, images = scan_path(str(tree), extensions=exts, exclude_hidden=True)
    assert len(images) == 1
    assert images[0].name == "a.jpg"


def test_scan_path_folder_count(tree):
    from picker.album import scan_path
    exts = {".jpg", ".jpeg", ".png"}
    folders, _ = scan_path(str(tree), extensions=exts, exclude_hidden=True)
    sub1 = next(f for f in folders if f.name == "sub1")
    assert sub1.image_count == 2
    sub2 = next(f for f in folders if f.name == "sub2")
    assert sub2.image_count == 1


def test_scan_path_cover(tree):
    from picker.album import scan_path
    exts = {".jpg", ".jpeg", ".png"}
    folders, _ = scan_path(str(tree), extensions=exts, exclude_hidden=True)
    sub1 = next(f for f in folders if f.name == "sub1")
    assert sub1.cover_path.endswith("b.jpg")


def test_scan_path_hidden_included(tree):
    from picker.album import scan_path
    exts = {".jpg", ".jpeg", ".png"}
    folders, _ = scan_path(str(tree), extensions=exts, exclude_hidden=False)
    folder_names = {f.name for f in folders}
    assert ".hidden" in folder_names


def test_scan_path_empty_dir_excluded(tree):
    from picker.album import scan_path
    exts = {".jpg", ".jpeg", ".png"}
    folders, _ = scan_path(str(tree), extensions=exts, exclude_hidden=True)
    assert not any(f.name == "empty" for f in folders)


def test_scan_path_nonexistent(tmp_path):
    from picker.album import scan_path
    folders, images = scan_path(str(tmp_path / "nope"), extensions={".jpg"})
    assert folders == []
    assert images == []


def test_scan_albums(tree):
    from picker.album import scan_albums
    exts = {".jpg", ".jpeg", ".png"}
    albums = scan_albums(str(tree), extensions=exts, exclude_hidden=True)
    assert len(albums) >= 3
    root_album = albums[0]
    assert root_album.rel == ""
    assert root_album.image_count == 1


def test_scan_albums_sorts_root_first(tree):
    from picker.album import scan_albums
    exts = {".jpg", ".jpeg", ".png"}
    albums = scan_albums(str(tree), extensions=exts, exclude_hidden=True)
    assert albums[0].rel == ""


def test_progress_callback(tree):
    from picker.album import scan_path
    exts = {".jpg", ".jpeg", ".png"}
    calls = []
    scan_path(str(tree), extensions=exts, exclude_hidden=True,
              progress_cb=lambda p: calls.append(p))
    assert len(calls) > 0
