# PICker on macOS

PICker runs on macOS (Apple Silicon and Intel). Two ways to use it:

## A. Download the app (easiest)

Grab **`PICker-<version>-macOS.dmg`** from the
[Releases page](https://github.com/nebula3141/picker/releases), open it, and drag
**PICker** to Applications.

> The app is **not notarized** by Apple. On first launch, right-click
> **PICker.app → Open** (or *System Settings → Privacy & Security → Open Anyway*)
> to bypass Gatekeeper. This is a one-time step.

## B. Run from source

```bash
git clone https://github.com/nebula3141/picker.git
cd picker/mac
./setup.sh      # ffmpeg via Homebrew + a venv with the Python deps
./run.sh        # launch  (pass a file/folder to open it: ./run.sh ~/Pictures)
```

Requirements: macOS 12+, Python 3.10+, and [Homebrew](https://brew.sh) (for
`ffmpeg`; images work without it — only video thumbnails/playback need it).

## What's macOS-native

- **Delete → macOS Trash** (recoverable), not a permanent delete.
- **Reveal in Finder** (`open -R`) on the right-click menu.
- **Open With** finds Adobe Photoshop / Lightroom in `/Applications`, and opens
  files with `open -a`.
- Settings, caches and crash logs live under
  `~/Library/Application Support/PICker/`.
- HEIC/AVIF (iPhone photos), animated GIF/WEBP, RAW, and SVG all supported.

## Building the .app yourself

```bash
source ../.venv/bin/activate
pip install pyinstaller
pyinstaller --noconfirm ../PICker_mac.spec     # → dist/PICker.app
```

CI also builds it automatically on every tagged release (see
`.github/workflows/`).
