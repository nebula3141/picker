import json
import os
import tempfile
from pathlib import Path

import pytest


@pytest.fixture
def tmp_dir(tmp_path):
    return tmp_path


@pytest.fixture
def settings_dir(tmp_path, monkeypatch):
    d = tmp_path / "PICker"
    d.mkdir()
    monkeypatch.setattr("picker.settings._config_dir", lambda: d)
    return d


@pytest.fixture
def settings_file(settings_dir):
    return settings_dir / "settings.json"


@pytest.fixture
def sample_images(tmp_path):
    """Create tiny valid JPEG files for scan tests."""
    from PIL import Image
    folder = tmp_path / "photos"
    folder.mkdir()
    paths = []
    for i in range(5):
        p = folder / f"img_{i:03d}.jpg"
        img = Image.new("RGB", (100, 100), color=(i * 50, 100, 200))
        img.save(str(p), "JPEG")
        paths.append(str(p))
    return str(folder), paths


@pytest.fixture
def index_dir(tmp_path, monkeypatch):
    d = tmp_path / "PICker"
    d.mkdir()
    monkeypatch.setattr("picker.index._config_dir", lambda: d)
    return d
