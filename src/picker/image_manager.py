import json
import os
import shutil
import tempfile
from pathlib import Path
from dataclasses import dataclass, field
from threading import Thread, Lock
from typing import Optional

from . import settings as settings_mod

SUPPORTED_EXTENSIONS = {
    # Common raster
    ".jpg", ".jpeg", ".png", ".tiff", ".tif", ".bmp", ".webp",
    # Vector
    ".svg", ".svgz",
    # RAW
    ".cr2", ".cr3", ".nef", ".arw", ".dng", ".raf", ".orf", ".rw2", ".pef", ".srw",
}

FILE_TYPE_MAP = {
    "jpeg": {".jpg", ".jpeg"},
    "png":  {".png"},
    "tiff": {".tiff", ".tif"},
    "webp": {".webp"},
    "bmp":  {".bmp"},
    "svg":  {".svg", ".svgz"},
    "raw":  {".cr2", ".cr3", ".nef", ".arw", ".dng", ".raf", ".orf", ".rw2", ".pef", ".srw"},
}


def active_extensions(
    enabled_types: list[str] | None, *, include_videos: bool | None = None
) -> set[str]:
    if not enabled_types:
        exts = set(SUPPORTED_EXTENSIONS)
    else:
        exts = set()
        for t in enabled_types:
            exts |= FILE_TYPE_MAP.get(t, set())
        if not exts:
            exts = set(SUPPORTED_EXTENSIONS)
    # Videos travel through the same scanners; opt-out only via setting.
    if include_videos is None:
        include_videos = bool(settings_mod.get("include_videos"))
    if include_videos:
        # Local import — avoids a cycle (media imports from this module).
        from .media import VIDEO_EXTENSIONS
        exts |= VIDEO_EXTENSIONS
    return exts

STATUS_UNREVIEWED = "unreviewed"
# destinations are stored as "dest_0", "dest_1", "dest_2"


# ── Move journal — persist move ops so they survive restart ──────────────────

_JOURNAL_MAX = 200

def _journal_file() -> Path:
    if os.name == "nt":
        base = Path(os.environ.get("APPDATA", Path.home()))
    else:
        base = Path.home() / ".config"
    return base / "PICker" / "move-journal.json"


def _load_journal() -> list[dict]:
    try:
        p = _journal_file()
        if p.exists():
            raw = json.loads(p.read_text(encoding="utf-8"))
            if isinstance(raw, list):
                return raw
    except Exception:
        pass
    return []


def _save_journal(entries: list[dict]) -> None:
    try:
        p = _journal_file()
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(entries[-_JOURNAL_MAX:], indent=2), encoding="utf-8")
    except Exception as e:
        from . import log
        log.warn("move journal save failed", err=str(e))


def _journal_add(src: str, dest: str) -> None:
    entries = _load_journal()
    entries.append({"src": src, "dest": dest})
    _save_journal(entries)


def _journal_remove(src: str, dest: str) -> None:
    entries = _load_journal()
    norm_s = os.path.normcase(os.path.abspath(src))
    norm_d = os.path.normcase(os.path.abspath(dest))
    entries = [e for e in entries
               if not (os.path.normcase(os.path.abspath(e.get("src", ""))) == norm_s
                       and os.path.normcase(os.path.abspath(e.get("dest", ""))) == norm_d)]
    _save_journal(entries)


def pending_moves() -> list[dict]:
    """Return journal entries where dest exists but src doesn't (recoverable moves)."""
    result = []
    for e in _load_journal():
        src, dest = e.get("src", ""), e.get("dest", "")
        if dest and os.path.isfile(dest) and src and not os.path.isfile(src):
            result.append(e)
    return result


def undo_move(src: str, dest: str) -> str | None:
    """Move file back from dest to src. Returns error string or None."""
    try:
        Path(src).parent.mkdir(parents=True, exist_ok=True)
        shutil.move(dest, src)
        _journal_remove(src, dest)
        return None
    except Exception as e:
        return str(e)


@dataclass
class ImageRecord:
    path: str          # absolute path
    filename: str      # basename only
    status: str = STATUS_UNREVIEWED
    thumbnail: object = field(default=None, repr=False)


@dataclass
class UndoEntry:
    action: str        # "send"
    image_idx: int
    prev_status: str
    dest_idx: Optional[int]
    dest_path: Optional[str]


