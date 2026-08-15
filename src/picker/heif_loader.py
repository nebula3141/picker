"""HEIC / HEIF / AVIF decoder via pillow-heif.

Optional, like the RAW loader: if pillow-heif isn't installed the app still runs,
these files just aren't decodable. Qt has no native HEIF/AVIF reader, so we route
through Pillow (pillow-heif registers the openers) and hand back a QImage.
"""
from pathlib import Path

from PyQt6.QtGui import QImage

_HAVE = False
try:
    import pillow_heif
    pillow_heif.register_heif_opener()
    try:
        # AVIF opener (available in newer pillow-heif); ignore if absent.
        pillow_heif.register_avif_opener()
    except Exception:
        pass
    from PIL import Image, ImageOps
    _HAVE = True
except Exception:
    _HAVE = False


HEIF_EXTENSIONS = {".heic", ".heif", ".hif", ".avif"}


def is_heif(path: str) -> bool:
    return Path(path).suffix.lower() in HEIF_EXTENSIONS


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
    """Decode a HEIF/AVIF file to a QImage. ``max_edge`` (if given) caps the
    longest side for cheap thumbnails. Returns None on any failure."""
    if not _HAVE:
        return None
    try:
        with Image.open(path) as im:
            im = ImageOps.exif_transpose(im)   # honour camera orientation
            if max_edge and max(im.width, im.height) > max_edge:
                im.thumbnail((max_edge, max_edge))
            return _pil_to_qimage(im)
    except Exception:
        return None
