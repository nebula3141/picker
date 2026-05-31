"""Apply destructive edits (rotate, crop) to image files on disk.

Writes via QImage — works for JPEG/PNG/TIFF/BMP/WEBP. RAW files cannot be
overwritten; for RAW we always write a sibling JPEG regardless of mode.
"""
from pathlib import Path

from PyQt6.QtCore import QRect, QRectF, QSize, Qt
from PyQt6.QtGui import QImage, QImageReader, QTransform

from . import settings as settings_mod
from . import raw_loader


RAW_EXTS = {".cr2", ".cr3", ".nef", ".arw", ".dng", ".raf", ".orf", ".rw2", ".pef", ".srw"}


def _is_raw(path: str) -> bool:
    return Path(path).suffix.lower() in RAW_EXTS


def _load_full(path: str) -> QImage:
    """Decode full-resolution image (no scaling), honouring EXIF orientation."""
    if _is_raw(path) and raw_loader.available():
        img = raw_loader.load_raw(path, prefer_thumb=False)
        if img is not None and not img.isNull():
            return img
    reader = QImageReader(path)
    reader.setAutoTransform(True)
    return reader.read()


def _target_path(source: str, mode: str) -> Path:
    """Resolve output path for save mode. RAW always forces new-file + .jpg."""
    src = Path(source)
    if _is_raw(source):
        # Can't overwrite raw; write JPEG next to it.
        suffix = settings_mod.get("edit_new_suffix") or "_edit"
        return src.with_name(f"{src.stem}{suffix}.jpg")
    if mode == "overwrite":
        return src
    suffix = settings_mod.get("edit_new_suffix") or "_edit"
    target = src.with_name(f"{src.stem}{suffix}{src.suffix}")
    # Avoid clobbering an existing "_edit" file
    i = 2
    while target.exists() and target != src:
        target = src.with_name(f"{src.stem}{suffix}{i}{src.suffix}")
        i += 1
    return target


def _save(img: QImage, target: Path) -> tuple[bool, str | None]:
    ext = target.suffix.lower().lstrip(".")
    fmt_map = {"jpg": "JPEG", "jpeg": "JPEG", "png": "PNG",
               "tif": "TIFF", "tiff": "TIFF", "bmp": "BMP", "webp": "WEBP"}
    fmt = fmt_map.get(ext, "JPEG")
    quality = int(settings_mod.get("edit_jpeg_quality") or 95) if fmt in ("JPEG", "WEBP") else -1
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        return False, str(e)
    if not img.save(str(target), fmt, quality):
        return False, f"QImage.save failed for {target}"
    return True, None


def apply_rotation(source: str, degrees: int, mode: str) -> tuple[str | None, str | None]:
    """Rotate image by degrees (0/90/180/270) and save. Returns (out_path, error)."""
    degrees = degrees % 360
    if degrees == 0:
        return source, None
    img = _load_full(source)
    if img.isNull():
        return None, "could not decode image"
    t = QTransform()
    t.rotate(degrees)
    rotated = img.transformed(t, Qt.TransformationMode.SmoothTransformation)
    target = _target_path(source, mode)
    ok, err = _save(rotated, target)
    return (str(target) if ok else None), err


def apply_crop(source: str, rect_norm: QRectF, extra_rotation: int, mode: str) -> tuple[str | None, str | None]:
    """Crop by a normalized rect (0..1 in post-rotation image space), then save.

    rect_norm is relative to what the user sees on canvas after any preview rotation.
    We load the original at full res, apply the same rotation, then scale the norm rect
    to pixel coords. This keeps output at full resolution regardless of preview scale.
    """
    img = _load_full(source)
    if img.isNull():
        return None, "could not decode image"
    if extra_rotation % 360 != 0:
        t = QTransform()
        t.rotate(extra_rotation)
        img = img.transformed(t, Qt.TransformationMode.SmoothTransformation)
    W, H = img.width(), img.height()
    x = max(0, int(round(rect_norm.x() * W)))
    y = max(0, int(round(rect_norm.y() * H)))
    w = max(1, int(round(rect_norm.width() * W)))
    h = max(1, int(round(rect_norm.height() * H)))
    clipped = QRect(x, y, w, h).intersected(QRect(0, 0, W, H))
    if clipped.width() < 2 or clipped.height() < 2:
        return None, "crop area too small"
    cropped = img.copy(clipped)
    target = _target_path(source, mode)
    ok, err = _save(cropped, target)
    return (str(target) if ok else None), err
