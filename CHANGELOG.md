# Changelog

## v5.0.0 (2026-09-28)

### A new look: light, transparent, instant
- **Redesigned interface.** Chrome is now translucent glass over one ground colour,
  with hairline edges and a single blue accent. Photos are the only saturated, solid
  thing on screen.
  - Buttons are quiet glass chips; the one main action per screen (Sort, Add folder,
    Save) is a solid blue button.
  - Labels use the system's UI font in natural case. Folder and file names are shown
    exactly as they're spelled, never uppercased.
  - Counts sit in a compact readout sized to its contents.
  - Focus and selection are marked with corner brackets and outlines, never a colour
    wash over your photos.

  Keyboard navigation, focus indicators, accessible names and shortcuts all work as
  before. Every icon is drawn: the video player's emoji buttons and the library
  manager's emoji folder are gone.
- **Light and Dark modes.** Toggle with **Ctrl+T**, **View → Light Mode**, or the switch
  in **Settings → Appearance**. Switching is instant (about 25 ms): the screen you're
  on repaints in place, so you keep your folder, scroll position, selection and Sort
  counts. Text in both modes meets WCAG AA contrast (4.5:1), and a test enforces it.
  On Windows the title bar follows the mode.
- **Animation switch.** **View → Animation** (Ctrl+Shift+M) or **Settings →
  Appearance**:
  - On, screens arrive with a short fade and buttons ease into hover.
  - Off, every change is instant, including photo cross-fades.

  One switch now controls all motion. The old fullscreen-only animation checkbox has
  been folded into it.
- **Lighter transitions.** Changing screens no longer snapshots the old screen or
  re-renders the new one offscreen. A single dissolving layer does the job, or nothing
  at all with Animation off.
- **Timeline scrollbar.** In a folder, the scroll track is labelled with when the photos
  were taken. The labels adapt to the folder:
  - clock times ("12:45 AM") when everything is from one day;
  - days ("Sep 12") within a year;
  - months or years for longer spans.

  How it works:
  - **Moving along it.** Drag anywhere and the grid follows the pointer immediately;
    click and it glides there. Hover or scroll and a bubble shows the exact date and
    time.
  - **Capture times.** They come from the library index, then from each photo's EXIF
    date (read in the background), then from the file date.
  - **Speed.** Scrubbing across 3,000 photos stays under 7 ms per step. Rows you fly
    past aren't decoded until you stop.
  - **Turning it off.** It's on by default; the button at the top right, above the
    rail, switches it off.
- **Smooth scrolling.** The mouse wheel glides instead of jumping, and fast spins build
  up into one continuous motion. Touchpads are unaffected, and turning Animation off
  makes it instant.
- **The folder view fills the window.**
  - Photos run edge to edge in justified rows: each full row is scaled to exactly the
    width of the window, and every photo keeps its own proportions.
  - The gaps between photos are a 1px hairline.
  - The "PHOTOS" heading only appears when a folder mixes subfolders and photos, and
    the chrome around the grid is tighter.
- **Search** is a magnifier button that opens into the search box (Ctrl+F; Esc clears
  and closes it). A "This folder / Whole library" switch sits in the search bar with
  both options always visible, and works from the keyboard.
- **One bar along the bottom of the window.**
  - Left: where you are, clearly legible with a folder mark. It follows you into
    subfolders and shows the current photo in the viewer.
  - Right: tile size (a slider between zoom-out and zoom-in magnifiers) and the full
    screen button.
- **Full screen is your choice, and it stays.** Turn it on or off from View → Full
  Screen (F11), the button at the bottom right, or F in the viewer. Opening a photo no
  longer switches the window to full screen, and Esc in the viewer goes straight back
  to the folder instead of first dropping out of full screen. Your choice is
  remembered between launches.
- **One Sort: Multi Sort.** The two-pane Sort is gone. The Sort button in a folder (and
  File → Sort…, Ctrl+Shift+S) starts Multi Sort with that folder already filled in as
  the source.
