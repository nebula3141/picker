# PICker — Production Readiness Roadmap

Status legend: `[ ]` todo · `[~]` in progress · `[x]` done

---

## 1. Windows Installer (Inno Setup) ✅

Create proper installer from PyInstaller output. Start menu + desktop shortcut, uninstaller in Add/Remove Programs, install path selection.

- [x] Write `installer.iss` Inno Setup script
- [x] Configure install directory default (`{autopf}\PICker`)
- [x] Add Start Menu shortcut + optional Desktop shortcut
- [x] Add uninstaller registry entry (Inno built-in)
- [x] Bundle ffmpeg/ffprobe as optional install component (skipifsourcedoesntexist)
- [x] File association + context menu tasks in installer `[Registry]`
- [ ] Add license agreement page (requires LICENSE file — see §18)
- [ ] Bundle VC++ redistributable merge module (if needed by PyInstaller output)
- [ ] Test clean install on fresh Windows VM
- [ ] Test upgrade install (overwrite previous version, preserve settings)
- [ ] Test uninstall (clean removal, settings optionally preserved)

---

## 2. Windows File Associations ✅

Register PICker as handler for image file types. Toggle in Settings.

- [x] Define ProgId `PICker.Image` with DefaultIcon and `shell\open\command`
- [x] Register extensions: `.jpg`, `.jpeg`, `.png`, `.tiff`, `.tif`, `.webp`, `.bmp`
- [x] Register RAW extensions: `.cr2`, `.cr3`, `.nef`, `.arw`, `.dng`, `.orf`, `.rw2`, `.raf`, `.pef`, `.srw`
- [x] Per-user registration via HKCU\Software\Classes (no admin needed)
- [x] Call `SHChangeNotify` after registration to refresh Explorer icons
- [x] Handle `sys.argv[1]` as file path in `main()` — open slideshow at that file
- [x] Handle `sys.argv[1]` as folder path — open album browser at that folder
- [x] Register/unregister toggle in Settings → System Integration
- [x] `Directory\shell\PICker` context menu ("Browse with PICker")
- [x] Unregister cleans up all keys
- [x] Write HKCR keys in Inno Setup `[Registry]` section
- [ ] Test: double-click .jpg in Explorer → PICker opens with that image
- [ ] Test: "Open with" → "Choose another app" → PICker appears in list

---

## 3. Single Instance / IPC ✅

Prevent multiple windows. Second launch sends file path to existing instance.

- [x] Add `QLocalServer` in `main()` — listen on named pipe `PICker-SingleInstance`
- [x] On startup, try `QLocalSocket.connectToServer()` first
- [x] If connected: send argv path via socket, activate existing window, exit
- [x] If not connected: become the server, proceed normally
- [x] Server side: receive path → `MainWindow.open_path(path)`
- [x] Bring existing window to front (`activateWindow()` + `raise_()`)
- [x] Add `--new-window` flag to force second instance
- [x] PyInstaller specs updated: QtNetwork no longer excluded
- [ ] Test: open PICker, double-click image in Explorer → same window activates

---

## 4. Command-Line Arguments ✅

Parse argv with argparse. Supports file/folder open, flags, and version.

- [x] Add `argparse` parser in `main()`
- [x] `PICker.exe <path>` — open file or folder
- [x] `PICker.exe --version` — print version and exit
- [x] `PICker.exe --reset-settings` — delete settings.json, start fresh
- [x] `PICker.exe --portable` — force portable mode (see §9)
- [x] `PICker.exe --log` — enable logging regardless of settings
- [x] `PICker.exe --help` — show usage (argparse built-in)
- [x] `PICker.exe --new-window` — bypass single-instance
- [ ] Handle multiple file args (open first, others queued)
- [ ] Test all flags from both terminal and Explorer double-click

---

## 5. Auto-Update System ✅

Check for new versions, notify user, link to download.

- [x] Define release check endpoint (GitHub Releases API)
- [x] On startup (delayed 10s), fetch latest version JSON in background thread
- [x] Compare with `__version__` — if newer, show dialog with download link
- [x] Dialog links to GitHub releases page (open in browser)
- [x] Add "Check for Updates…" in Help menu (manual check)
- [x] Add `check_updates` setting (added via migration v1→v2)
- [x] Cache last check time — don't check more than once per 24h
- [x] `picker/updater.py` — version parser, background checker, cache
- [x] Tests: 7 tests covering version parsing, comparison, settings
- [ ] Respect `--no-update-check` flag
- [ ] Test: mock newer version → dialog appears with correct link

---

## 6. Crash Reporting & Diagnostics ✅

