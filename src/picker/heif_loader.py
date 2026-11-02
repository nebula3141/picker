"""HEIC / HEIF / AVIF decoder via pillow-heif.

Optional, like the RAW loader: if pillow-heif isn't installed the app still runs,
these files just aren't decodable. Qt has no native HEIF/AVIF reader, so we route
through Pillow (pillow-heif registers the openers) and hand back a QImage.
"""
import importlib.util
import threading

from PyQt6.QtGui import QImage

# pillow-heif + PIL cost ~45 ms to import and register. Do it on the first HEIF
# file instead of on every launch; `available()` answers from a spec check.
Image = None
ImageOps = None
pillow_heif = None
_HAVE = (importlib.util.find_spec("pillow_heif") is not None
         and importlib.util.find_spec("PIL") is not None)
_loaded = False
_load_lock = threading.Lock()


def _ensure_loaded() -> bool:
    global Image, ImageOps, pillow_heif, _HAVE, _loaded
    if _loaded:
        return _HAVE
    with _load_lock:
        if not _loaded:
            try:
                import pillow_heif as _ph
                _ph.register_heif_opener()
                try:
                    # AVIF opener (available in newer pillow-heif); ignore if absent.
                    _ph.register_avif_opener()
                except Exception:
                    pass
                from PIL import Image as _Image, ImageOps as _ImageOps
                pillow_heif, Image, ImageOps = _ph, _Image, _ImageOps
                _HAVE = True
            except Exception:
                _HAVE = False
            # Published LAST: the unlocked fast path above trusts _loaded, so it
            # must never be True while Image is still None (other decode
            # workers would call None.open and cache a grey placeholder).
            _loaded = True
    return _HAVE


HEIF_EXTENSIONS = {".heic", ".heif", ".hif", ".avif"}


def is_heif(path: str) -> bool:
    import os
    return os.path.splitext(path)[1].lower() in HEIF_EXTENSIONS


def available() -> bool:
    return _HAVE


def _pil_to_qimage(im) -> QImage:
    """Convert a PIL image to a detached QImage (own its buffer)."""
    if im.mode not in ("RGB", "RGBA"):
        im = im.convert("RGBA" if "A" in im.mode else "RGB")
    if im.mode == "RGB":
        data = im.tobytes("raw", "RGB")
        qimg = QImage(data, im.width, im.height, im.width * 3,
                      QImage.Format.Format_RGB888)
    else:
        data = im.tobytes("raw", "RGBA")
        qimg = QImage(data, im.width, im.height, im.width * 4,
                      QImage.Format.Format_RGBA8888)
    return qimg.copy()


def load_heif(path: str, max_edge: int | None = None) -> QImage | None:
    """Decode a HEIF/AVIF file to a QImage (imports the decoder on first use).
    ``max_edge`` (if given) caps the
    longest side for cheap thumbnails. Returns None on any failure."""
    if not _ensure_loaded():
        return None
    try:
        with Image.open(path) as im:
            im = ImageOps.exif_transpose(im)   # honour camera orientation
            if max_edge and max(im.width, im.height) > max_edge:
                im.thumbnail((max_edge, max_edge))
            return _pil_to_qimage(im)
    except Exception:
        return None
