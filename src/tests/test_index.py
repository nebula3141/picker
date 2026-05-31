"""Tests for picker.index — SQLite index operations."""
import os
import sqlite3

import pytest
from picker import index


class TestConnect:
    def test_creates_db(self, index_dir):
        conn = index.connect()
        assert (index_dir / "index.sqlite").exists()
        conn.close()

    def test_schema_tables_exist(self, index_dir):
        conn = index.connect()
        tables = [r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()]
        assert "files" in tables
        conn.close()

    def test_corrupt_db_quarantined(self, index_dir):
        db_path = index_dir / "index.sqlite"
        db_path.write_bytes(b"NOT A DATABASE FILE AT ALL")
        conn = index.connect()
        assert (index_dir / "index.sqlite.corrupt").exists()
        conn.close()


class TestIntegrityCheck:
    def test_healthy_db(self, index_dir):
        index.connect().close()
        assert index.integrity_check() is None

    def test_no_db_returns_none(self, index_dir):
        assert index.integrity_check() is None


class TestScan:
    def test_scan_finds_images(self, index_dir, sample_images):
        folder, paths = sample_images
        stats = index.scan_root(folder)
        assert stats["added"] == 5
        assert stats["skipped"] == 0

    def test_rescan_skips_unchanged(self, index_dir, sample_images):
        folder, _ = sample_images
        index.scan_root(folder)
        stats = index.scan_root(folder)
        assert stats["skipped"] == 5
        assert stats["added"] == 0

    def test_files_in_folder(self, index_dir, sample_images):
        folder, _ = sample_images
        index.scan_root(folder)
        rows = index.files_in_folder(folder)
        assert len(rows) == 5

    def test_remove_root_entries(self, index_dir, sample_images):
        folder, _ = sample_images
        index.scan_root(folder)
        removed = index.remove_root_entries(folder)
        assert removed == 5
        assert index.files_in_folder(folder) == []


class TestRatingFlag:
    def test_set_rating(self, index_dir, sample_images):
        folder, paths = sample_images
        index.scan_root(folder)
        index.set_rating(paths[0], 5)
        rows = index.files_in_folder(folder)
        rated = [r for r in rows if r["rating"] == 5]
        assert len(rated) == 1

    def test_set_flag(self, index_dir, sample_images):
        folder, paths = sample_images
        index.scan_root(folder)
        index.set_flag(paths[0], "pick")
        rows = index.files_in_folder(folder)
        flagged = [r for r in rows if r["flag"] == "pick"]
        assert len(flagged) == 1

    def test_invalid_flag_becomes_none(self, index_dir, sample_images):
        folder, paths = sample_images
        index.scan_root(folder)
        index.set_flag(paths[0], "invalid")
        rows = index.files_in_folder(folder)
        assert all(r["flag"] is None for r in rows)


class TestSearch:
    def test_search_by_extension(self, index_dir, sample_images):
        folder, _ = sample_images
        index.scan_root(folder)
        results = index.search(parent=folder, exts=[".jpg"])
        assert len(results) == 5

    def test_search_empty_result(self, index_dir, sample_images):
        folder, _ = sample_images
        index.scan_root(folder)
        results = index.search(parent=folder, exts=[".png"])
        assert len(results) == 0
