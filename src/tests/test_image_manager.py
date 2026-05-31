"""Tests for picker.image_manager — scan, filter, send, undo."""
import os
import json
from pathlib import Path

import pytest
from PIL import Image


@pytest.fixture
def img_folder(tmp_path, monkeypatch):
    """Create a folder with mixed image files and mock settings."""
    d = tmp_path / "photos"
    d.mkdir()
    for name in ("a.jpg", "b.png", "c.bmp"):
        img = Image.new("RGB", (10, 10), color=(100, 100, 100))
        fmt = {"jpg": "JPEG", "png": "PNG", "bmp": "BMP"}[name.rsplit(".", 1)[1]]
        img.save(str(d / name), fmt)
    (d / "skip.txt").write_text("not an image")

    cfg = tmp_path / "cfg"
    cfg.mkdir()
    monkeypatch.setattr("picker.settings._config_dir", lambda: cfg)
    settings_file = cfg / "settings.json"
    settings_file.write_text(json.dumps({
        "include_subfolders": True,
        "exclude_hidden": True,
        "file_types": ["jpeg", "png", "bmp"],
        "include_videos": False,
    }))
    return d


@pytest.fixture
def dest_folder(tmp_path):
    d = tmp_path / "dest"
    d.mkdir()
    return d


def test_scan_finds_images(img_folder):
    from picker.image_manager import ImageManager
    mgr = ImageManager(str(img_folder), [], "copy", 25)
    assert len(mgr.images) == 3
    names = {r.filename for r in mgr.images}
    assert "a.jpg" in names
    assert "b.png" in names
    assert "c.bmp" in names


def test_scan_skips_non_image(img_folder):
    from picker.image_manager import ImageManager
    mgr = ImageManager(str(img_folder), [], "copy", 25)
    names = {r.filename for r in mgr.images}
    assert "skip.txt" not in names


def test_scan_excludes_hidden(img_folder, monkeypatch):
    hidden = img_folder / ".hidden"
    hidden.mkdir()
    img = Image.new("RGB", (10, 10))
    img.save(str(hidden / "secret.jpg"), "JPEG")
    from picker.image_manager import ImageManager
    mgr = ImageManager(str(img_folder), [], "copy", 25)
    paths = [r.path for r in mgr.images]
    assert not any(".hidden" in p for p in paths)


def test_scan_subfolders(img_folder, monkeypatch):
    sub = img_folder / "sub"
    sub.mkdir()
    img = Image.new("RGB", (10, 10))
    img.save(str(sub / "deep.jpg"), "JPEG")
    from picker.image_manager import ImageManager
    mgr = ImageManager(str(img_folder), [], "copy", 25)
    assert len(mgr.images) == 4


def test_scan_no_subfolders(img_folder, monkeypatch):
    sub = img_folder / "sub"
    sub.mkdir()
    img = Image.new("RGB", (10, 10))
    img.save(str(sub / "deep.jpg"), "JPEG")
    from picker.image_manager import ImageManager
    mgr = ImageManager(str(img_folder), [], "copy", 25, include_subfolders=False)
    assert len(mgr.images) == 3


def test_send_copy(img_folder, dest_folder):
    from picker.image_manager import ImageManager
    dests = [{"name": "Keep", "path": str(dest_folder)}]
    mgr = ImageManager(str(img_folder), dests, "copy", 25)
    err = mgr.send_to(0, 0)
    assert err is None
    assert mgr.images[0].status == "dest_0"
    assert os.path.exists(mgr.images[0].path)
    dest_files = list(dest_folder.iterdir())
    assert len(dest_files) == 1


def test_send_move(img_folder, dest_folder):
    from picker.image_manager import ImageManager
    dests = [{"name": "Keep", "path": str(dest_folder)}]
    mgr = ImageManager(str(img_folder), dests, "move", 25)
    original_path = mgr.images[0].path
    err = mgr.send_to(0, 0)
    assert err is None
    assert not os.path.exists(original_path)


def test_undo_copy(img_folder, dest_folder):
    from picker.image_manager import ImageManager
    dests = [{"name": "Keep", "path": str(dest_folder)}]
    mgr = ImageManager(str(img_folder), dests, "copy", 25)
    mgr.send_to(0, 0)
    assert mgr.images[0].status == "dest_0"
    err = mgr.undo()
    assert err is None
    assert mgr.images[0].status == "unreviewed"
    assert len(list(dest_folder.iterdir())) == 0


def test_undo_move(img_folder, dest_folder):
    from picker.image_manager import ImageManager
    dests = [{"name": "Keep", "path": str(dest_folder)}]
    mgr = ImageManager(str(img_folder), dests, "move", 25)
    original_path = mgr.images[0].path
    mgr.send_to(0, 0)
    err = mgr.undo()
    assert err is None
    assert os.path.exists(original_path)


def test_undo_empty(img_folder):
    from picker.image_manager import ImageManager
    mgr = ImageManager(str(img_folder), [], "copy", 25)
    err = mgr.undo()
    assert err is not None


def test_conflict_rename(img_folder, dest_folder):
    from picker.image_manager import ImageManager
    dests = [{"name": "Keep", "path": str(dest_folder)}]
    mgr = ImageManager(str(img_folder), dests, "copy", 25)
    mgr.send_to(0, 0)
    mgr.images[0].status = "unreviewed"
    mgr.send_to(0, 0)
    files = list(dest_folder.iterdir())
    assert len(files) == 2


def test_stats(img_folder, dest_folder):
    from picker.image_manager import ImageManager
    dests = [{"name": "Keep", "path": str(dest_folder)}]
    mgr = ImageManager(str(img_folder), dests, "copy", 25)
    s = mgr.stats()
    assert s["total"] == 3
    assert s["unreviewed"] == 3
    mgr.send_to(0, 0)
    s = mgr.stats()
    assert s["unreviewed"] == 2


def test_has_destinations(img_folder, dest_folder):
    from picker.image_manager import ImageManager
    mgr = ImageManager(str(img_folder), [], "copy", 25)
    assert not mgr.has_destinations
    mgr2 = ImageManager(str(img_folder), [{"name": "x", "path": str(dest_folder)}], "copy", 25)
    assert mgr2.has_destinations
