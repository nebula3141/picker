"""SQLite-backed library index.

One DB at %APPDATA%/PICker/index.sqlite holds metadata for every image under
any library root. The scanner walks a root and inserts/updates rows; unchanged
files (matching size+mtime) are skipped. Queries power folder browsing,
search, sort, and group-by without re-scanning disk.

Kept module-level (functions, not a class) so callers can open/close a
connection per operation and avoid threading locks.
"""
import os
import sqlite3
import time
from pathlib import Path
from typing import Callable, Iterable

from .image_manager import SUPPORTED_EXTENSIONS


# ── Locations ──────────────────────────────────────────────────────────────────

def _config_dir() -> Path:
    from . import settings as settings_mod
    if settings_mod.is_portable():
        return settings_mod.portable_dir()
    if os.name == "nt":
        base = Path(os.environ.get("APPDATA", Path.home()))
    else:
        base = Path.home() / ".config"
    d = base / "PICker"
    try:
        d.mkdir(parents=True, exist_ok=True)
    except Exception:
        pass
    return d


def db_path() -> Path:
    return _config_dir() / "index.sqlite"


# ── Schema ─────────────────────────────────────────────────────────────────────

_SCHEMA = """
CREATE TABLE IF NOT EXISTS files (
    path          TEXT PRIMARY KEY,
    root          TEXT NOT NULL,
    parent        TEXT NOT NULL,
    filename      TEXT NOT NULL,
    ext           TEXT NOT NULL,
    size          INTEGER NOT NULL,
    mtime         REAL NOT NULL,
    width         INTEGER,
    height        INTEGER,
    date_taken    TEXT,
    camera_make   TEXT,
    camera_model  TEXT,
    has_gps       INTEGER DEFAULT 0,
    rating        INTEGER DEFAULT 0,
    flag          TEXT,
    indexed_at    REAL NOT NULL,
    media_type    TEXT DEFAULT 'image',
    duration_ms   INTEGER
);
CREATE INDEX IF NOT EXISTS idx_files_parent      ON files(parent);
CREATE INDEX IF NOT EXISTS idx_files_root        ON files(root);
CREATE INDEX IF NOT EXISTS idx_files_date_taken  ON files(date_taken);
CREATE INDEX IF NOT EXISTS idx_files_ext         ON files(ext);
CREATE INDEX IF NOT EXISTS idx_files_camera      ON files(camera_model);
CREATE INDEX IF NOT EXISTS idx_files_rating      ON files(rating);
CREATE INDEX IF NOT EXISTS idx_files_flag        ON files(flag);
CREATE INDEX IF NOT EXISTS idx_files_media_type  ON files(media_type);
"""


def _migrate(conn: sqlite3.Connection) -> None:
    """Apply ALTER TABLE for columns added after v3.0. SQLite has no
    IF NOT EXISTS for ADD COLUMN; we duck-type via PRAGMA + try/except."""
    cur = conn.execute("PRAGMA table_info(files)")
    cols = {row[1] for row in cur.fetchall()}
    if "media_type" not in cols:
        try:
            conn.execute("ALTER TABLE files ADD COLUMN media_type TEXT DEFAULT 'image'")
        except sqlite3.OperationalError:
            pass
    if "duration_ms" not in cols:
        try:
            conn.execute("ALTER TABLE files ADD COLUMN duration_ms INTEGER")
        except sqlite3.OperationalError:
            pass


def _backup_db(path: Path) -> None:
    """Create a backup before schema migrations."""
    bak = path.with_suffix(".sqlite.bak")
    try:
        import shutil
        shutil.copy2(path, bak)
    except Exception:
        pass


def integrity_check() -> str | None:
    """Run PRAGMA integrity_check. Returns None if OK, error string otherwise."""
    path = db_path()
    if not path.exists():
        return None
    try:
        conn = sqlite3.connect(path, timeout=5.0)
        result = conn.execute("PRAGMA integrity_check").fetchone()
        conn.close()
        if result and result[0] == "ok":
            return None
        return str(result[0]) if result else "unknown error"
    except Exception as e:
        return str(e)


