# PICker — Feature Specification

> A complete, implementation-agnostic description of **what PICker does** — every feature,
> screen, behavior, and option. No code or algorithms; this is the "what to build," not the
> "how." Use it to rebuild the app on any stack. Current version: **4.7.0**.

---

## 1. What PICker is

A **fast, local, desktop photo and video viewer / culler** for photographers who work with
thousands of files. Core promises:

- **Instant** — opening an image (even a double-click from the OS file manager) shows it on
  screen immediately; the rest of the folder loads in the background.
- **Runs on weak hardware** — large RAW files open instantly even on low-RAM machines.
- **No catalog, no cloud, no lock-in** — files stay where they are; all app data is local
  and disposable.
- **Cull fast** — review, rate, and sort images into destination folders quickly, mostly
  from the keyboard.

Single-window desktop application. Windows is the primary target (file associations, recycle
bin, taskbar integration), but nothing is conceptually Windows-only except those OS hooks.

---

## 2. Supported media

- **Images:** JPEG, PNG, TIFF, BMP, WEBP.
- **RAW:** Canon CR2/CR3, Nikon NEF, Sony ARW, Adobe DNG, Fuji RAF, Olympus ORF, Panasonic
  RW2, Pentax PEF, Samsung SRW.
- **Video:** MP4, M4V, MOV, MKV, WEBM, AVI, MTS, M2TS, WMV, 3GP, OGV.
- Video support is optional at runtime — if the video backend/tools are missing, videos
  show a placeholder but everything else still works.

---

## 3. Screens & navigation

The app has four nested screens. You move **down** by opening things and **up** with a Back
action / Escape:

```
Library home  →  Folder browser  →  Gallery (one folder's images)  →  Slideshow (one image)
```

Transitions between screens are smooth (a soft cross-fade). Animations can be turned off in
settings.

### 3.1 Library home
The landing screen. Shows:
- **Library folders** ("roots") the user has added, each as a card with a cover thumbnail,
  label, and image count. Clicking a card opens that folder.
- **Recent folders** pinned along the bottom of the window (hidden when empty).
- Buttons: **Add Folder** and **Manage** (rename a root's label, set its cover, pin/unpin,
  remove it).
- On first run, if no folders are configured, the user's Pictures folder is added
  automatically.

### 3.2 Folder browser (Picasa-style)
Browse into a folder tree. Shows:
- A **Folders** section: subfolders that contain at least one image, each as a tile with a
  cover thumbnail and a recursive image count. Empty folders are hidden. Clicking drills in.
- An **Images** section: the images directly in the current folder, laid out as a
  **justified mosaic** (variable-width tiles that keep their real aspect ratio, packed into
  centered rows).
- A clickable **breadcrumb** path, an item count, and a **Rescan** button.
- Hidden/system folders and the app's own cache folders are skipped.

### 3.3 Gallery
A justified thumbnail grid of one folder's images (the working set). Thumbnail size is
adjustable by scrolling to zoom (the chosen size is remembered). Clicking a thumbnail opens
the slideshow at that image. Right-click offers "open in external editor" and "reveal."

### 3.4 Slideshow (main viewer)
Full-screen-capable single-item viewer with a title bar (filename + position counter, a
"VIDEO" badge for videos), the image/video area, an optional info panel, a filmstrip, a
status bar, transient toast messages, and a shortcut overlay. Detailed below.

---

## 4. Viewing features (slideshow)

- **Navigate** previous/next, jump to first/last, jump ±10.
- **Zoom** centered on the cursor with the scroll wheel; zoom in/out keys; **fit to window**;
  **1:1 actual-pixels** view for focus checks.
- **Pan** by dragging when zoomed in.
- **Smooth cross-fade** between images (toggleable).
- **Rotate** the on-screen image 90° clockwise / counter-clockwise / 180° / reset. This is a
  non-destructive preview until explicitly saved.
- **Filmstrip** strip of neighbor thumbnails along the bottom; the current image stays
  centered and the strip glides smoothly as you navigate. Can be hidden. Thumbnails for
  destination-sorted images show a colored marker.
