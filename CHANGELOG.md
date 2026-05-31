# Changelog

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
