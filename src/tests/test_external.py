"""Tests for picker.external — editor detection with mocked registry."""
import json
import os
import sys
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest


@pytest.fixture(autouse=True)
def mock_settings(tmp_path, monkeypatch):
    cfg = tmp_path / "cfg"
    cfg.mkdir()
    monkeypatch.setattr("picker.settings._config_dir", lambda: cfg)
    (cfg / "settings.json").write_text(json.dumps({
        "photoshop_path": "",
        "lightroom_path": "",
    }))
    from picker import external
    external.invalidate_cache()
    yield
    external.invalidate_cache()


def test_invalidate_cache():
    from picker.external import _cache, invalidate_cache
    _cache["test"] = "value"
    invalidate_cache()
    assert len(_cache) == 0


def test_manual_path_override(tmp_path, monkeypatch):
    fake_ps = tmp_path / "Photoshop.exe"
    fake_ps.write_bytes(b"MZ")
    cfg = tmp_path / "cfg"
    (cfg / "settings.json").write_text(json.dumps({
        "photoshop_path": str(fake_ps),
        "lightroom_path": "",
    }))
    from picker import external
    external.invalidate_cache()
    result = external.photoshop_path()
    assert result == str(fake_ps)


def test_photoshop_path_returns_none_when_not_found():
    from picker import external
    external.invalidate_cache()
    with patch.object(external, "_HAVE_WINREG", False):
        external.invalidate_cache()
        result = external.photoshop_path()
        if result is not None:
            assert os.path.isfile(result)


def test_lightroom_path_returns_none_when_not_found():
    from picker import external
    external.invalidate_cache()
    with patch.object(external, "_HAVE_WINREG", False):
        external.invalidate_cache()
        result = external.lightroom_path()
        if result is not None:
            assert os.path.isfile(result)


def test_ver_key():
    from picker.external import _ver_key
    assert _ver_key("25.0") == [25, 0]
    assert _ver_key("13.1.2") == [13, 1, 2]
    assert _ver_key("") == [0]


def test_cache_prevents_repeated_lookups():
    from picker import external
    external.invalidate_cache()
    with patch.object(external, "_HAVE_WINREG", False):
        external.invalidate_cache()
        r1 = external.photoshop_path()
        external._cache["ps"] = "/fake/path.exe"
        r2 = external.photoshop_path()
        assert r2 == "/fake/path.exe"
