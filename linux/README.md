# Running PICker on Linux

PICker is a PyQt6 desktop app. It was built and shipped for Windows, but it runs on Linux
from source — **there are no hard blockers.** This guide covers every command needed, and the
handful of Windows-only features that degrade on Linux.

- **Tested target:** Ubuntu / Debian (and derivatives — Mint, Pop!_OS). Fedora/Arch notes at
  the bottom.
- **Python:** 3.11 or newer (the code uses 3.11+ syntax).
- **Display:** X11 or Wayland desktop session.

---

## TL;DR (copy-paste)

```bash
# 1. System libraries (Qt runtime, video, ffmpeg)
sudo apt update
sudo apt install -y \
  python3 python3-venv python3-pip \
  libgl1 libegl1 libxkbcommon0 libdbus-1-3 libfontconfig1 \
  libxcb-cursor0 libxcb-icccm4 libxcb-image0 libxcb-keysyms1 \
  libxcb-randr0 libxcb-render-util0 libxcb-shape0 libxcb-xinerama0 \
  ffmpeg \
  gstreamer1.0-plugins-base gstreamer1.0-plugins-good \
  gstreamer1.0-plugins-bad gstreamer1.0-plugins-ugly gstreamer1.0-libav

# 2. Python deps in a virtualenv (run from this linux/ folder)
python3 -m venv ../.venv
source ../.venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt

# 3. Run (use the cross-platform launcher)
cd ..
python3 linux/launch.py
```

Or use the helper scripts in this folder:

```bash
chmod +x setup.sh run.sh
./setup.sh      # installs apt packages + creates venv + pip install
./run.sh        # activates venv and launches via the cross-platform launcher
```

### The cross-platform launcher (`linux/launch.py`)

`launch.py` runs on **both Windows and Linux** (and macOS). On boot it:

1. **Detects the OS** and **writes a flag file** — `linux/platform.flag` — in the launching
   directory, recording the platform (e.g. `"platform": "linux"`) and a little context.
2. **Disables Windows-only features on non-Windows** by exporting
   `PICKER_DISABLE_WINDOWS_FEATURES=1` (and `PICKER_PLATFORM=linux`). The app reads this and
   skips **file associations**, the **shell context menu**, and the taskbar AppUserModelID —
   nothing else changes. On Windows the launcher sets no such flag, so the app behaves
   exactly as before.
3. **Starts the app** in-process (single-instance IPC, exit codes, and CLI args all behave
   like a normal launch), forwarding any file/folder argument:

```bash
python3 linux/launch.py /path/to/photos
```

You can still run `src/main.py` directly — on Linux it already guards every Windows-only call
— but the launcher is the recommended entry point because it sets the platform flag and
guarantees the Windows integrations are off.

---

## What you're installing and why

### A. System packages (apt)

PyQt6's pip wheel ships Qt6's own `.so` files, but Qt still **dlopen**s system libraries at
runtime. These are the ones it needs:

**Required — GUI won't start without them**

| Package | Why |
|---|---|
| `python3`, `python3-venv`, `python3-pip` | Python + virtualenv + pip |
| `libgl1`, `libegl1` | OpenGL/EGL — Qt rendering |
| `libxkbcommon0` | keyboard handling |
| `libdbus-1-3` | desktop integration |
| `libfontconfig1` | font lookup |
| `libxcb-cursor0` | **the famous one** — Qt 6.5+ refuses to start the `xcb` platform plugin without it ("Could not load the Qt platform plugin xcb") |
| `libxcb-icccm4`, `libxcb-image0`, `libxcb-keysyms1`, `libxcb-randr0`, `libxcb-render-util0`, `libxcb-shape0`, `libxcb-xinerama0` | the rest of the X11 (`xcb`) platform-plugin dependencies |

**For in-app video playback** (Qt Multimedia uses GStreamer on Linux)

| Package | Why |
|---|---|
| `gstreamer1.0-plugins-base` / `-good` / `-bad` / `-ugly` | codecs & elements |
| `gstreamer1.0-libav` | H.264 / HEVC / most camera video |

> Without GStreamer, the app still runs — videos just show a placeholder instead of playing.

**For video thumbnails & metadata**

| Package | Why |
|---|---|
| `ffmpeg` | provides `ffmpeg` + `ffprobe` on `PATH`. PICker shells out to them to make video thumbnails and read codec/duration/fps. Without it, video tiles get a dark placeholder. |

### B. Python packages (pip → `requirements.txt`)

| Package | Role | If missing |
|---|---|---|
| `PyQt6` | the whole GUI (incl. Network for single-instance IPC, Multimedia for video) | app won't start |
| `Pillow` | EXIF | EXIF panel/overlay empty |
| `numpy` | histogram, focus peaking, RAW array conversion | those features disabled |
| `rawpy` | RAW decode (bundles LibRaw in the wheel) | RAW files show a placeholder |

---

## Step-by-step (manual)

### 1. Get the code
Clone or copy the repository so you have the `src/` folder (this `linux/` folder sits next to
it).

### 2. Install system packages
Run the `apt install` block from the TL;DR.

### 3. Create a virtual environment & install Python deps
```bash
cd linux
python3 -m venv ../.venv
source ../.venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt
```

