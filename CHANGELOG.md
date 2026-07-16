# Changelog

## Unreleased

### Modern UI
- App-wide visual refresh: new accent (#3b82f6), deeper neutral dark palette, modern rounded
  menus + right-click menus, restyled menu bar / status bar / tooltips / scrollbars
- All dialogs (Settings, Sort Photos, Conflict, Save) unified on one shared modern stylesheet —
  rounded cards, roomier spacing, accent focus rings, modern toggles
- Slideshow chrome: cleaner title bar, accent filmstrip ring; library cards, loading screen and
  empty states refreshed

### Linux support
- Cross-platform launcher (`linux/launch.py`) — detects the OS on boot, writes a platform flag
  in the launching directory, and disables Windows-only integrations off-Windows
- `linux/` folder with apt setup, pip requirements, helper scripts and a full guide
- Settings hides Windows-only "System Integration" on other platforms (`picker/sysfeatures.py`)

### Behavior
- New setting: **Esc on a photo opened from Explorer** → open the folder mosaic (default) or
  close PICker (the old behavior)

### Build
- `bin/` folder holds ffmpeg/ffprobe + their DLLs; both PyInstaller specs bundle it so packaged
  builds have working video thumbnails/metadata; UPX excludes the codec DLLs
- Specs now build from the repo's `src/` layout with no hardcoded paths

### Docs
- `APPLICATION.md` — the complete feature specification
- CI workflow (Windows + Linux), CONTRIBUTING.md, issue templates

## v4.7.0 (2026-06-09)

### Right-Click Menus
- **Open With** is now a submenu — Adobe Photoshop, Adobe Lightroom, then System Default (slideshow + mosaic)
- **Rotate** submenu with verbose actions: Rotate 90° Clockwise (R), Rotate 90° Anticlockwise (Shift+R), Rotate 180°, and Reset Rotation
- The `O` quick "Open With" popup gained System Default for parity

### Compare (rewritten, more user-friendly)
- One side is "active" (accent-outlined); ←/→ steps that side through the library, the other pane stays put — no more confusing dual-cursor navigation
- Tab or clicking a pane switches the active side; **X** swaps A↔B
- Per-side header shows the file name, position (i/n) and pixel dimensions, with the active side highlighted
- `+`/`−` zoom the active side; `0` resets both; `S` toggles loupe sync with a clear status hint
- Smooth fade-in when entering compare mode

### Smart Resolution Scaling
- The display-resolution setting is now a hint, not a hard cut: small images are **never** downscaled, and large images are never decoded below your screen resolution, so a 100×100 photo stays pixel-sharp
- Default decode resolution raised from 25% to **50%** (existing users still on the old 25% default are migrated to 50%)

### Library / Startup
- Cleaner opening Library screen; recent folders moved to the bottom of the screen

### Folder Browser performance
- Large folders open faster: image-dimension reads now run on their own thread pool instead of queueing behind the heavy thumbnail decodes, so the justified layout settles quickly instead of appearing stuck
- No more runaway loading: navigating away from a big folder mid-load now stops its dimension/thumbnail reads instead of quietly finishing all of them in the background
- Thumbnails remain viewport-driven (only what's on screen, plus a small look-ahead, is decoded)
- The "Loading photos N / M" strip moved to the bottom of the window and stays out of the way while thumbnails stream in

### Settings
- Redesigned with a category **sidebar + paged content** (General, Gallery, Slideshow, Editing, Scanning, Integrations, Cache, Advanced) instead of one long scroll
- Exposed previously hidden options: **Group images by** (None / Date / Folder / Camera) and **Date granularity**
- Maintenance: **Clear all cache & database** and **Reset settings to defaults**
- Quick fade when switching pages

### UI / Animations
- Navigating between views (Library ⇄ folders ⇄ gallery) now cross-dissolves — the outgoing screen is snapshotted and melted into the incoming one instead of hard-cutting
- Slideshow cross-fade now runs through an ease-in-out curve for a more fluid dissolve
- Filmstrip glides smoothly to recenter on the current image instead of snapping
- Toasts fade and rise into place, then fade out (previously the fade-out never rendered on the child widget); all motion respects the "animate transitions" setting
- Windows taskbar now shows the PICker icon correctly (explicit AppUserModelID set before any window is created)


### Album / Mosaic View — Multi-Selection
- Marquee of selection methods: Ctrl+click toggles, Shift+click ranges, plain click still opens slideshow
- Full keyboard navigation with a focus cursor (white dashed ring):
  - Arrow keys move the cursor (Up/Down jump rows by x-position), Home/End to ends
  - Shift+arrow extends the selection from the anchor, Ctrl+arrow moves focus only
  - Space toggles the focused tile, Enter opens it, Ctrl+A / Esc select-all / clear
  - Focused tile auto-scrolls into view
- Selected tiles show a tint, accent border and check badge

### Right-Click Menus (everywhere, with icons)
- Mosaic tiles and the slideshow image view now share a full, icon-tagged menu
- Move to / Copy to submenus listing the last 3 destinations + "Choose Folder…"
- Open with Photoshop / Lightroom / System Default, Copy Path, Reveal in Explorer
- Slideshow menu adds Rotate, Zoom 1:1, Crop, Histogram, Focus Peaking, Compare, Info, Fullscreen, Send-to destinations
- 12 hand-drawn vector glyph icons (cached) for menu actions

### File Operations
- Move/copy through the existing side-by-side ConflictDialog (honours the `conflict_default` setting: ask / rename / replace / skip; cancel aborts the batch)
- Read-only / WinError 5 handling: clears the read-only attribute and retries, with a clear "file is read-only or open in another program" message on real failures
- Incremental view updates — moving/deleting N of M files drops only those tiles and remaps cached thumbnails in place (no full folder rescan, no re-decode)
- Last-3 destination folders remembered across sessions (`recent_target_folders`)

### Editors
- First use of Open with Photoshop/Lightroom prompts for the executable if auto-detection fails, then remembers it

### Performance
- Settings cached in memory (was re-reading + re-parsing settings.json on every `get()`)
- Stale thumbnail/header worker callbacks are now ignored after tiles are removed/remapped

### Logging
- Timing logged for folder open/scan/render, image decode, and move/copy/delete operations (always written to picker.log)

## v4.6.0 (2026-06-01)

### Instant Open
- Double-click any image in Explorer — visible on screen instantly, no loading screen
- Filmstrip loads in background, fades in when all thumbnails ready
- Navigate with arrow keys immediately during background scan
- Images closest to selection load first (closest-first discovery)
- Subtle progress bar at bottom during folder scan

### Video Playback
- Skip ±10s with J/L keys (YouTube-style)
- Frame step forward/backward with ;/' keys
- Speed cycling with [ / ] keys (0.25x – 4x, 9 steps)
- Loop toggle (Ctrl+L) with visual indicator
- Skip to start/end with Home/End
- Skip ±10s buttons in toolbar

### Production Hardening
- GPL-3.0 license (required by PyQt6)
- Silent exception audit: 8 dangerous `except: pass` blocks now log errors
- Move journal: moved files tracked to disk, recoverable after restart
- Delete key: confirmation dialog, moves to Windows Recycle Bin
- Manual update check now runs in background thread (no UI freeze)
- Large video warning toast for files >4GB

### System Integration
- Directory background right-click: "Browse with PICker"
- SystemFileAssociations: "Open with PICker" on any image type
- Multi-monitor: opens on same screen as Explorer
- Window title shows current filename
- Drag-drop image files (not just folders) onto app
- ESC behavior: fullscreen → maximized → close (Explorer) / back (browser)
- Inno Setup installer with file association tasks, context menu tasks, Add to PATH, license page

### Settings
- Added: Auto-scan on launch, Scan recursive, Sort order, Enable logging, Check for updates
- All settings now exposed in Settings dialog

### Auto-Update
- Checks GitHub Releases API on startup (24h cache)
- "Check for Updates" in Help menu
- Background thread — no UI freeze

### Portable Mode
- `portable.txt` or `.portable` marker file detection
- `--portable` CLI flag wired to config system
- All data paths (settings, library, index, logs, crash) respect portable mode

### Tests
- 98 tests across 11 files
- New: test_image_manager (13), test_external (6), test_album (10), test_updater (7)

### Accessibility
- Accessible names on 35+ interactive widgets
- QTranslator infrastructure for future i18n

### Packaging
- pyproject.toml with PEP 621 metadata, GPL-3.0 SPDX
- Inno Setup installer script with all integration tasks
- GitHub Pages website (docs/)