- **See your destinations fill up while you sort.**
  - A panel beside the photo shows every destination: its key, name, live count and
    photos, newest first. A photo appears there the moment you press its number.
  - Anything sent this session has an **undo** button on hover (or right-click →
    "Send back"). It returns just that photo: a copy is deleted, a moved file goes back
    to the source.
  - Photos that were already in a destination are shown but never touched, and changes
    made to the folders outside the app appear too.
  - The panel's Undo button (and Ctrl+Z) still undoes the most recent send.
- **Sorting keys fixed, and yours to choose.**
  - In Multi Sort, pressing **1 sends the photo to the first destination**. It used to
    set a star rating instead, so sorting appeared to do nothing, and the hint bar's
    "1 Keepers 2 Maybe" was wrong.
  - Plain keys now sort, and **star rating moved to Ctrl+1–5**. Outside Multi Sort,
    1–9 send to your Selection locations (they used to be Ctrl+1–9).
  - Each destination has its own key, chosen in the Sort window. Defaults are 1, 2, 3,
    4…, and only keys the viewer doesn't already use are offered. Picking a key another
    folder has swaps the two.
- **Copy or move is chosen in the Sort window** ("When a key is pressed: Move the photo /
  Copy the photo"). It always opens on **Copy**, so nothing leaves the source unless
  you pick Move for that sort.
- The Sort window labels its columns **Shortcut key · Name · Folder**, so the key
  dropdown clearly reads as the shortcut for that folder.
- **Open a destination's photos.** Click a photo in the destinations panel (or
  right-click → Open) to see it large over the sorting view.
  - Left/Right (or A/D, the side arrows, the wheel) step through that destination, and
    Home/End jump to the ends.
  - "Send back" undoes it if it was sent this session.
  - Esc returns to sorting.
- **Copy where you are.** Click the location at the bottom left to copy the name (the
  photo's file name, or the folder's), or double-click to copy the full path. A short
  "Copied" confirmation appears, and right-click offers both.
- The folder view's top bar is quieter: Back is an icon, and the album name sits at the
  centre of the window.
- **Library cards scale their cover once** instead of on every repaint, so hover and
  focus are a plain blit.
- Fixed: a mode chosen in Settings used to take effect only after a restart.
- Fixed: Settings, Sort-Multi, the conflict dialog, the library manager, the video
  controls and the compare view had hard-coded dark colours and broke in light mode.
- Fixed: **photos squashed into the wrong shape in the folder mosaic.** Portraits shot
  on a camera or phone (stored landscape with a "rotate 90°" tag) were measured as
  landscape, and on a revisit nothing corrected it, so a folder of portraits showed
  every tile as 3:2. They're measured correctly now, and the thumbnail always settles
  a tile's shape. Tiles crop to fill rather than stretching, and wrong sizes saved by
  earlier builds are discarded.
- Fixed: **folders of SVGs (or HEIC/AVIF) showed "no photos"** for anyone whose
  settings were saved before those formats were added (4.9 and 4.10). The saved
  File types list never gained the new types. New types now switch on
  automatically; a type you've seen and turned off stays off.
- Fixed: the Sort-Multi gallery threw an error on every repaint (a drawing call mixed
  two rectangle types), so its thumbnails never appeared.
- Fixed: the toast in the viewer had no visible background, so its text floated over
  the photo.
- Fixed: combo-box arrows rendered as a flat bar; they're a drawn chevron now.

### Second speed pass — nothing loads that you can't see
- **A first visit to a big folder no longer reads every photo's header.** Opening a
  10,000-photo folder used to spend **68 seconds** reading all 10,000 image headers just
  to lay out the grid — and those reads starved the dozen thumbnails actually on screen,
  which took **3.1 s** to appear. Sizes now come from the cache instantly, unknown ones
  borrow the folder's average shape, and headers are read **only for tiles near the
  viewport**. First screen of thumbnails: **3120 ms → 28 ms**.
- **The folder index survives a restart.** The listing (names, sizes, dimensions) of the
  folders you've visited is saved to one small file and reloaded in the background at
  launch, so the first folder you open after starting the app comes from memory:
  **313 ms → 56 ms** on 10,000 photos. It's validated against the folder's modification
  time, so anything changed on disk is re-scanned instead of trusted.
- **Warm start** — the folder you were last in has its listing, sizes and first screen of
  thumbnails loaded in the background right after launch, before you ask for it.
