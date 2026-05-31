"""EXIF extraction via Pillow. Graceful fallback if Pillow missing."""
import os
from collections import OrderedDict
from fractions import Fraction

try:
    from PIL import Image, ExifTags
    _HAVE_PIL = True
except Exception:
    _HAVE_PIL = False


_TAG = {v: k for k, v in ExifTags.TAGS.items()} if _HAVE_PIL else {}

# LRU cache keyed by (path, mtime) — EXIF reads are expensive for RAW files
_CACHE_MAX = 200
_cache: "OrderedDict[tuple[str, int], dict]" = OrderedDict()


def _fmt_shutter(val) -> str:
    try:
        f = Fraction(val).limit_denominator(8000)
        if f >= 1:
            return f"{float(f):.1f}s"
        return f"1/{int(1/float(f))}s"
    except Exception:
        return str(val)


def _fmt_aperture(val) -> str:
    try:
        return f"f/{float(val):.1f}"
    except Exception:
        return str(val)


def _fmt_focal(val) -> str:
    try:
        return f"{float(val):.0f}mm"
    except Exception:
        return str(val)


def read_exif(path: str) -> dict:
    """Return simplified EXIF dict. Empty dict if unavailable."""
    if not _HAVE_PIL:
        return {}
    try:
        mtime = int(os.path.getmtime(path))
    except OSError:
        mtime = 0
    key = (path, mtime)
    hit = _cache.get(key)
    if hit is not None:
        _cache.move_to_end(key)
        return hit
    try:
        with Image.open(path) as img:
            exif = img.getexif()
            if not exif:
                return {}
            data = dict(exif)
            # Get Exif sub-IFD
            try:
                sub = exif.get_ifd(ExifTags.IFD.Exif)
                data.update(sub)
            except Exception:
                pass
            out = {}

            def pick(*names):
                for n in names:
                    tag = _TAG.get(n)
                    if tag and tag in data:
                        return data[tag]
                return None

            make = pick("Make")
            model = pick("Model")
            if make or model:
                out["camera"] = f"{make or ''} {model or ''}".strip()
            lens = pick("LensModel", "LensMake")
            if lens:
                out["lens"] = str(lens).strip()
            iso = pick("ISOSpeedRatings", "PhotographicSensitivity")
            if iso is not None:
                out["iso"] = f"ISO {iso}"
            shutter = pick("ExposureTime")
            if shutter is not None:
                out["shutter"] = _fmt_shutter(shutter)
            aperture = pick("FNumber", "ApertureValue")
            if aperture is not None:
                out["aperture"] = _fmt_aperture(aperture)
            focal = pick("FocalLength")
            if focal is not None:
                out["focal"] = _fmt_focal(focal)
            dt = pick("DateTimeOriginal", "DateTime")
            if dt:
                out["datetime"] = str(dt)
            orientation = pick("Orientation")
            if orientation is not None:
                out["orientation"] = int(orientation)
            # Subject area / focus point (rare, but present on some cameras)
            sa = pick("SubjectArea", "SubjectLocation")
            if sa:
                out["subject_area"] = tuple(sa) if isinstance(sa, (tuple, list)) else sa
            _cache[key] = out
            _cache.move_to_end(key)
            while len(_cache) > _CACHE_MAX:
                _cache.popitem(last=False)
            return out
    except Exception:
        return {}


def format_lines(exif: dict) -> list[str]:
    """Human-readable lines for overlay."""
    lines = []
    if exif.get("camera"):
        lines.append(exif["camera"])
    if exif.get("lens"):
        lines.append(exif["lens"])
    exp_bits = [exif.get("shutter"), exif.get("aperture"), exif.get("iso"), exif.get("focal")]
    exp_line = "  ·  ".join(b for b in exp_bits if b)
    if exp_line:
        lines.append(exp_line)
    if exif.get("datetime"):
        lines.append(exif["datetime"])
    return lines