def connect() -> sqlite3.Connection:
    """Open the index DB. If the file is corrupt or schema-applying fails,
    move it aside and start fresh — better than crashing the app on launch
    because of a half-flushed sqlite from a previous power loss."""
    path = db_path()
    try:
        conn = sqlite3.connect(path, timeout=10.0, isolation_level=None)
        conn.execute("PRAGMA journal_mode=WAL;")
        conn.execute("PRAGMA synchronous=NORMAL;")
        conn.execute("PRAGMA foreign_keys=ON;")
        _backup_db(path)
        conn.executescript(_SCHEMA)
        _migrate(conn)
        conn.row_factory = sqlite3.Row
        return conn
    except sqlite3.DatabaseError:
        try:
            conn.close()
        except Exception:
            pass
        try:
            from . import log
            log.error("index DB corrupt — quarantining", path=str(path))
        except Exception:
            pass
        try:
            quarantine = path.with_suffix(path.suffix + ".corrupt")
            os.replace(str(path), str(quarantine))
            for sib_suffix in ("-wal", "-shm"):
                sib = path.with_name(path.name + sib_suffix)
                if sib.exists():
                    try:
                        sib.unlink()
                    except OSError:
                        pass
        except OSError as e:
            from . import log
            log.error("index quarantine failed", err=str(e))
        conn = sqlite3.connect(path, timeout=10.0, isolation_level=None)
        conn.execute("PRAGMA journal_mode=WAL;")
        conn.execute("PRAGMA synchronous=NORMAL;")
        conn.execute("PRAGMA foreign_keys=ON;")
        conn.executescript(_SCHEMA)
        conn.row_factory = sqlite3.Row
        return conn


def _norm(p: str) -> str:
    return os.path.normcase(os.path.abspath(p))


# ── Metadata extraction (lightweight) ──────────────────────────────────────────

def _read_dimensions(path: str) -> tuple[int | None, int | None]:
    """Fast dimension read via QImageReader (header-only, no decode)."""
    try:
        from PyQt6.QtGui import QImageReader
        r = QImageReader(path)
        if r.canRead():
            s = r.size()
            if s.isValid():
                return s.width(), s.height()
    except Exception:
        pass
    return None, None


def _read_exif_fields(path: str) -> dict:
    """Return {date_taken, camera_make, camera_model, has_gps}."""
    from . import exif as exif_mod
    out: dict = {}
    try:
        ex = exif_mod.read_exif(path)
    except Exception:
        ex = {}
    dt = ex.get("datetime")
    if dt:
        # EXIF "YYYY:MM:DD HH:MM:SS" → ISO "YYYY-MM-DDTHH:MM:SS"
        s = str(dt).strip()
        if len(s) >= 10 and s[4] == ":" and s[7] == ":":
            s = s[:4] + "-" + s[5:7] + "-" + s[8:10] + "T" + s[11:].replace(" ", "")
        out["date_taken"] = s
    camera = ex.get("camera")
    if camera:
        parts = str(camera).split(" ", 1)
        out["camera_make"] = parts[0] if parts else None
        out["camera_model"] = parts[1] if len(parts) > 1 else parts[0]
    # has_gps — exif module doesn't expose; cheap re-peek via Pillow
    try:
        from PIL import Image, ExifTags
        with Image.open(path) as img:
            gps_tag = None
            for k, v in ExifTags.TAGS.items():
                if v == "GPSInfo":
                    gps_tag = k
                    break
            ex_raw = img.getexif()
            if gps_tag and ex_raw and gps_tag in ex_raw:
                out["has_gps"] = 1
    except Exception:
        pass
    return out


# ── Scanning ───────────────────────────────────────────────────────────────────