- **The app starts ~185 ms faster** (500 ms → 316 ms): the viewer, gallery, dialogs,
  Pillow, pillow-heif and rawpy are loaded the first time they're needed rather than on
  every launch.
- **Scrolling and flicking through photos got much cheaper.** Thumbnails were being
  smooth-scaled from 360 px on *every* repaint; tile-sized copies are now kept ready to
  blit. Viewer navigation: **14 ms → 3.9 ms per key**. Returning to a big folder:
  **664 ms → 34 ms**.
- Removed repeated work on hot paths: path normalisation is memoised (it ran per tile per
  frame), file modification times are no longer re-checked on every preload, and file-type
  checks no longer build a `Path` object each time.
- **Library folder counts use an indexed range scan** instead of a `LIKE` query that
  forced a full table scan of the photo index.
- **Settings are written in the background**, debounced and flushed on exit — saving a
  preference (theme, last folder, tile size) no longer stalls the window on an fsync.

## v4.11.0 (2026-09-16)

### New: two-pane Sort
- **File → Sort…** (`Ctrl+Shift+S`, or the **⇄ Sort** button in the folder browser) opens a
  split screen: the **source** folder's mosaic on the left, the **destination** folder's on
  the right. Arrow to a photo and press **Space** to copy it (or the whole selection) across.
- The copy shows up on the right **instantly** — it reuses the source's thumbnail and shape
  while the bytes copy in the background, in the order you pressed Space.
- Source photos already in the destination get a **green tick**; pressing Space on one again
  does nothing. Name clashes with a *different* file follow your conflict setting.
- **Both panes are live**: files added, removed or renamed by anything else (Explorer, an
  import, another app) appear within a fraction of a second, without a rescan.
- **Enter** (or double-click) opens a big **preview** — ←/→ step, Space copies and moves on.
  **Tab** switches panes, **Ctrl+Z** undoes the last copy, **+/−** resize tiles, **Swap**
  exchanges the folders, **Change…** picks another folder; the last pair is remembered.
- The previous multi-destination workflow is unchanged and now called **Sort-Multi…**
  (`Ctrl+Alt+S`).

### Instant everything
- **One shared thumbnail memory** for the whole app, sized to your free RAM. A thumbnail
  decoded anywhere (folder mosaic, gallery, filmstrip, folder covers, Sort) is instantly
  available everywhere else — leaving and re-entering a folder, closing the viewer, or typing
  a search no longer re-decodes anything.
- **One prioritised decode service** replaces the private thread pools each view used to spin
  up: visible tiles jump the queue, and tiles you scroll past before their turn are simply
  never decoded.
- **Folders open without ever blocking the window** — listings run in the background, and the
  subfolders of whatever you're looking at are pre-listed, so clicking into one is usually
  rendered straight from memory. Hovering a folder pre-warms its first screen of thumbnails.
  Cached listings re-check the folder on disk and patch themselves if something changed.
- **Counting a folder counts everything beneath it once**: drilling down no longer re-walks
  the same subtree at every level.
- **Opening a photo from a big folder is ~900× faster** in the listing step (5,000 photos:
  1.4 s → 1.6 ms); the old closest-first re-insert was quadratic and posted a UI callback per
  file. The viewer now opens complete — filmstrip and counter included — reusing the listing
  the folder browser already had.
- **Viewer**: decoded full-size frames are kept in a RAM-sized cache that survives closing the
  viewer; preloading looks further ahead in the direction you're moving and cancels decodes
  you've already flicked past. Remembering your position no longer writes and fsyncs a file on
  every arrow key.
- **Library home** paints immediately: card counts and covers load in the background (covers
  were decoded at full resolution on the UI thread), and the index no longer copies its whole
  database file on every query.
- Search refilters the grid in place; scrolling, hovering and clicking only touch the rows on
  screen; window resizes re-flow folder tiles instead of rebuilding the grid.
- Faster start: numpy/rawpy and the video player load on first use; transitions are snappier;
  no more splash screen when opening a folder.
- Thumbnails read a file into memory before decoding, so the app no longer holds files open
  (which could make a delete or move in Explorer fail with "file in use").