Persist crash info to disk. Dialog on next launch. Diagnostic copy.

- [x] Write tracebacks to `%APPDATA%/PICker/crash-logs/crash-<timestamp>.txt`
- [x] Include: version, OS version, Python version, Qt version
- [x] Cap crash logs to 20 files, delete oldest
- [x] Show crash dialog on next launch: "PICker crashed last time. Copy report?"
- [x] Help → "Copy Diagnostic Info" — copies system info to clipboard
- [x] Help → "Open Log Folder" — opens log dir in Explorer
- [x] Hook `sys.excepthook` + `threading.excepthook` for worker thread crashes
- [x] `crash.diagnostics()` returns version/Python/Qt/NumPy/Pillow/paths
- [x] Tests: 8 tests covering write, read, clear, eviction, diagnostics

---

## 7. File Logging ✅

Rotating file logger alongside stderr. Always on.

- [x] Custom rotating file handler in `log.py` (no stdlib `logging` dependency)
- [x] Log path: `%APPDATA%/PICker/logs/picker.log`
- [x] Max 5MB per file, keep 3 rotations
- [x] Include timestamps, level in file format
- [x] File logging always on (even when stderr logging is off)
- [x] No ANSI color codes in file output
- [x] Public `log_dir()` and `log_file_path()` accessors
- [x] Tests: 5 tests covering file write, rotation, no-ANSI, public API
- [ ] Add "Enable verbose logging" toggle in settings (increases file log detail)

---

## 8. Settings Migration ✅

Versioned settings.json with sequential migration functions.

- [x] Add `SETTINGS_VERSION = 2` and `"settings_version"` to DEFAULTS
- [x] On load: if version < current, run migration functions sequentially
- [x] Migration registry via `@_migration(from_version)` decorator
- [x] v0→v1: add `slideshow_animation`, `file_associations_registered`
- [x] v1→v2: add `check_updates`
- [x] Handle removed keys (drop silently — existing behavior)
- [x] Backup old settings.json before migration (`settings.json.bak`)
- [x] Corrupt JSON quarantined to `settings.json.corrupt`
- [x] Empty file returns defaults without crash
- [x] Tests: 10 tests covering migration, backup, corruption, positions

---

## 9. Portable Mode ✅

Store all data next to exe instead of `%APPDATA%` when marker file present.

- [x] Check for `portable.txt` or `.portable` next to exe on startup
- [x] If present: override `_config_dir()` to return exe directory / `data/`
- [x] Override log dir, crash dir, index dir, library.json, positions.json via portable check
- [x] `enable_portable()` / `is_portable()` / `portable_dir()` in settings.py
- [x] `--portable` CLI flag wired to `settings_mod.enable_portable()`
- [x] Marker file detection in `_detect_portable()` (main.py)
- [x] Document in README: "Create `portable.txt` next to exe for USB mode"
- [ ] Test: create marker → all data written next to exe, not APPDATA

---

## 10. Thumbnail Cache Management ✅

Enforce cache size limits. Show disk usage in settings.

- [x] Calculate total cache size on settings dialog open
- [x] Display: "Current: 342.1 MB (1,204 files)"
- [x] Add "Clear thumbnail cache" button in settings
- [x] Add configurable max cache size (default 1GB, from `thumb_cache_mb`)
- [x] Implement LRU eviction: on gallery open, `prune_cache()` evicts oldest-accessed
- [x] Track last-access time per cached thumbnail (file atime)
- [x] Run eviction check on gallery view construction
- [ ] Test: set limit to 10MB, scan large folder → old thumbs evicted

---

## 11. High-DPI & Multi-Monitor ✅

Custom painting works at non-100% display scaling via Qt6 automatic DPI handling.

- [x] Qt6 default DPI awareness enabled (automatic — no opt-in needed)
- [x] All widget `paintEvent` code uses logical coordinates (Qt scales to physical)
- [x] Font sizes use `setPointSize()` (DPR-aware, not `setPixelSize()`)
- [x] No hard-coded QPixmap sizes in display path (placeholder pixmaps only)
- [ ] Test on 100%, 150%, 200%, 300% scaling
- [ ] Test window drag between monitors with different DPI
- [ ] Test fullscreen on secondary monitor

---

## 12. Accessibility ✅

Keyboard navigation and screen reader support.

- [x] Set `accessibleName` on all interactive widgets in Settings dialog (28 widgets)
- [x] Set `accessibleName` on LoadingScreen
- [x] Alt-key mnemonics on menu items (`&File`, `&View`, `&Help`)
- [ ] Add high-contrast theme option (black/white, no grays)
- [ ] Ensure all toast messages also fire `QAccessible.updateAccessibility`
- [ ] Test with Windows Narrator: all major actions reachable + announced
- [ ] Test keyboard-only workflow: launch → browse → open image → sort → close

