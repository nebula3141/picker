"""Tests for picker.updater — version parsing and comparison."""
import pytest


def test_parse_version_basic():
    from picker.updater import _parse_version
    assert _parse_version("4.6.0") == (4, 6, 0)


def test_parse_version_with_v_prefix():
    from picker.updater import _parse_version
    assert _parse_version("v4.6.0") == (4, 6, 0)


def test_parse_version_two_parts():
    from picker.updater import _parse_version
    assert _parse_version("5.0") == (5, 0)


def test_is_newer_true():
    from picker.updater import is_newer, _parse_version
    from unittest.mock import patch
    with patch("picker.__version__", "4.6.0"):
        assert is_newer("v5.0.0")


def test_is_newer_false():
    from picker.updater import is_newer
    from unittest.mock import patch
    with patch("picker.__version__", "4.6.0"):
        assert not is_newer("v4.6.0")


def test_is_newer_older():
    from picker.updater import is_newer
    from unittest.mock import patch
    with patch("picker.__version__", "4.6.0"):
        assert not is_newer("v4.5.0")


def test_should_check_respects_setting(tmp_path, monkeypatch):
    import json
    cfg = tmp_path / "cfg"
    cfg.mkdir()
    monkeypatch.setattr("picker.settings._config_dir", lambda: cfg)
    (cfg / "settings.json").write_text(json.dumps({"check_updates": False}))
    from picker.updater import should_check
    assert not should_check()