### Fixes
- Clicking a photo in a **filtered** folder or in **whole-library search results** now opens
  that exact photo (it used to open whichever file had the same position in the unfiltered
  folder).

### macOS support
- **PICker now runs on macOS** (Apple Silicon & Intel). Qt is cross-platform; the
  OS-specific glue is now handled per-platform:
  - **Delete → macOS Trash** (recoverable via Finder), not a permanent delete.
  - **Reveal in Finder** (`open -R`); the menu label adapts per OS.
  - **Open With** finds Adobe Photoshop / Lightroom in `/Applications` and opens
    files via `open -a`.
  - Settings, caches and crash logs live under `~/Library/Application Support/PICker/`.
- **Build & distribution:** a PyInstaller `.app` spec (`PICker_mac.spec`) plus a
  GitHub Actions job that builds signed-later `.dmg`s for arm64 and x86_64 on
  every tag; a `mac/` folder with `setup.sh` / `run.sh` for running from source.
  Tests now also run on macOS in CI.

## v4.10.0 (2026-08-14)

### New features
- **HEIC / HEIF / AVIF support** — iPhone photos and modern formats open, browse,
  thumbnail and sort like any other image (via pillow-heif; bundled in installs).
- **Animated GIF / WEBP / APNG play** in the viewer instead of showing a frozen
  first frame.
- **Show on Map** — right-click a photo with GPS to open its location in your
  browser (OpenStreetMap); coordinates also appear in the Info panel.
- **Auto-advance on send** now applies to the **Selection** menu too — copy/send
  a photo and PICker jumps to the next automatically, so you can rapid-fire sort.
- **Progress + resume** — the viewer counter shows how far through you are
  (`[42 / 900 · 5%]`), and reopening a folder resumes where you left off with a
  brief "Resumed at photo X" note (Home jumps back to the first).
- **First-run welcome** — a short three-card guide to browsing, sorting, and
  shortcuts, shown once (reopen any time from Help → Getting Started).

### Keyboard access & Copy Image
- **Folder tiles are now keyboard-navigable** — arrow keys move between folders,
  **Enter** (or Space) opens the focused folder, and a clear focus ring shows
  where you are. Opening the browser puts focus on the first tile, so you can
  drive it without touching the mouse.
- **Escape now goes back one level at a time** (subfolder → parent → … → library),
  instead of jumping straight out to the library.
- **Copy Image** — a new right-click option (and **Ctrl+C**) in the image viewer,
  and in the folder browser's right-click menu, copies the picture to the
  clipboard so you can paste it straight into a document, email, or chat.

### No more redundant re-scan on Back
- Returning from the image viewer to the folder browser is now **instant** — the
  browser is kept alive while you view, so pressing **Escape** restores it exactly
  as it was (same scroll position, no rescan). It only refreshes — and only the
  one folder — when you actually moved or deleted a file in the viewer.

### Faster, streamlined loading
- **Folders now open instantly, no matter how deep the tree.** The per-subfolder
  recursive walk that used to freeze the browser (counting every image and finding
  a cover before the first paint) no longer blocks. A folder paints immediately —
  subfolders show a **"counting…"** placeholder while their real image count and
  cover **stream in from the background**, top-down, and fill the tiles in place.
- Empty subfolders are pruned once their walk finishes; the finished result is
  cached so re-visiting a folder is instant with correct counts.
- **Uses your CPU cores:** several walker threads share one ordered queue, so
  counts fill in as fast as the machine allows while still resolving the visible
  (top) folders first. Thumbnail/cover decodes run on their own pool alongside.
- **Scrolling stays smooth while a folder loads.** All background decode/scan
  threads now run at low OS priority, so the UI thread always wins the cores it
  needs to paint scroll frames — opening a folder no longer stutters the grid.
  Thread pools were also right-sized so a folder open doesn't oversubscribe the
  CPU with four competing pools.
- **Reopening a folder or gallery is now near-instant.** Image dimensions are
  remembered in a small per-folder cache, so the justified grid lays out with the
  correct shapes on the very first paint — no per-image header-read pass and no
  re-justify shuffle. A folder you've opened before (this session or a previous
  one) does **zero** dimension decodes; combined with the on-disk thumbnail cache,
  the grid is right immediately and thumbs stream straight off disk. The cache
  self-invalidates when a file changes.

