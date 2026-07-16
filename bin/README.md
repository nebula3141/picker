# bin/ — bundled media binaries

These are the external tools PICker shells out to for **video** support:

| File | Purpose |
|---|---|
| `ffmpeg.exe` | extract a single-frame thumbnail from video files |
| `ffprobe.exe` | read video metadata (resolution, duration, fps, codec, bitrate) |
| `av*.dll`, `sw*.dll` | shared libraries the two exes are dynamically linked against — **required**; the exes won't run without them in the same folder |

The app finds them automatically here (`<repo>/bin/`) when run from source, or next to the
executable (or a `bin/` beside it) when frozen, or on `PATH`. If they're missing, PICker still
runs — it just skips video thumbnails and video metadata (a dark placeholder is shown).

## Notes
- Only **images** need nothing here; these are purely for video.
- `ffplay.exe` is intentionally **not** included (the app never uses it).
- This is a Windows (x64) FFmpeg build. For Linux, install `ffmpeg` via the package
  manager instead (see `../linux/README.md`) — these `.exe`/`.dll` files are not used there.
- **Size:** ~224 MB. These are large binaries; they are **git-ignored by default** (see
  `.gitignore`) to keep the repository small. Keep them on disk locally, or fetch a fresh
  FFmpeg build if setting up on a new machine.

## For packaged builds
A PyInstaller build should bundle **all** files in this folder together (the exes *and* the
DLLs), or place them next to the produced `.exe`, so video features work in the shipped app.