### 4. Run
```bash
cd ../src
python main.py
```
- Open a folder or pass a file/folder directly: `python main.py /path/to/photos`
- Useful flags: `--portable` (store all data next to the code), `--log` (verbose logging),
  `--new-window`, `--reset-settings`, `--version`.

### 5. (Optional) Make it launchable from your app menu
Create `~/.local/share/applications/picker.desktop`:
```ini
[Desktop Entry]
Type=Application
Name=PICker
Comment=Fast photo viewer & culler
Exec=/ABSOLUTE/PATH/TO/.venv/bin/python /ABSOLUTE/PATH/TO/src/main.py %F
Icon=/ABSOLUTE/PATH/TO/icon.ico
Terminal=false
Categories=Graphics;Photography;Viewer;
MimeType=image/jpeg;image/png;image/tiff;image/bmp;image/webp;
```
Then `update-desktop-database ~/.local/share/applications` (optional).

---

## Is there anything blocking Linux? (short answer: no)

The app **starts and core features work**: library, folder browser, gallery, slideshow,
zoom/pan/rotate/crop, histogram, focus peaking, EXIF, compare, copy/move/sort to
destinations, thumbnails, RAW, search/index, dark/light theme, single-instance.

The following are **Windows-only integrations** that don't apply or silently no-op on Linux.
None stop the app from running, but you should know about them:

| Feature | On Linux | Severity |
|---|---|---|
| **Delete → Recycle Bin** | Falls back to a **permanent delete** (`unlink`). There is no Trash integration. | ⚠️ **Data-safety** — see below |
| **Reveal in file manager** ("Reveal in Explorer", filmstrip right-click, Help → Open Log Folder) | Calls Windows `explorer`; fails silently (does nothing) | Minor |
| **Open With → Photoshop / Lightroom** | Auto-detection uses the Windows registry → returns "not found". (Those apps don't exist on Linux anyway.) **Open With → System Default works** (uses `xdg-open`). | Minor |
| **Register as image handler / "Browse with PICker" context menu** | **Hidden** when launched via `launch.py` on Linux — the Settings → Integrations page only shows the external-editor fields, no dead toggles | By design |
| **Taskbar icon grouping** (AppUserModelID) | Windows-only call; skipped | None |
| **Installer / packaged .exe** | N/A — run from source (or build your own bundle with PyInstaller) | None |

### ⚠️ The one thing to watch: Delete is permanent on Linux

On Windows, **Delete** sends files to the Recycle Bin (recoverable). On Linux the current
build falls back to a **permanent** delete. Until that's patched, treat the Delete key as
*permanent* — prefer **Move** to a "rejects" folder for culling.

> Want me to make Delete use the real Linux Trash (via `gio trash` / `trash-cli`)? It's a
> small, low-risk change to one file (`_recycle.py`). Just ask and I'll patch it.

---

## Troubleshooting

**`qt.qpa.plugin: Could not load the Qt platform plugin "xcb"`**
Install the xcb libs (especially `libxcb-cursor0`). To see what's missing:
```bash
QT_DEBUG_PLUGINS=1 python main.py
```

**Window doesn't appear on Wayland, or looks wrong**
Force X11 (XWayland), which is the most-tested path:
```bash
QT_QPA_PLATFORM=xcb python main.py
```
Most Wayland sessions ship XWayland by default.

**Videos don't play (image works, video is blank)**
Install the GStreamer plugins (`gstreamer1.0-*` incl. `gstreamer1.0-libav`). Verify ffmpeg is
present for thumbnails: `ffprobe -version`.

**RAW files show a placeholder**
`pip install rawpy` inside the venv. If the wheel fails to build on an old distro, install
LibRaw headers first: `sudo apt install -y libraw-dev` then `pip install --no-binary :all: rawpy`.

**`python: command not found`**
Use `python3` (and `python3 -m venv`).

---

## Where PICker stores its data on Linux

All local and disposable:
- Config, library, index DB, logs, crash reports: `~/.config/PICker/`
- Thumbnail caches: a hidden `.picker_cache/` folder **next to your photos**
- Portable mode (`--portable` or a `portable.txt`/`.portable` marker next to the code) puts
  **everything** under `<code-dir>/data/` instead.

To wipe and start fresh: delete `~/.config/PICker/` (or use Settings → **Clear all cache &
database** / **Reset settings to defaults**).

---

## Other distros (quick notes)

**Fedora**
```bash
sudo dnf install -y python3 python3-pip \
  mesa-libGL mesa-libEGL libxkbcommon xcb-util-cursor \
  xcb-util-image xcb-util-keysyms xcb-util-renderutil xcb-util-wm \
  fontconfig dbus-libs ffmpeg \
  gstreamer1-plugins-base gstreamer1-plugins-good \
  gstreamer1-plugins-bad-free gstreamer1-libav
```
(then the same venv + `pip install -r requirements.txt`)

**Arch / Manjaro**
```bash
sudo pacman -S --needed python python-pip libgl libxkbcommon xcb-util-cursor \
  fontconfig dbus ffmpeg gst-plugins-base gst-plugins-good gst-plugins-bad \
  gst-plugins-ugly gst-libav
```

---

*See the repo root `APPLICATION.md` for the full feature list, and `CHANGELOG.md` for version
history.*