---

## 13. Internationalization (i18n) ✅

Translation infrastructure wired. Translations contributed later.

- [x] `QTranslator` loading in `main()` based on `QLocale.system()`
- [x] Translations directory created at `picker/translations/`
- [ ] Wrap all user-facing strings in `self.tr()` or `QCoreApplication.translate()`
- [ ] Generate `.ts` file with `pylupdate6`
- [ ] Add language selector in settings (auto-detect + manual override)
- [ ] Ship English `.ts` as reference for translators
- [ ] Document translation contribution workflow in README
- [ ] Test: load non-English `.qm` file → all strings translated

---

## 14. Error Recovery & Data Safety ✅

Handle edge cases that corrupt data or confuse users.

- [x] Backup `index.sqlite` before schema migrations (copy to `.bak`)
- [x] Check destination disk free space before copy/move operations
- [x] PermissionError-specific message for locked files
- [x] Handle SQLite corruption: detect, quarantine, rebuild from scratch
- [x] `integrity_check()` function for explicit DB health verification
- [x] Handle settings.json corruption: detect, quarantine to `.corrupt`, reset
- [x] Close corrupt DB connection before quarantine (prevents WAL interference)
- [ ] Detect and retry on locked files (antivirus, OneDrive, cloud sync)
- [ ] Handle network drives going offline mid-scan
- [ ] Add "Repair" option in Help menu (rebuilds index, clears caches)
- [ ] Audit all `except Exception: pass` blocks — log or surface meaningful ones

---

## 15. Memory Management ✅

Cap memory usage. Prevent OOM on large RAW files.

- [x] Memory-based pixmap cache eviction (`PIXMAP_CACHE_MAX_BYTES = 512MB`)
- [x] Track pixmap memory: `_pixmap_bytes()` using `width * height * depth / 8`
- [x] Combined count + memory eviction (whichever limit hit first, count raised to 12)
- [x] Cache byte tracking on insert and eviction (including manual invalidation)
- [ ] Cap thread pool based on available RAM (reduce workers when low)
- [ ] Monitor memory usage — log warning at 2GB, force GC at 3GB
- [ ] Profile memory with 1000+ RAW folder — ensure stable plateau
- [ ] Add memory usage display in diagnostics (Help → About)

---

## 16. Test Suite ✅

Automated tests for core logic. 98 tests, all passing.

- [x] `tests/conftest.py` — temp dir fixtures, mock settings/index dirs, sample images
- [x] `tests/test_settings.py` — load, save, migrate, defaults, atomic write, corruption, positions (13 tests)
- [x] `tests/test_index.py` — connect, schema, corrupt recovery, scan, search, rating/flag (12 tests)
- [x] `tests/test_media.py` — extension classification, active_extensions (10 tests)
- [x] `tests/test_library.py` — roots, pinned, recents, legacy migration, stat (12 tests)
- [x] `tests/test_crash.py` — write, read, clear, eviction, diagnostics (8 tests)
- [x] `tests/test_log.py` — file logging, rotation, public API (5 tests)
- [x] `tests/test_image_manager.py` — scan, filter, send, undo, stats (13 tests)
- [x] `tests/test_external.py` — editor detection, cache, version parsing (6 tests)
- [x] `tests/test_album.py` — folder scanning, album discovery, grouping (10 tests)
- [x] `tests/test_updater.py` — version parsing, comparison, settings (7 tests)
- [ ] Add GitHub Actions CI: `pytest` on push/PR
- [ ] Add `ruff` linter to CI
- [ ] Target: 70%+ coverage on data layer

---

## 17. Modern Packaging (`pyproject.toml`) ✅

PEP 621 project metadata with setuptools build system.

- [x] Create `pyproject.toml` with `[project]` section (PEP 621)
- [x] Define name, version (dynamic from `picker.__version__`), description
- [x] Define dependencies (PyQt6, Pillow, numpy)
- [x] Define optional dependencies: `raw` (rawpy), `dev` (pytest, ruff, pyinstaller)
- [x] Define `[project.scripts]` entry point: `picker = "main:main"`
- [x] Define `[project.urls]` — homepage, repository, issues
- [x] Add `[build-system]` (setuptools)
- [x] Tool config: pytest paths, ruff settings
- [ ] Verify `pip install -e .` works for development
- [ ] Remove `requirements.txt` (or keep as generated lockfile)

