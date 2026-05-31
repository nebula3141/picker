"""Tests for picker.log — file logging and rotation."""
import os
from pathlib import Path

import pytest
from picker import log


@pytest.fixture(autouse=True)
def log_dir(tmp_path, monkeypatch):
    d = tmp_path / "PICker" / "logs"
    d.mkdir(parents=True)
    monkeypatch.setattr("picker.log._log_dir", lambda: d)
    monkeypatch.setattr("picker.log._log_file", lambda: d / "picker.log")
    # Reset module state so each test gets a fresh file handle.
    log._log_init_done = False
    log._log_fh = None
    yield d
    try:
        if log._log_fh:
            log._log_fh.close()
    except Exception:
        pass
    log._log_init_done = False
    log._log_fh = None


class TestFileLogging:
    def test_writes_to_file(self, log_dir):
        log._emit("INFO", "test message", key="value")
        if log._log_fh:
            log._log_fh.flush()
        content = (log_dir / "picker.log").read_text(encoding="utf-8")
        assert "test message" in content
        assert "key=" in content

    def test_no_ansi_in_file(self, log_dir):
        log._emit("ERR ", "error msg")
        if log._log_fh:
            log._log_fh.flush()
        content = (log_dir / "picker.log").read_text(encoding="utf-8")
        assert "\033[" not in content


class TestRotation:
    def test_rotation_on_large_file(self, log_dir):
        logfile = log_dir / "picker.log"
        logfile.write_text("x" * (6 * 1024 * 1024))
        log._open_log_file()
        assert (log_dir / "picker.1.log").exists()


class TestPublicApi:
    def test_log_dir_returns_string(self, log_dir):
        result = log.log_dir()
        assert isinstance(result, str)

    def test_log_file_path_returns_string(self, log_dir):
        result = log.log_file_path()
        assert isinstance(result, str)
        assert "picker.log" in result
