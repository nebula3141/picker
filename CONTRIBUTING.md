# Contributing to PICker

Thanks for your interest! PICker started as a personal project to cull a wedding album and
grew into a full photo-culling tool — contributions of every size are welcome: bug reports,
docs, translations, features.

## Dev setup

```bash
git clone https://github.com/nebula3141/picker.git
cd picker
python -m venv .venv
# Windows: .venv\Scripts\activate     Linux/macOS: source .venv/bin/activate
pip install -r requirements.txt pytest
```

Run the app from source:

```bash
cd src
python main.py            # logging is on automatically for dev runs
```

On Linux, see [`linux/README.md`](linux/README.md) for the system packages
(Qt xcb libs, ffmpeg, GStreamer) and helper scripts.

## Running tests

```bash
cd src
pytest        # 98 tests; QT_QPA_PLATFORM=offscreen works for headless runs
```

CI runs the suite on Windows and Linux for every PR — please make sure it's green.

## Project layout (30-second tour)

| Path | What it is |
|---|---|
| `src/main.py` | entry point, `MainWindow`, view switching, single-instance IPC |
| `src/picker/slideshow_view.py` | the viewer: canvas, zoom, overlays, filmstrip, compare, video |
| `src/picker/album_browser_view.py` | folder tree + justified image mosaic |
| `src/picker/gallery_view.py` | justified thumbnail grid for one folder |
| `src/picker/library_view.py` | home screen (library roots + recents) |
| `src/picker/image_manager.py` | image list, sort-to-destination, undo, move journal |
| `src/picker/index.py` | SQLite metadata index + search |
| `src/picker/theme.py` | palettes + the app-wide / dialog stylesheets |
| `APPLICATION.md` | full feature spec — great orientation read |

## Guidelines

- **Match the surrounding style** — the codebase favors small modules, graceful
  degradation (`try/except` around optional deps), and atomic file writes.
- **Every optional dependency must degrade gracefully** — the app must still start
  without rawpy, Pillow, numpy, ffmpeg, or QtMultimedia.
- **Add a test** when you fix a bug in a testable module (settings, index, media,
  library, image_manager, …).
- Keep PRs focused; describe *why*, not just *what*.
- Windows-only integrations must be gated (see `src/picker/sysfeatures.py`).

## Reporting bugs

Use the bug-report issue template. Attach the log if you can:
`%APPDATA%/PICker/logs/picker.log` (Windows) or `~/.config/PICker/logs/picker.log` (Linux),
and Help → **Copy Diagnostic Info** output.

## License

By contributing you agree your work is licensed under [GPL-3.0](LICENSE), the project license.
