# PICker

> **Your photos. Lightning fast. Zero bloat.**
>
> A keyboard-first photo and video viewer built for photographers who shoot thousands of files and need to review them *now* — not after a 20-minute catalog import.

[![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-blue.svg)](https://www.python.org/downloads/)
[![PyQt6](https://img.shields.io/badge/Qt-PyQt6-41cd52.svg)](https://pypi.org/project/PyQt6/)
[![Platform](https://img.shields.io/badge/platform-Windows-lightgrey.svg)](#)
[![Version](https://img.shields.io/badge/version-4.7.0-orange.svg)](#)
[![License](https://img.shields.io/badge/license-GPL--3.0-blue.svg)](LICENSE)
[![Tests](https://img.shields.io/badge/tests-98%20passing-brightgreen.svg)](#)

---

## Built for photographers, not pixel-pushers

You just came back from a 12-hour wedding shoot. 4,000 files across three cards — Canon CR3, Sony ARW, Nikon NEF, plus iPhone clips the couple texted you. Lightroom wants to import for 40 minutes. Bridge is choking on the RAW previews. Explorer can't even show you what's in the `.CR3` files.

**PICker opens the folder in under a second.** No import. No catalog. No waiting. Every image — JPEG, RAW, TIFF, video — visible immediately with keyboard-only navigation. Cull your shoot in minutes, not hours.

### Why photographers use PICker

- **Instant open** — double-click any image in Explorer and it's on screen instantly. No splash screen, no loading bar. The rest of the folder loads in the background.
- **Works on potato PCs** — smart 50% decode by default (small images stay full-res, never softer than your screen). A 60MP Canon R5 RAW file that makes Lightroom swap to disk? PICker shows it instantly. Bump to 100% only when you need to pixel-peep.
- **Every RAW format** — Canon CR2/CR3, Nikon NEF, Sony ARW, Fuji RAF, DNG, Olympus ORF, Panasonic RW2, Pentax PEF, Samsung SRW. All day one.
- **Video alongside photos** — MP4, MOV, MKV, AVI inline with your stills. Same filmstrip, same keyboard shortcuts, same cull workflow.
- **Keyboard-first culling** — `1/2/3` sorts to destinations, `Enter` sends, `Ctrl+Z` undoes, `←/→` navigates. Never touch the mouse during a review session.
- **No lock-in** — your files stay where they are. PICker reads them in place. No proprietary catalog, no sidecar litter, no database you can't migrate away from.

---

## Download

### Installer (recommended)

Download `PICker-4.7.0-setup.exe` from [Releases](https://github.com/nebula3141/picker/releases). Includes everything — Python runtime, Qt, ffmpeg for video thumbnails. One click install.

### Standalone exe

Grab `PICker-4.7.0.exe` from [Releases](https://github.com/nebula3141/picker/releases). Single file, no install. Drop it anywhere and run.

### From source

```bash
git clone https://github.com/nebula3141/picker.git
cd picker/src
pip install -r ../requirements.txt
python main.py
```

---

## Features

### Instant image viewing

Open any image from Explorer — PICker shows it **instantly**. The filmstrip loads in the background, appearing with a smooth fade once all thumbnails are ready. Navigate with arrow keys immediately; images closest to your selection load first.

### Library management

- **Multiple library roots** — add any number of folders. Home screen shows counts and covers.
- **In-memory folder cache** — first scan walks disk; subsequent visits are instant.
- **Drag-and-drop** — drop folders or individual image files onto the app.
- **Recents** — last 12 folders one click away.

### Browser

- **Justified mosaic** — variable-width tiles preserving aspect ratio. No crops, naturally balanced.
- **Ctrl + scroll zoom** — live tile-size adjustment, layout reflows on the fly.
- **Folder tree** — drill into sub-folders, breadcrumb navigation, back/forward.

### Slideshow & review

- **Keyboard-driven** — `←/→` navigate, `Z` for 1:1 focus check, `R`/`Shift+R` rotate either way, `C` crop, `O` open with Photoshop / Lightroom / system default.
- **Smart preload** — ±2 images decode in background. Navigation is instant.
- **Progressive RAW** — embedded JPEG for fast preview, full demosaic on demand.
- **Histogram overlay** — RGB additive or luminance. Clipping warning with pulsing red indicator.
- **Focus peaking** — edge-detection overlay to check sharpness without zooming.
- **Crop tool** — drag region, `Enter` to apply, save as new or overwrite.
- **Side-by-side compare** — `V` to compare current with adjacent image. Sync zoom/pan with `S`.
- **Info panel** — `I` toggles camera, lens, shutter, aperture, ISO, focal length, GPS, resolution.
- **Cross-fade animation** — smooth transitions between images (toggleable).

### Video playback

| Key | Action |
|-----|--------|
| `Space` / `K` | Play / pause |
| `, / .` | Skip ±5 seconds |
| `J / L` | Skip ±10 seconds |
| `; / '` | Frame step backward / forward |
| `[ / ]` | Speed down / up (0.25x – 4x) |
| `M` | Mute / unmute |
| `Ctrl+L` | Toggle loop |
| `Home / End` | Skip to start / end |

Full scrub bar with click-to-jump, speed dropdown, loop toggle, volume slider.

### Sort & cull workflow

- **Up to 3 destinations** — name them "Selects", "Maybe", "Reject" — whatever fits your workflow.
- **Copy or move** — non-destructive review or actual file relocation.
- **Conflict resolution** — auto-rename, replace, skip, or ask. Remembers your choice.
- **50-deep undo stack** — `Ctrl+Z` reverses any send.
- **Move journal** — moved files tracked to disk. Recoverable even after app restart.
- **Auto-advance** — after sending, jump to next unreviewed image.
- **Delete to Recycle Bin** — `Delete` key with confirmation dialog.

### Performance on any hardware

PICker is designed to run fast on modest hardware:

| Setting | Default | What it does |
|---------|---------|-------------|
| Resolution % | 50% | Decode scale hint for the slideshow. Smart: small images are never downscaled, and large files are never decoded below your screen resolution — so it always looks sharp while still saving RAM on 60MP RAW. |
| Full resolution | off | Override to native decode. For pixel-peeping or print prep. |
| Pixmap cache | 512 MB | In-memory limit. Evicts oldest images when exceeded. |
| Thumb cache | 1 GB | On-disk LRU. Evicts oldest thumbnails per source folder. |

A 3,000-image wedding shoot on a laptop with 8GB RAM and integrated graphics? PICker handles it. Lightroom won't.

### System integration (Windows)

- **File associations** — register as handler for 18 image + RAW extensions. Toggle in Settings.
- **Context menus** — "Browse with PICker" on folders, "Open with PICker" on images.
- **Single instance** — second launch sends path to existing window. `--new-window` to override.
- **Multi-monitor** — opens on the same screen as Explorer.
- **Auto-update** — checks GitHub Releases on startup (24h cache). Manual check in Help menu.
- **Portable mode** — `portable.txt` next to exe stores all data locally. Perfect for USB drives.

---

## Keyboard shortcuts

### Navigation

| Key | Action |
|-----|--------|
| `←` / `→` | Previous / Next |
| `Home` / `End` | First / Last |
| `PgUp` / `PgDn` | Jump ±10 |
| `F` / `F11` | Fullscreen |
| `Esc` | Exit fullscreen → back → close |
| `?` | Shortcut panel |

### Image tools

| Key | Action |
|-----|--------|
| Scroll | Zoom at cursor |
| `0` | Fit to window |
| `Z` | 1:1 pixel zoom |
| `R` / `Shift+R` | Rotate 90° clockwise / anticlockwise |
| `Ctrl+S` | Save rotation |
| `C` | Crop mode |
| `H` | Histogram |
| `P` | Focus peaking |
| `I` | Info panel |
| `O` | Open With (Photoshop / Lightroom / System) |
| `V` | Side-by-side compare |
| `Delete` | Move to Recycle Bin |

### Compare mode (`V`)

| Key | Action |
|-----|--------|
| `←` / `→` | Step the **active** side through the library |
| `Tab` / click | Switch which side is active |
| `X` | Swap the two images |
| Scroll / drag | Zoom / pan (synced across both sides) |
| `S` | Toggle loupe sync |
| `+` / `−` / `0` | Zoom active in / out / reset both |
| `F` · `Esc` | Fullscreen · back |

### Sort mode

| Key | Action |
|-----|--------|
| `1` / `2` / `3` | Star rating (or send to destination) |
| `Ctrl+1-3` | Send to destination (or star rating) |
| `Enter` | Send to active destination |
| `Tab` | Cycle destination |
| `Ctrl+Z` | Undo |

---

## Command-line

```
PICker.exe <path>              open file or folder
PICker.exe --version           print version
PICker.exe --reset-settings    delete settings, start fresh
PICker.exe --portable          store data next to exe
PICker.exe --log               force verbose logging
PICker.exe --new-window        bypass single-instance
```

---

## Supported formats

### Images
JPEG, PNG, TIFF, WebP, BMP

### RAW
Canon CR2/CR3, Nikon NEF, Sony ARW, Adobe DNG, Fuji RAF, Olympus ORF, Panasonic RW2, Pentax PEF, Samsung SRW

### Video
MP4, MOV, MKV, AVI, WMV, FLV, WebM, M4V, 3GP, TS

---

## Tech stack

- **Python 3.11+** — runtime
- **PyQt6 6.5+** — UI, multimedia, threading, networking
- **Pillow 10+** — EXIF reader, image fallback
- **NumPy 1.24+** — histogram and focus peaking math
- **rawpy** *(optional)* — full RAW demosaic via LibRaw
- **ffmpeg + ffprobe** *(optional)* — video thumbnails and codec metadata
- **SQLite** — library index with WAL mode and corruption recovery
- **PyInstaller** — single-exe packaging
- **Inno Setup** — Windows installer

---

## Building

### PyInstaller (standalone exe)

```bash
pip install pyinstaller
cd src
pyinstaller PICker.spec
```

Output: `dist/PICker-4.7.0.exe`. Drop `ffmpeg.exe` / `ffprobe.exe` next to spec file before building to bundle them.

### Inno Setup (installer)

```powershell
iscc installer.iss
```

Produces `installer_output/PICker-4.7.0-setup.exe` with Start Menu shortcut, file associations, context menus, and bundled ffmpeg.

---

## Testing

```bash
pip install pytest
cd src
pytest
```

98 tests across 11 files covering settings, index, media classification, library, crash reporting, logging, image manager, external editors, album scanning, and auto-updater.

---

## License

[GPL-3.0](LICENSE) — required by PyQt6's licensing terms. All bundled dependencies (Pillow, NumPy, rawpy) are compatible.

---

## Author

Built by **nebula3141** for photographers who value their time.
