"""Tests for picker.library — roots, pinned, recents."""
import json
import os
from pathlib import Path

import pytest
from picker import library


@pytest.fixture(autouse=True)
def lib_dir(tmp_path, monkeypatch):
    d = tmp_path / "PICker"
    d.mkdir()
    monkeypatch.setattr("picker.library._config_dir", lambda: d)
    return d


@pytest.fixture
def real_folder(tmp_path):
    d = tmp_path / "photos"
    d.mkdir()
    return str(d)


class TestRoots:
    def test_add_root(self, real_folder):
        library.add_root(real_folder)
        assert real_folder in library.root_paths()

    def test_add_duplicate_ignored(self, real_folder):
        library.add_root(real_folder)
        library.add_root(real_folder)
        assert len(library.root_paths()) == 1

    def test_remove_root(self, real_folder):
        library.add_root(real_folder)
        library.remove_root(real_folder)
        assert real_folder not in library.root_paths()

    def test_rename_root(self, real_folder):
        library.add_root(real_folder)
        library.rename_root(real_folder, "My Photos")
        root = library.get_root(real_folder)
        assert root["label"] == "My Photos"

    def test_nonexistent_path_rejected(self):
        library.add_root("/nonexistent/path")
        assert len(library.root_paths()) == 0


class TestPinned:
    def test_toggle_pin(self, real_folder):
        library.add_root(real_folder)
        assert library.toggle_pin(real_folder) is True
        assert library.is_pinned(real_folder)
        assert library.toggle_pin(real_folder) is False
        assert not library.is_pinned(real_folder)


class TestRecents:
    def test_push_recent(self, real_folder):
        library.push_recent(real_folder)
        assert real_folder in library.recents()

    def test_max_recents(self, tmp_path):
        folders = []
        for i in range(15):
            d = tmp_path / f"folder_{i}"
            d.mkdir()
            folders.append(str(d))
            library.push_recent(str(d))
        assert len(library.recents()) <= library.MAX_RECENTS

    def test_clear_recents(self, real_folder):
        library.push_recent(real_folder)
        library.clear_recents()
        assert library.recents() == []


class TestLegacyMigration:
    def test_string_roots_migrated(self, lib_dir, real_folder):
        data = {"roots": [real_folder], "pinned": [], "recents": []}
        (lib_dir / "library.json").write_text(json.dumps(data), encoding="utf-8")
        loaded = library.load()
        assert loaded["roots"][0]["path"] == real_folder
        assert loaded["roots"][0]["label"] is None


class TestStat:
    def test_compute_stat(self, tmp_path):
        d = tmp_path / "test_folder"
        d.mkdir()
        for i in range(3):
            (d / f"file_{i}.txt").write_text("hello")
        stat = library.compute_stat(str(d))
        assert stat["count"] == 3

    def test_stat_differs(self):
        a = {"count": 10, "size": 1000, "mtime": 100.0}
        b = {"count": 10, "size": 1000, "mtime": 100.0}
        assert not library.stat_differs(a, b)
        b["count"] = 11
        assert library.stat_differs(a, b)