class ImageManager:
    MAX_UNDO = 50

    def __init__(self, source_folder: str, destinations: list[dict], mode: str, resolution_pct: int,
                 progress_cb=None, include_subfolders: bool | None = None):
        self.source_folder = source_folder
        self.destinations = destinations
        self.mode = mode
        self.resolution_pct = resolution_pct
        self.images: list[ImageRecord] = []
        self.undo_stack: list[UndoEntry] = []
        self.rotations: dict[str, int] = {}     # filename -> 0/90/180/270 (in-memory; not persisted)
        # Optional conflict callback: fn(src_path, dest_path) -> "replace"|"rename"|"skip"|"cancel"
        self.conflict_handler = None
        # Optional progress callback: fn(done:int, total:int, current:str). total=0 => indeterminate.
        self._progress_cb = progress_cb
        # When set, overrides settings["include_subfolders"]. Used by album-mode
        # callers that want to scope a manager to exactly one folder.
        self._include_subfolders_override = include_subfolders
        self._load_images()

    @property
    def has_destinations(self) -> bool:
        return len(self.destinations) > 0

    def _load_images(self) -> None:
        """Walk source folder. Honors settings: include_subfolders, exclude_hidden, file_types."""
        folder = Path(self.source_folder).resolve()

        include_sub = (
            self._include_subfolders_override
            if self._include_subfolders_override is not None
            else bool(settings_mod.get("include_subfolders"))
        )
        exclude_hidden = bool(settings_mod.get("exclude_hidden"))
        types = settings_mod.get("file_types") or []
        exts = active_extensions(types if isinstance(types, list) else None)

        excluded = set()
        for d in self.destinations:
            try:
                excluded.add(Path(d["path"]).resolve())
            except Exception:
                continue

        files: list[Path] = []
        cb = self._progress_cb

        def walk(dir_path: Path):
            try:
                entries = list(dir_path.iterdir())
            except OSError:
                return
            if cb:
                try:
                    rel_dir = dir_path.relative_to(folder).as_posix() or "."
                except ValueError:
                    rel_dir = dir_path.name
                cb(len(files), 0, f"Scanning {rel_dir}…")
            for p in entries:
                try:
                    if p.is_dir():
                        if not include_sub:
                            continue
                        rp = p.resolve()
                        if rp in excluded:
                            continue
                        if exclude_hidden and (p.name.startswith(".") or p.name == "__pycache__"):
                            continue
                        walk(p)
                    elif p.is_file() and p.suffix.lower() in exts:
                        files.append(p)
                except OSError:
                    continue

        walk(folder)
        files.sort()
        # Filename uniqueness: include relative path so identical basenames in
        # different subfolders don't collide in session/status lookups.
        total = len(files)
        self.images = []
        for i, p in enumerate(files):
            try:
                rel = p.relative_to(folder).as_posix()
            except ValueError:
                rel = p.name
            self.images.append(ImageRecord(path=str(p), filename=rel))
            if cb and (i & 63) == 0:
                cb(i + 1, total, f"Preparing {i + 1} of {total}")
        if cb:
            cb(total, total, f"Found {total} images")

    # ── Instant-open: seed + background scan ────────────────────────────────

    _scan_lock = None
    _scan_done = False
    _scan_thread = None
    _on_scan_progress = None  # callback(discovered_count)
    _on_scan_complete = None  # callback()

    @classmethod
    def create_seeded(cls, source_folder: str, target_file: str,
                      seed_count: int = 5,
                      destinations: list[dict] | None = None,
                      mode: str = "copy", resolution_pct: int = 50,
                      on_progress=None, on_complete=None):
        """Create ImageManager with only ±seed_count files around target_file loaded.
        Remaining files discovered in background thread, closest-first."""
        mgr = cls.__new__(cls)
        mgr.source_folder = source_folder
        mgr.destinations = destinations or []
        mgr.mode = mode
        mgr.resolution_pct = resolution_pct
        mgr.images = []
        mgr.undo_stack = []
        mgr.rotations = {}
        mgr.conflict_handler = None
        mgr._progress_cb = None
        mgr._include_subfolders_override = False
        mgr._scan_lock = Lock()
        mgr._scan_done = False
        mgr._on_scan_progress = on_progress
        mgr._on_scan_complete = on_complete

        folder = Path(source_folder).resolve()
        types = settings_mod.get("file_types") or []
        exts = active_extensions(types if isinstance(types, list) else None)
        exclude_hidden = bool(settings_mod.get("exclude_hidden"))

        try:
            all_names = sorted(
                f for f in os.listdir(str(folder))
                if os.path.isfile(os.path.join(str(folder), f))
                and os.path.splitext(f)[1].lower() in exts
                and (not exclude_hidden or not f.startswith("."))
            )
        except OSError:
            all_names = []

        target_base = os.path.basename(target_file)
        try:
            center = [n.lower() for n in all_names].index(target_base.lower())
        except ValueError:
            center = 0

        lo = max(0, center - seed_count)
        hi = min(len(all_names), center + seed_count + 1)
        seed_names = all_names[lo:hi]

        mgr.images = [
            ImageRecord(
                path=os.path.join(str(folder), n),
                filename=n,
            )
            for n in seed_names
        ]
        mgr._seed_idx = center - lo

        remaining = all_names[:lo] + all_names[hi:]
        remaining_sorted = sorted(remaining, key=lambda n: abs(
            all_names.index(n) - center
        ))

        def _bg_scan():
            import bisect
            for name in remaining_sorted:
                rec = ImageRecord(
                    path=os.path.join(str(folder), name),
                    filename=name,
                )
                with mgr._scan_lock:
                    keys = [r.filename.lower() for r in mgr.images]
                    insert_pos = bisect.bisect_left(keys, name.lower())
                    mgr.images.insert(insert_pos, rec)
                if mgr._on_scan_progress:
                    try:
                        mgr._on_scan_progress(len(mgr.images))
                    except Exception:
                        pass
            with mgr._scan_lock:
                mgr._scan_done = True
            if mgr._on_scan_complete:
                try:
                    mgr._on_scan_complete()
                except Exception:
                    pass

        mgr._bg_scan_fn = _bg_scan
        mgr._scan_thread = None
        return mgr, mgr._seed_idx

    def start_background_scan(self):
        """Call after initial image is on screen to begin discovering remaining files."""
        if self._scan_thread or not hasattr(self, "_bg_scan_fn"):
            return
        self._scan_thread = Thread(target=self._bg_scan_fn, daemon=True)
        self._scan_thread.start()

    @property
    def scan_complete(self) -> bool:
        return self._scan_done if self._scan_lock else True

    def current_index_of(self, path: str) -> int:
        """Find current index of a file path (may shift during background scan)."""
        norm = os.path.normcase(os.path.abspath(path))
        with (self._scan_lock or Lock()):
            for i, rec in enumerate(self.images):
                if os.path.normcase(os.path.abspath(rec.path)) == norm:
                    return i
        return 0

    @staticmethod
    def check_dest_writable(destinations: list[dict]) -> str | None:
        """Return None if all dests writable (creates missing). Else error string."""
        for d in destinations:
            path = Path(d.get("path", ""))
            try:
                path.mkdir(parents=True, exist_ok=True)
                with tempfile.NamedTemporaryFile(
                    prefix=".picker_wtest_", dir=str(path), delete=True
                ):
                    pass
            except Exception as e:
                return f"{d.get('name', path)}: {e}"
        return None

    # ------------------------------------------------------------------ actions

    def send_to(self, image_idx: int, dest_idx: int) -> str | None:
        rec = self.images[image_idx]
        dest_info = self.destinations[dest_idx]
        dest_folder = Path(dest_info["path"])

        dest_folder.mkdir(parents=True, exist_ok=True)
        # Flatten nested filename (source may be recursive) to basename at dest
        dest_path = dest_folder / Path(rec.filename).name

        if dest_path.exists():
            default_choice = settings_mod.get("conflict_default") or "ask"
            if default_choice in ("rename", "replace", "skip"):
                choice = default_choice
            elif self.conflict_handler is not None:
                choice = self.conflict_handler(rec.path, str(dest_path)) or "rename"
            else:
                choice = "rename"
            if choice == "skip" or choice == "cancel":
                return None
            if choice == "rename":
                stem = dest_path.stem
                suffix = dest_path.suffix
                counter = 1
                while dest_path.exists():
                    dest_path = dest_folder / f"{stem}_{counter}{suffix}"
                    counter += 1
            # choice == "replace" falls through — shutil overwrites the file

        try:
            file_size = os.path.getsize(rec.path)
            free_space = shutil.disk_usage(str(dest_folder)).free
            if file_size > free_space:
                return f"Not enough disk space ({file_size // (1024*1024)} MB needed, {free_space // (1024*1024)} MB free)"
        except OSError:
            pass

        try:
            if self.mode == "move":
                shutil.move(rec.path, str(dest_path))
                _journal_add(rec.path, str(dest_path))
            else:
                shutil.copy2(rec.path, str(dest_path))
        except PermissionError:
            return f"Permission denied: {dest_path.name} (file may be locked by another program)"
        except Exception as e:
            return str(e)

        entry = UndoEntry(
            action="send",
            image_idx=image_idx,
            prev_status=rec.status,
            dest_idx=dest_idx,
            dest_path=str(dest_path),
        )
        rec.status = f"dest_{dest_idx}"
        self._push_undo(entry)
        return None

    def undo(self) -> str | None:
        if not self.undo_stack:
            return "Nothing to undo."

        entry = self.undo_stack.pop()
        rec = self.images[entry.image_idx]

        if entry.action == "send":
            dest_path = Path(entry.dest_path)
            if dest_path.exists():
                try:
                    if self.mode == "move":
                        shutil.move(str(dest_path), rec.path)
                        _journal_remove(rec.path, str(dest_path))
                    else:
                        dest_path.unlink()
                except Exception as e:
                    self.undo_stack.append(entry)
                    return str(e)

        rec.status = entry.prev_status
        return None

    # ------------------------------------------------------------------ stats

    def stats(self) -> dict:
        total = len(self.images)
        unreviewed = sum(1 for r in self.images if r.status == STATUS_UNREVIEWED)
        selected = total - unreviewed
        reviewed = selected
        return {
            "total": total,
            "reviewed": reviewed,
            "selected": selected,
            "unreviewed": unreviewed,
        }

    def dest_name_for_status(self, status: str) -> str:
        if status.startswith("dest_"):
            idx = int(status.split("_")[1])
            if idx < len(self.destinations):
                return self.destinations[idx]["name"]
        return status

    # ------------------------------------------------------------------ private

    def _push_undo(self, entry: UndoEntry) -> None:
        self.undo_stack.append(entry)
        if len(self.undo_stack) > self.MAX_UNDO:
            self.undo_stack.pop(0)
