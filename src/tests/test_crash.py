"""Tests for picker.crash — crash reporting and diagnostics."""
import os
from pathlib import Path

import pytest
from picker import crash


@pytest.fixture(autouse=True)
def crash_dir(tmp_path, monkeypatch):
    d = tmp_path / "PICker" / "crash-logs"
    d.mkdir(parents=True)
    monkeypatch.setattr("picker.crash._crash_dir", lambda: d)
    return d


class TestWriteCrash:
    def test_writes_crash_file(self, crash_dir):
        try:
            raise ValueError("test error")
        except ValueError:
            import sys
            path = crash.write_crash(*sys.exc_info())
        assert path is not None
        assert os.path.exists(path)
        content = Path(path).read_text(encoding="utf-8")
        assert "ValueError" in content
        assert "test error" in content

    def test_crash_file_contains_version(self, crash_dir):
        try:
            raise RuntimeError("boom")
        except RuntimeError:
            import sys
            path = crash.write_crash(*sys.exc_info())
        content = Path(path).read_text(encoding="utf-8")
        assert "Version:" in content


class TestLastCrash:
    def test_last_crash_none_when_empty(self, crash_dir):
        assert crash.last_crash() is None

    def test_last_crash_returns_content(self, crash_dir):
        try:
            raise ValueError("test")
        except ValueError:
            import sys
            crash.write_crash(*sys.exc_info())
        report = crash.last_crash()
        assert report is not None
        assert "ValueError" in report


class TestClearCrash:
    def test_clear_removes_file(self, crash_dir):
        try:
            raise ValueError("test")
        except ValueError:
            import sys
            crash.write_crash(*sys.exc_info())
        crash.clear_last_crash()
        assert crash.last_crash() is None


class TestDiagnostics:
    def test_contains_version(self):
        info = crash.diagnostics()
        assert "PICker" in info

    def test_contains_python(self):
        info = crash.diagnostics()
        assert "Python" in info


class TestEviction:
    def test_max_files_enforced(self, crash_dir):
        for i in range(25):
            (crash_dir / f"crash-{i:04d}.txt").write_text(f"crash {i}")
        crash._evict_old(crash_dir)
        files = list(crash_dir.glob("crash-*.txt"))
        assert len(files) <= crash._MAX_CRASH_FILES