- **Info panel** (toggle): file name, size, modified date, format, full path; for images:
  resolution, megapixels, aspect ratio, orientation, any unsaved rotation, plus camera, lens,
  shutter, aperture, ISO, focal length, capture date (from EXIF); for videos: resolution,
  duration, frame rate, codec, bitrate; plus sort status when destinations are configured.
- **Toast messages** for transient feedback (e.g. "Rotated 90° clockwise", "1:1", "Saved").

### 4.1 Photographer's overlays
- **RGB histogram** with two styles (RGB curves or single luminance curve) and a
  **clipping warning** when highlights are blown. Position is configurable to any corner.
- **Focus peaking** — highlights in-focus edges with a colored overlay; sensitivity is
  adjustable.
- **EXIF overlay** — camera/lens/exposure info drawn in a configurable corner.

### 4.2 Editing
- **Crop**: drag a rectangle (with movable/resizable handles), confirm or cancel. Output is
  full-resolution.
- **Save rotation / crop to disk**: choose to overwrite the original or save as a new file
  (with a configurable filename suffix). The choice can be remembered. RAW files are never
  overwritten — edits are always written as a sibling JPEG.
- JPEG/WEBP output quality is configurable.

### 4.3 Compare mode
Side-by-side comparison of two images with **synchronized zoom and pan** (the same region of
each image stays framed). One side is "active":
- Left/right step the active side through the library; the other side stays put.
- Tab or clicking a pane switches which side is active.
- A swap action exchanges the two images.
- A per-side header shows the filename, position, and pixel dimensions; the active side is
  highlighted.
- Sync can be toggled off so each side pans/zooms independently.

### 4.4 Video playback
When the current item is a video, the viewer becomes a player with: play/pause, a
click-to-seek scrub bar with live preview, ±10 s skip, frame-step, playback speed
(0.25×–4×), loop, mute, and volume. Reaching the end can loop or advance. (The player
releases the file when leaving so the file can be moved/renamed/deleted.)

---

## 5. Culling / sorting workflow

- The user defines up to **9 destination folders**, each with a label and a color, in the
  "Sort Photos" dialog (along with the source folder).
- **File mode** is copy (keep original) or move (remove from source).
- In the slideshow, number keys send the current image to a destination; an Enter key sends
  to the "active" destination; a key cycles the active destination; and there's **undo**.
- After sending, the viewer can auto-advance to the next unreviewed image.
- A status bar shows counts: current status, selected, remaining.
- **Move/Copy to recent folders**: right-click menus offer the last few destination folders
  plus "Choose Folder…". Recent destinations are remembered.
- **Filename conflicts** on send/move/copy are handled by a configurable policy: ask, keep
  both (rename), replace, or skip. "Ask" shows a side-by-side comparison (existing vs
  incoming, with previews and file metadata) so the user can choose.
- **Delete** sends the file to the OS Recycle Bin (recoverable), not a permanent delete.
- Move operations are remembered so they can be recovered even after restarting the app.
- Read-only / locked files are handled gracefully (cleared and retried, with a clear error
  if it still fails).

---

## 6. Right-click menu (slideshow and folder mosaic share the same menu)

- **Open With** → Adobe Photoshop · Adobe Lightroom · System Default.
- **Rotate** → 90° clockwise · 90° counter-clockwise · 180° · reset.
  (In the mosaic, where there's no live preview, rotate writes to disk immediately using the
  configured save mode; selecting multiple images rotates them all.)
- **Move to** / **Copy to** → recent destination folders + "Choose Folder…".
- **Copy Path**, **Reveal in file manager**.
- In the mosaic: **Open in Slideshow**, **Select All / Clear Selection**.
- **Delete (Recycle Bin)**.
- Acts on the whole selection when multiple images are selected.

---

## 7. Multi-selection (folder mosaic)

- Plain click opens the slideshow; Ctrl+click toggles selection; Shift+click selects a range.
- Full keyboard navigation with a visible focus cursor: arrows move (up/down jump rows),
  Home/End, Shift+arrow extends the selection, Ctrl+arrow moves focus only, Space toggles,
  Enter opens, Select-All, and Clear.
