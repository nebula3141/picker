"""RAW file decoder. Uses rawpy; falls back to embedded thumb for speed.

rawpy and numpy are imported lazily — on the first RAW decode, on a worker
thread — instead of at app start, where they added a noticeable chunk to launch
time even for users who never open a RAW file.
"""
import importlib.util
import threading
from pathlib import Path

from PyQt6.QtGui import QImage
from PyQt6.QtCore import QByteArray

rawpy = None
np = None
_import_lock = threading.Lock()
_imported = False
_HAVE_RAWPY = importlib.util.find_spec("rawpy") is not None     # cheap: no import
_HAVE_NUMPY = importlib.util.find_spec("numpy") is not None


def _ensure_imports() -> bool:
    global rawpy, np, _imported, _HAVE_RAWPY, _HAVE_NUMPY
    if _imported:
        return _HAVE_RAWPY
    with _import_lock:
        if not _imported:
            try:
                import rawpy as _rawpy
                rawpy = _rawpy
            except Exception:
                _HAVE_RAWPY = False
            try:
                import numpy as _np
                np = _np
            except Exception:
                _HAVE_NUMPY = False
            _imported = True
    return _HAVE_RAWPY


RAW_EXTENSIONS = {".cr2", ".cr3", ".nef", ".arw", ".dng", ".raf", ".orf", ".rw2", ".pef", ".srw"}


def is_raw(path: str) -> bool:
    import os
    return os.path.splitext(path)[1].lower() in RAW_EXTENSIONS


def available() -> bool:
    return _HAVE_RAWPY and _HAVE_NUMPY


def load_raw(path: str, prefer_thumb: bool = True) -> QImage | None:
    """Return QImage of RAW. Tries embedded JPEG first (fast), then full demosaic."""
    if not _ensure_imports():
        return None
    try:
        with rawpy.imread(path) as raw:
            if prefer_thumb:
                try:
                    thumb = raw.extract_thumb()
                    if thumb.format == rawpy.ThumbFormat.JPEG:
                        img = QImage()
                        img.loadFromData(QByteArray(thumb.data), "JPEG")
                        if not img.isNull():
                            return img
                    elif thumb.format == rawpy.ThumbFormat.BITMAP and _HAVE_NUMPY:
                        return _numpy_to_qimage(thumb.data)
                except (rawpy.LibRawNoThumbnailError, rawpy.LibRawUnsupportedThumbnailError):
                    pass
            if _HAVE_NUMPY:
                arr = raw.postprocess(use_camera_wb=True, no_auto_bright=False, output_bps=8)
                return _numpy_to_qimage(arr)
    except Exception:
        return None
    return None


def _numpy_to_qimage(arr) -> QImage:
    if arr.ndim != 3 or arr.shape[2] != 3:
        return QImage()
    h, w, _ = arr.shape
    arr = np.ascontiguousarray(arr)
    img = QImage(arr.tobytes(), w, h, w * 3, QImage.Format.Format_RGB888)
    return img.copy()   # detach from numpy buffer