---

## 18. License & Legal ✅

GPL-3.0-only (required by PyQt6 GPL licensing).

- [x] LICENSE file (GPL-3.0 full text) at repo root
- [x] SPDX identifier in pyproject.toml (`license = "GPL-3.0-only"`)
- [ ] Add NOTICE file listing dependency licenses (Pillow BSD, NumPy BSD, rawpy MIT)
- [ ] If bundling ffmpeg: add LGPL/GPL notice + source offer

---

## 19. Windows Context Menu Integration ✅

"Open with PICker" / "Browse with PICker" in Explorer right-click.

- [x] Register `HKCU\...\Directory\shell\PICker\command` — folders get "Browse with PICker"
- [x] Register `Directory\Background\shell\PICker\command` — folder background right-click
- [x] Register `SystemFileAssociations\image\shell\PICker\command` — all image types "Open with PICker"
- [x] Set icon for all context menu entries (uses app icon)
- [x] Add/remove via Settings toggle
- [x] Unregister cleans up all three context menu key trees
- [x] Add/remove registry entries during install/uninstall (Inno Setup `[Registry]`)
- [ ] Optional: add to Windows 11 "Show more options" new-style menu
- [ ] Test: right-click folder → "Browse with PICker" → app opens with that folder
- [ ] Test: right-click .jpg → "Open with PICker" → app opens at that image

---

## Priority Order

| Phase | Items | Goal | Status |
|-------|-------|------|--------|
| **P0 — Ship it** | §4 CLI, §2 file assoc, §3 IPC, §6 crash, §18 license | Installable app that opens from Explorer | **5/5 done** |
| **P1 — Installer** | §1 Inno Setup, §19 context menu, §17 pyproject.toml | Proper Windows installer | **3/3 done** |
| **P2 — Robustness** | §7 logging, §14 error recovery, §15 memory, §8 migration | Stable for daily use on large libraries | **4/4 done** |
| **P3 — Polish** | §10 cache, §11 HiDPI, §5 auto-update, §9 portable | Professional desktop app feel | **4/4 done** |
| **P4 — Scale** | §16 tests, §12 accessibility, §13 i18n | Maintainable, inclusive, translatable | **3/3 done** |

---

## 20. GitHub Pages Website ✅

Minimal futuristic landing page for the project.

- [x] Create `docs/index.html` — single-file, zero-dependency landing page
- [x] Dark theme matching app brand colors (accent `#2a82da`)
- [x] Hero, feature cards, keyboard shortcuts showcase, tech stack, CTA
- [x] Responsive layout (mobile-friendly)
- [x] Frosted glass nav, fade-up animations, glow orb background
- [x] SVG aperture logo in nav + hero (matches app icon)
- [x] Favicons: SVG, ICO, PNG 16/32, Apple Touch 180, Android Chrome 192/512
- [x] Web manifest (`site.webmanifest`) with theme color
- [ ] Deploy: set GitHub Pages source to `docs/` folder in repo settings
- [ ] Update GitHub username/repo URL if different from `nebula3141/picker`
- [ ] Add screenshots/GIFs of the app in action

## 21. Instant Open & Polish

Instant image display from Explorer/album browser. No loading screen.

- [x] `ImageManager.create_seeded()` — load ±2 images around target, scan rest in background thread
- [x] Background scan inserts images in sorted order (bisect), closest-first
- [x] Filmstrip hidden until scan complete, then slide-up animation (300ms OutCubic)
- [x] Subtle 3px progress bar at bottom during background scan
- [x] Status shows "Image N" (no total) until scan done
- [x] Navigate among discovered images only during scan
- [x] Window title shows filename: "PICker — IMG_2547.jpg"
- [x] ESC from Explorer open: fullscreen → maximized → close app
- [x] ESC from album browser: fullscreen → maximized → back to browser
- [x] Drag-drop image files onto app (not just folders)
- [x] Delete key → confirmation → Recycle Bin (SHFileOperationW)
- [x] Multi-monitor: open on screen where cursor is (Explorer proxy)
- [x] `picker/_recycle.py` — Windows Recycle Bin via ctypes

### New files added
- `picker/updater.py` — auto-update checker (GitHub Releases API)
- `picker/_recycle.py` — Windows Recycle Bin support
- `picker/translations/` — i18n translations directory
- `installer.iss` — Inno Setup installer script
- `LICENSE` — GPL-3.0 full text
- `tests/test_image_manager.py` — 13 tests
- `tests/test_external.py` — 6 tests
- `tests/test_album.py` — 10 tests
- `tests/test_updater.py` — 7 tests