- Selected tiles are visually marked (tint, border, check badge).

---

## 8. External editors

- Open the current image in **Photoshop**, **Lightroom**, or the **system default** app.
- Editor locations are auto-detected; if detection fails the user is asked to locate the
  program once, and the choice is remembered. Paths can also be set manually in settings.

---

## 9. Performance behaviors (as features)

- **Smart display resolution:** images are decoded at a chosen fraction of native size to
  save memory, **but**: small images are never shrunk, and nothing is ever decoded below the
  screen's resolution — so everything stays sharp on screen while huge RAW files still open
  fast. Default is 50%; options are 10/25/50/100%, plus a "full native resolution" override.
- **Instant open:** opening a single file shows it immediately and discovers the rest of the
  folder in the background (closest images first).
- **Background loading is viewport-driven:** only thumbnails on screen (plus a small
  look-ahead) are generated; navigating away from a big folder stops its background loading
  rather than finishing it.
- **Neighbor preloading:** a configurable number of images on each side of the current one
  are pre-decoded so navigation is instant.
- **Thumbnail cache:** generated thumbnails are cached on disk next to the photos (in a
  hidden cache folder) and reused; the cache auto-invalidates when a file changes. A cache
  size cap is configurable, and the cache can be cleared.
- A **loading screen** with progress is shown for heavy folder scans.

---

## 10. Library index & search

- The app maintains a **local metadata index** of all files under the library folders
  (dimensions, capture date, camera make/model, GPS presence, file size/date, media type,
  duration for video, plus rating and pick/reject flag).
- The index updates incrementally — unchanged files are skipped; deleted files are removed.
- It can be rescanned on demand, or automatically on launch (optional). Rescans first do a
  cheap check and only deep-scan folders that actually changed.
- The index supports **search/filter** by: folder, filename, date range, camera, minimum
  rating, pick/reject flag, file type, has-GPS, minimum dimensions, and orientation
  (landscape/portrait/square); and **sorting** by filename, date taken, modified date, size,
  rating, or random.
- The whole index can be cleared from settings.

---

## 11. Grouping & sorting (gallery/library)

- **Group images by:** none (flat), date, folder, or camera. When grouping by date, the
  granularity is year / month / day.
- **Sort order:** filename, date taken, modified date, file size, rating, or random.

---

## 12. Settings (all user options)

**Defaults**
- File mode: copy or move.
- Display resolution: 10 / 25 / 50 / 100% (default 50, smart-scaled).
- Disable resolution limit (decode at native size).

**Gallery**
- Thumbnail size (small / medium / large / extra-large).
- Group images by (none / date / folder / camera) and date granularity (day / month / year).

**Slideshow**
- Preload neighbor count (1–5).
- Auto-advance to next unreviewed after a send.
- Show filmstrip.
- Animate transitions (cross-fades, view transitions).
- Filename-conflict policy (ask / keep both / replace / skip).

**Editing**
- Save mode for edits (ask / always new file / always overwrite).
- New-file suffix.
- JPEG/WEBP quality.

**Source scanning**
- Include subfolders.
- Exclude hidden/system folders.
- Include videos.
- Sort order.
- Which file types to include.

**Cache & data**
- Thumbnail cache size cap (with a current-size readout).
- Clear thumbnail cache.
- Clear recent folders list.
- Clear all cache & database.
- Reset settings to defaults (keeps editor paths, file associations, recents).

**Appearance**
- Theme: dark or light.

**Overlays**
- EXIF overlay position; histogram position; histogram style (RGB / luminance).

**External editors**
- Photoshop path; Lightroom path (blank = auto-detect, with detected-path display).

**System integration (Windows)**
- Register PICker as an image file handler (double-click / Open With).
- Add "Browse with PICker" to folder right-click menus.

**Advanced**
- RAW decode preference (embedded JPEG = fast, or full demosaic = accurate/slow).
- Zoom wheel factor.
- Focus-peaking sensitivity.
- Verbose logging.
- Check for updates on launch.

The settings screen is organized as a **category sidebar with paged content** (General,
Gallery, Slideshow, Editing, Scanning, Integrations, Cache, Advanced).

---

