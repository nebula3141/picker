"""Tests for picker.settings — load, save, migration, corruption recovery."""
import json
import os

import pytest
from picker import settings as settings_mod


class TestLoadSave:
    def test_load_defaults(self, settings_dir):
        data = settings_mod.load()
        assert data["default_mode"] == "copy"
        assert data["theme"] == "dark"
        assert data["settings_version"] == settings_mod.SETTINGS_VERSION

    def test_save_and_reload(self, settings_dir):
        settings_mod.save({"default_mode": "move", "theme": "light"})
        data = settings_mod.load()
        assert data["default_mode"] == "move"
        assert data["theme"] == "light"

    def test_unknown_keys_dropped(self, settings_dir):
        settings_mod.save({"default_mode": "copy", "bogus_key": 42})
        data = settings_mod.load()
        assert "bogus_key" not in data

    def test_get_and_set_value(self, settings_dir):
        settings_mod.set_value("theme", "light")
        assert settings_mod.get("theme") == "light"

    def test_atomic_write_survives_crash(self, settings_dir):
        settings_mod.save({"default_mode": "move"})
        tmp = (settings_dir / "settings.json.tmp")
        if tmp.exists():
            tmp.unlink()
        data = settings_mod.load()
        assert data["default_mode"] == "move"


class TestMigration:
    def test_v0_to_current(self, settings_dir):
        old = {"default_mode": "copy", "theme": "dark"}
        (settings_dir / "settings.json").write_text(json.dumps(old), encoding="utf-8")
        data = settings_mod.load()
        assert data["settings_version"] == settings_mod.SETTINGS_VERSION
        assert data["slideshow_animation"] is True
        assert data["check_updates"] is True

    def test_backup_created_on_migration(self, settings_dir):
        old = {"default_mode": "copy"}
        (settings_dir / "settings.json").write_text(json.dumps(old), encoding="utf-8")
        settings_mod.load()
        assert (settings_dir / "settings.json.bak").exists()

    def test_current_version_no_migration(self, settings_dir):
        current = {"settings_version": settings_mod.SETTINGS_VERSION, "default_mode": "move"}
        (settings_dir / "settings.json").write_text(json.dumps(current), encoding="utf-8")
        data = settings_mod.load()
        assert data["default_mode"] == "move"
        assert not (settings_dir / "settings.json.bak").exists()


class TestCorruptionRecovery:
    def test_corrupt_json_quarantined(self, settings_dir):
        (settings_dir / "settings.json").write_text("{invalid json!!!", encoding="utf-8")
        data = settings_mod.load()
        assert data["default_mode"] == "copy"
        assert (settings_dir / "settings.json.corrupt").exists()
        assert not (settings_dir / "settings.json").exists()

    def test_empty_file_returns_defaults(self, settings_dir):
        (settings_dir / "settings.json").write_text("", encoding="utf-8")
        data = settings_mod.load()
        assert data["default_mode"] == "copy"


class TestPositions:
    def test_save_and_get_position(self, settings_dir):
        settings_mod.save_position("/some/folder", 42)
        assert settings_mod.get_position("/some/folder") == 42

    def test_missing_folder_returns_zero(self, settings_dir):
        assert settings_mod.get_position("/nonexistent") == 0

    def test_lru_eviction(self, settings_dir):
        for i in range(250):
            settings_mod.save_position(f"/folder/{i}", i)
        data = settings_mod.load_positions()
        assert len(data) <= settings_mod._POS_CACHE_MAX
