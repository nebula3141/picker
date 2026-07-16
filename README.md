<div align="center">

<img src="docs/logo.png" alt="PICker" width="110" height="110">

# PICker

**Your photos. Lightning fast.**

A blazing-fast photo viewer & culling tool for people who shoot *thousands* of pictures.
Opens 60 MP RAW files instantly — even on a potato PC. No catalog. No cloud. No waiting.

[![tests](https://github.com/nebula3141/picker/actions/workflows/tests.yml/badge.svg)](https://github.com/nebula3141/picker/actions/workflows/tests.yml)
[![release](https://img.shields.io/github/v/release/nebula3141/picker?color=3b82f6)](https://github.com/nebula3141/picker/releases/latest)
[![downloads](https://img.shields.io/github/downloads/nebula3141/picker/total?color=3b82f6)](https://github.com/nebula3141/picker/releases)
[![license](https://img.shields.io/badge/license-GPL--3.0-blue.svg)](LICENSE)
[![platform](https://img.shields.io/badge/platform-Windows%20%7C%20Linux-lightgrey.svg)](#-install)

[**⬇ Download**](https://github.com/nebula3141/picker/releases/latest) ·
[Website](https://nebula3141.github.io/picker/) ·
[Full feature spec](APPLICATION.md) ·
[Changelog](CHANGELOG.md)

</div>

---

## 💍 Why this exists

I got married. The photographers handed me **thousands of photos**, and every "professional"
tool I tried either wanted a subscription, a catalog import, a cloud account — or five seconds
per RAW file on my aging laptop.

So I built PICker to sort my own wedding album: something that opens the moment you
double-click a photo, flies through a folder of huge RAW files, and lets you keep/reject
images as fast as you can press a key. It's been my passion project ever since — and now
it's yours too. If it saves you an evening of album-sorting, a ⭐ makes my day.

---

## ✨ What it does

| | |
|---|---|
| ⚡ **Instant open** | Double-click any image in your file manager — it's on screen immediately. No splash, no import. The rest of the folder loads in the background. |
| 🏜️ **Runs on anything** | Smart decoding: small images stay pixel-perfect, huge RAW files are decoded just enough to look sharp on *your* screen. A 60 MP file opens instantly on 8 GB RAM. |
| 📷 **Every RAW format** | Canon CR2/CR3 · Nikon NEF · Sony ARW · DNG · Fuji RAF · Olympus ORF · Panasonic RW2 · Pentax PEF · Samsung SRW — plus JPEG, PNG, TIFF, WEBP, BMP. |
| ⌨️ **Cull at typing speed** | `1/2/3` sorts into destination folders, `Enter` sends, `Ctrl+Z` undoes, arrows navigate. Tear through a 3,000-photo shoot without touching the mouse. |
| 🔍 **Photographer's tools** | RGB histogram with clipping warning, focus peaking, 1:1 pixel zoom, crop & rotate, EXIF panel, side-by-side **compare** with synced loupe. |
| 🎞️ **Video, inline** | MP4/MOV/MKV sit right next to your stills — frame-step, scrub, loop, 0.25–4× speed, same filmstrip, same workflow. |
| 🗂️ **A library, not a catalog** | Point it at your folders. Browse Picasa-style, search by date/camera/rating, and your files never move unless *you* move them. |
| 🛟 **Safe by design** | Deletes go to the Recycle Bin. Moves are journaled and undoable — even across restarts. Name clashes show a side-by-side compare before anything is overwritten. |
| 🌗 **Modern UI** | Fluid dark interface (light theme too), smooth transitions, clean rounded menus. Feels 2026, not 2006. |

---

## 📦 Install

### Windows
| | |
|---|---|
| **Installer** | Grab [`PICker-setup.exe`](https://github.com/nebula3141/picker/releases/latest) — Start-Menu shortcut, optional file associations & "Browse with PICker", ffmpeg bundled. |
| **Portable** | Grab the single [`PICker.exe`](https://github.com/nebula3141/picker/releases/latest) — no install, drop it anywhere. Add a `portable.txt` next to it to keep *all* data beside the exe. |

### Linux
```bash
cd linux && ./setup.sh && ./run.sh        # Debian/Ubuntu one-shot (apt + venv + launch)
```
Full guide (Fedora/Arch, troubleshooting, .desktop launcher): [`linux/README.md`](linux/README.md)

### From source (any OS)
```bash
git clone https://github.com/nebula3141/picker.git && cd picker
pip install -r requirements.txt
python linux/launch.py        # cross-platform launcher — or: cd src && python main.py
```
Requires Python 3.11+. Everything optional degrades gracefully — no ffmpeg? You just lose
video thumbnails, nothing crashes.

---

## ⌨️ The keyboard is the workflow

<details>
<summary><b>Click to see all shortcuts</b></summary>

### Navigation
| Key | Action |
|-----|--------|
| `←` / `→` | Previous / Next |
| `Home` / `End` | First / Last |
| `PgUp` / `PgDn` | Jump ±10 |
| `F` / `F11` | Fullscreen |
| `Esc` | Up one level (viewer → folder → library) |
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

### Sorting
| Key | Action |
|-----|--------|
| `1` / `2` / `3` | Send to destination |
| `Enter` | Send to active destination |
| `Tab` | Cycle destination |
| `Ctrl+Z` | Undo |

### Video
| Key | Action |
|-----|--------|
| `Space` / `K` | Play / pause |
| `J` / `L` · `,` / `.` | Skip ±10 s · ±5 s |
| `;` / `'` | Frame step |
| `[` / `]` | Speed down / up |
| `M` · `Ctrl+L` | Mute · Loop |

</details>

---

## 🚀 Built for weak hardware

PICker is designed to run fast on modest machines:

| Setting | Default | What it does |
|---------|---------|-------------|
| Resolution % | 50% | Decode-scale *hint*. Smart: small images are never downscaled, and nothing is ever decoded below your screen resolution — always sharp, still fast on 60 MP RAW. |
| Full resolution | off | Native decode for pixel-peeping / print prep. |
| Pixmap cache | 512 MB | In-memory limit; oldest images evicted. |
| Thumb cache | 1 GB | On-disk, lives *next to your photos* in `.picker_cache/`, self-invalidating. |

A 3,000-image wedding shoot on a laptop with 8 GB RAM and integrated graphics? That's
literally the machine this was built on.

---

## 🧱 Lean stack, no bloat

Python 3.11+ · PyQt6 · Pillow · rawpy/LibRaw · NumPy · SQLite (WAL) · ffmpeg (optional) —
no Electron, no browser engine, no telemetry, no accounts. 98 tests run on Windows + Linux
in CI for every change.

Want to understand or rebuild it? [`APPLICATION.md`](APPLICATION.md) documents **every
feature and behavior** in the app.

---

## 🛠️ Building

```bash
# Portable one-file exe
pyinstaller PICker.spec              # → dist/PICker-<ver>.exe

# Onedir + Windows installer
pyinstaller PICker_multifile.spec    # → dist/PICker-<ver>/
iscc /DMyAppVersion=<ver> installer.iss
```
Put ffmpeg binaries in [`bin/`](bin/README.md) before building to bundle video support.

---

## 🤝 Contributing

Bug reports, features, docs, translations — all welcome. Start with
[CONTRIBUTING.md](CONTRIBUTING.md); the issue templates will ask for the info that makes
fixes fast. If PICker helped you sort an album you care about, **star the repo** — it
genuinely helps others find it.

## 📄 License

[GPL-3.0](LICENSE) — free forever. Bundled dependencies (Pillow, NumPy, rawpy) are compatible.

<div align="center">
<sub>Made with ❤️ (and one very large wedding album) by <a href="https://github.com/nebula3141">nebula3141</a></sub>
</div>