## 13. System integration (Windows)

- **File associations:** register as a handler for all supported image/RAW types so
  double-click and "Open With" launch PICker (per-user, no admin needed). Reversible.
- **Folder context menu:** "Browse with PICker" on folders and folder backgrounds.
- **Single instance:** launching PICker again (e.g. opening another file) reuses the running
  window and brings it to front, rather than opening a second copy. A flag can force a new
  window.
- **Recycle Bin** for deletes (recoverable).
- Correct **taskbar icon** and grouping.
- The installer can optionally set up file associations, the context menu, a desktop icon,
  and add PICker to PATH.

---

## 14. Reliability & housekeeping

- **Crash reporting:** unexpected errors are written to a local crash report; on next launch
  the user is offered to copy the report. Diagnostic info (version, OS, library versions) can
  be copied from the Help menu.
- **Logging:** a rotating local log file is always kept; verbose console logging is optional.
- **Update check:** periodically checks for a newer release (throttled, opt-out) and, if
  found, offers to open the download page. Also available on demand.
- **Robust config:** all settings/state files are written safely so a crash mid-write can't
  corrupt them; a corrupt index is rebuilt automatically instead of crashing.
- **Portable mode:** an option (flag or marker file) makes the app store *all* of its data
  next to the executable instead of in the user profile.

---

## 15. Application data (what gets stored)

All local and disposable:
- User **settings**.
- **Library** definition: folders (with labels/covers), pinned folders, recent folders.
- **Recent source folders** (for the open dialog).
- Per-folder **last-viewed position** (so reopening a folder returns to where you were).
- The **metadata index** of all library files.
- A **move journal** (recoverable moves).
- **Update-check** timestamp.
- **Logs** and **crash reports**.
- **Thumbnail caches** next to the photos (not in the profile).

---

## 16. Keyboard shortcuts

**Global / window**
- Home (Library); Open Folder; Sort Photos; toggle theme; quit; fullscreen; show shortcuts.
- Escape goes up one level (slideshow → browser/gallery → library), exiting fullscreen first.

**Slideshow — navigation & view**
- Previous / Next; first / last; jump ±10.
- Scroll to zoom at cursor; zoom in/out; fit to window; 1:1; drag to pan.

**Slideshow — tools**
- Rotate clockwise / counter-clockwise; save rotation; crop; histogram; focus peaking;
  info panel; compare; open with system default; open-with menu; reveal in file manager;
  delete to recycle bin.

**Slideshow — sorting (when destinations exist)**
- Send to destination 1/2/3; send to active destination; cycle active destination; undo.

**Compare mode**
- Left/right move the active side; switch active side; swap the two images; zoom active;
  reset both; toggle sync; fullscreen; back.

**Video**
- Play/pause; skip ±5 s and ±10 s; frame step; speed down/up; mute; loop; jump to start/end.

**Folder mosaic**
- Ctrl+click toggle; Shift+click range; arrows move focus; Shift+arrow extend; Ctrl+arrow
  focus only; Space toggle; Enter open; select all; clear.

---

## 17. Behaviors worth getting right (acceptance criteria)

1. Double-clicking a file from the OS opens it on screen near-instantly; the folder fills in
   afterward.
2. A small image (e.g. 100×100) is shown at full quality, never downscaled.
3. Large RAW images open quickly and still look sharp on screen at default settings.
4. Navigating images feels instant (neighbors preloaded); the filmstrip glides, not snaps.
5. Rotating in the viewer is a preview until saved; saving never overwrites a RAW original.
6. Sending/moving/copying with a name clash prompts (or applies the chosen policy); moves are
   undoable and recoverable after restart; deletes go to the recycle bin.
7. Compare always shows two different images with synchronized zoom/pan.
8. Videos play with full transport controls and release their file when you leave.
9. Leaving a large folder mid-load stops background work for that folder.
10. The app never crashes on a corrupt index or a half-written config file.
11. A second launch reuses the existing window.
12. Clearing caches/index or resetting settings is available and safe (never touches photos).
13. Everything important is reachable from the keyboard.

---

*For per-release feature changes, see `CHANGELOG.md`.*