def scan_root(
    root: str,
    *,
    include_subfolders: bool = True,
    exclude_hidden: bool = True,
    extensions: Iterable[str] | None = None,
    progress_cb: Callable[[int, int, str], None] | None = None,
    cancel_cb: Callable[[], bool] | None = None,
) -> dict:
    """Walk a root folder, inserting/updating rows. Returns stats dict.

    Files with unchanged (size, mtime) are skipped. Files no longer on disk
    under the root are deleted from the index.
    """
    root_abs = _norm(root)
    if not os.path.isdir(root_abs):
        return {"scanned": 0, "added": 0, "updated": 0, "removed": 0, "skipped": 0}

    if extensions is None:
        # Default: pick up everything the app can render — images + videos.
        from .media import MEDIA_EXTENSIONS
        exts = {e.lower() for e in MEDIA_EXTENSIONS}
    else:
        exts = {e.lower() for e in extensions}
    from .media import VIDEO_EXTENSIONS as _VID
    stats = {"scanned": 0, "added": 0, "updated": 0, "removed": 0, "skipped": 0}

    conn = connect()
    try:
        # Collect on-disk files
        disk_files: list[tuple[str, int, float]] = []  # (path_norm, size, mtime)
        walker = os.walk(root_abs) if include_subfolders else [next(os.walk(root_abs))]
        for dirpath, dirnames, filenames in walker:
            if exclude_hidden:
                dirnames[:] = [d for d in dirnames if not d.startswith(".")]
            for fn in filenames:
                if exclude_hidden and fn.startswith("."):
                    continue
                ext = os.path.splitext(fn)[1].lower()
                if ext not in exts:
                    continue
                full = os.path.join(dirpath, fn)
                try:
                    st = os.stat(full)
                    disk_files.append((_norm(full), st.st_size, st.st_mtime))
                except OSError:
                    continue

        total = len(disk_files)

        # Existing rows for this root (path → (size, mtime))
        cur = conn.execute(
            "SELECT path, size, mtime FROM files WHERE root = ?", (root_abs,)
        )
        existing = {row["path"]: (row["size"], row["mtime"]) for row in cur}

        disk_set = {p for p, _, _ in disk_files}
        to_remove = [p for p in existing if p not in disk_set]
        if to_remove:
            conn.executemany("DELETE FROM files WHERE path = ?", [(p,) for p in to_remove])
            stats["removed"] = len(to_remove)

        now = time.time()
        for i, (path_norm, size, mtime) in enumerate(disk_files):
            if cancel_cb and cancel_cb():
                break
            stats["scanned"] += 1
            prev = existing.get(path_norm)
            if prev and prev[0] == size and abs(prev[1] - mtime) < 0.001:
                stats["skipped"] += 1
            else:
                parent = os.path.dirname(path_norm)
                filename = os.path.basename(path_norm)
                ext = os.path.splitext(filename)[1].lower()
                is_video = ext in _VID
                if is_video:
                    media_type = "video"
                    # ffprobe is opt-in (binary may be absent). Pulls
                    # width/height/duration without decoding any frame.
                    from .media import probe_video
                    probe = probe_video(path_norm)
                    w = probe.get("width")
                    h = probe.get("height")
                    duration_ms = probe.get("duration_ms")
                    meta = {}
                else:
                    media_type = "image"
                    duration_ms = None
                    w, h = _read_dimensions(path_norm)
                    meta = _read_exif_fields(path_norm)
                conn.execute(
                    """INSERT INTO files
                        (path, root, parent, filename, ext, size, mtime,
                         width, height, date_taken, camera_make, camera_model,
                         has_gps, indexed_at, media_type, duration_ms)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                       ON CONFLICT(path) DO UPDATE SET
                         size=excluded.size, mtime=excluded.mtime,
                         width=excluded.width, height=excluded.height,
                         date_taken=excluded.date_taken,
                         camera_make=excluded.camera_make,
                         camera_model=excluded.camera_model,
                         has_gps=excluded.has_gps,
                         indexed_at=excluded.indexed_at,
                         media_type=excluded.media_type,
                         duration_ms=excluded.duration_ms""",
                    (path_norm, root_abs, parent, filename, ext, size, mtime,
                     w, h, meta.get("date_taken"), meta.get("camera_make"),
                     meta.get("camera_model"), meta.get("has_gps", 0), now,
                     media_type, duration_ms),
                )
                if prev:
                    stats["updated"] += 1
                else:
                    stats["added"] += 1
            if progress_cb:
                progress_cb(i + 1, total, path_norm)
        return stats
    finally:
        conn.close()


def remove_root_entries(root: str) -> int:
    """Delete all rows for a root (e.g. when user removes root from library)."""
    root_abs = _norm(root)
    conn = connect()
    try:
        cur = conn.execute("DELETE FROM files WHERE root = ?", (root_abs,))
        return cur.rowcount or 0
    finally:
        conn.close()


# ── Queries ────────────────────────────────────────────────────────────────────

def files_in_folder(parent: str, *, recursive: bool = False) -> list[sqlite3.Row]:
    parent_norm = _norm(parent)
    conn = connect()
    try:
        if recursive:
            cur = conn.execute(
                "SELECT * FROM files WHERE path LIKE ? ORDER BY filename",
                (parent_norm + os.sep + "%",),
            )
        else:
            cur = conn.execute(
                "SELECT * FROM files WHERE parent = ? ORDER BY filename",
                (parent_norm,),
            )
        return list(cur)
    finally:
        conn.close()


def folder_counts(parent: str) -> int:
    """Count files directly in a folder (non-recursive)."""
    parent_norm = _norm(parent)
    conn = connect()
    try:
        cur = conn.execute("SELECT COUNT(*) FROM files WHERE parent = ?", (parent_norm,))
        return int(cur.fetchone()[0])
    finally:
        conn.close()