### SVG
- SVG/SVGZ **thumbnails and folder covers are now crisp** — vectors rasterize up
  to thumbnail resolution instead of being decoded at their tiny intrinsic size
  and stretched.
- **Gallery layout no longer stalls behind thumbnail decodes** — dimension reads
  (which the justified layout waits on) get their own thread pool, so rows lay out
  while heavy decodes proceed separately.

### Reliability
- **Clear load errors** — if an image or video can't be opened (corrupt file,
  unsupported codec), the viewer now shows a single explanatory line instead of a
  blank grey frame.
- **Library indexing no longer freezes the window** — the optional launch scan now
  runs on a background thread.

### Fixes
- Fixed a cache bug where moving a photo out of a folder while its counts were
  still streaming could leave that folder stuck showing "counting…" on return.
- Fixed the folder browser being restored stale after a **move** (or its undo) in
  the sort workflow — it now refreshes just that folder on return.
- Fixed a blank view that could appear when opening an image from a folder that
  had just become empty.
- Animated files that can't be decoded now show the load-error line instead of
  sticking on a placeholder.

## v4.9.0 (2026-07-23)

### Search
- A **search box** in the folder browser (`Ctrl+F`, `Esc` clears) with a **scope toggle**:
  - **This folder** — instant live filename filter as you type
  - **Whole library** — press Enter to run a real search across the metadata index
- Result count in the header ("42 results in library"); a helpful empty state when nothing matches

### Destinations
- **Drag ⠿ to reorder** sort destinations — the order sets the 1 / 2 / 3 keys, and the colour
  dots and tooltips update live
- Names stay inline-editable, so renaming is just typing

### One "Selection" menu instead of Move to / Copy to
- The right-click menu now has a single **Selection** submenu holding all your saved send
  locations. The old nested "Move to" / "Copy to" submenus are gone.
- Each location shows its **name · Copy/Move · shortcut**, sends the photo **instantly** on
  click (no confirm — a toast with one-tap **Undo**, also `Ctrl+Z`), and has an **✕** to remove it
- Add as many as you like (up to 9): **"Copy to a new folder…"** / **"Move to a new folder…"**
  picks the method inline, then the folder — and remembers it
- Shortcuts: **Ctrl+Space** with a single location, **Ctrl+1…Ctrl+9** with more; listed in the
  `?` panel
- The viewer status bar always shows the current **mode + active target** (e.g. `Copy → Best`)

### Easier to learn
- Context-menu rows display their **keyboard shortcut** (Z, C, H, P, V, I, F, Del, R…)
- Proper icons for Rotate / Crop / Zoom / Info / Compare / Fullscreen / Histogram — no more
  borrowed, misleading glyphs
- Folder browser gets a real **"‹ Back"** button (label + "Back (Esc)" tooltip)
- **Bulk-action bar** in the mosaic — selecting tiles reveals Move / Copy / Delete / Clear
- Empty states now teach ("Drop images or a folder in, or press Esc to go back")

### Safer & calmer
- **Ask before deleting** setting (default on); off = straight to the Recycle Bin, no modal
- Plain-language errors — no more `WinError 5` / `QImage.save failed` leaking through
- **Cancel** button on long folder scans
- Quit only confirms when a folder is still loading
- Optional subtle accent **flash on send** (settings toggle)

### Readability
- Bigger minimum type (VIDEO badge, info-panel headings); roomier info panel and shortcut
  panel; better-spaced status legend
- Middle-ellipsis for long folder names + full-path tooltips
- Consistent thousands separators (`1,739 images`)
- Visible keyboard **focus rings** in dialogs
- Skeleton tiles use the theme placeholder colour at the correct aspect
- Window size/position remembered between runs

### SVG support
- View, browse, thumbnail and index **.svg / .svgz** files (rendered crisp at screen
  resolution); rotate/crop on an SVG writes a PNG sibling
- New **SVG** file-type toggle in Settings → Scanning

## v4.8.0 (2026-07-17)

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