def folder_count_recursive(folder: str) -> int:
    folder_norm = _norm(folder)
    conn = connect()
    try:
        cur = conn.execute(
            "SELECT COUNT(*) FROM files WHERE path LIKE ?",
            (folder_norm + os.sep + "%",),
        )
        return int(cur.fetchone()[0])
    finally:
        conn.close()


def distinct_cameras() -> list[str]:
    conn = connect()
    try:
        cur = conn.execute(
            "SELECT DISTINCT camera_model FROM files WHERE camera_model IS NOT NULL ORDER BY camera_model"
        )
        return [r[0] for r in cur]
    finally:
        conn.close()


def search(
    *,
    root: str | None = None,
    parent: str | None = None,
    recursive: bool = False,
    filename_like: str | None = None,
    date_from: str | None = None,   # ISO 'YYYY-MM-DD'
    date_to: str | None = None,
    camera: str | None = None,
    rating_min: int | None = None,
    flag: str | None = None,
    exts: list[str] | None = None,  # e.g. ['.jpg', '.jpeg']
    has_gps: bool | None = None,
    min_width: int | None = None,
    min_height: int | None = None,
    orientation: str | None = None,   # "landscape"|"portrait"|"square"
    order_by: str = "filename",       # "filename"|"date_taken"|"mtime"|"size"|"rating"|"random"
    limit: int | None = None,
) -> list[sqlite3.Row]:
    clauses: list[str] = []
    params: list = []
    if root:
        clauses.append("root = ?")
        params.append(_norm(root))
    if parent:
        if recursive:
            clauses.append("path LIKE ?")
            params.append(_norm(parent) + os.sep + "%")
        else:
            clauses.append("parent = ?")
            params.append(_norm(parent))
    if filename_like:
        clauses.append("filename LIKE ?")
        params.append(f"%{filename_like}%")
    if date_from:
        clauses.append("date_taken >= ?")
        params.append(date_from)
    if date_to:
        clauses.append("date_taken <= ?")
        params.append(date_to + "T99")
    if camera:
        clauses.append("camera_model = ?")
        params.append(camera)
    if rating_min is not None:
        clauses.append("rating >= ?")
        params.append(int(rating_min))
    if flag:
        clauses.append("flag = ?")
        params.append(flag)
    if exts:
        placeholders = ",".join("?" for _ in exts)
        clauses.append(f"ext IN ({placeholders})")
        params.extend([e.lower() for e in exts])
    if has_gps is True:
        clauses.append("has_gps = 1")
    elif has_gps is False:
        clauses.append("(has_gps = 0 OR has_gps IS NULL)")
    if min_width:
        clauses.append("width >= ?")
        params.append(int(min_width))
    if min_height:
        clauses.append("height >= ?")
        params.append(int(min_height))
    if orientation == "landscape":
        clauses.append("width > height")
    elif orientation == "portrait":
        clauses.append("height > width")
    elif orientation == "square":
        clauses.append("width = height AND width IS NOT NULL")

    order_sql = {
        "filename":    "filename ASC",
        "date_taken":  "date_taken DESC NULLS LAST, mtime DESC",
        "mtime":       "mtime DESC",
        "size":        "size DESC",
        "rating":      "rating DESC, date_taken DESC",
        "random":      "RANDOM()",
    }.get(order_by, "filename ASC")

    sql = "SELECT * FROM files"
    if clauses:
        sql += " WHERE " + " AND ".join(clauses)
    sql += " ORDER BY " + order_sql
    if limit:
        sql += f" LIMIT {int(limit)}"

    conn = connect()
    try:
        return list(conn.execute(sql, params))
    finally:
        conn.close()


# ── Sidecar metadata (rating / flag) ───────────────────────────────────────────

def set_rating(path: str, rating: int) -> None:
    rating = max(0, min(5, int(rating)))
    conn = connect()
    try:
        conn.execute("UPDATE files SET rating = ? WHERE path = ?", (rating, _norm(path)))
    finally:
        conn.close()


def set_flag(path: str, flag: str | None) -> None:
    """flag in {'pick','reject',None}."""
    if flag not in (None, "pick", "reject"):
        flag = None
    conn = connect()
    try:
        conn.execute("UPDATE files SET flag = ? WHERE path = ?", (flag, _norm(path)))
    finally:
        conn.close()


def clear_all() -> None:
    """Drop all indexed data — user-invoked reset."""
    conn = connect()
    try:
        conn.execute("DELETE FROM files")
    finally:
        conn.close()
