# Product

<!-- impeccable:product-schema 1 -->

## Platform

desktop

Not one of the schema's `web | ios | android | adaptive` values: PICker is a
PyQt6 desktop application. Windows is the primary target (file associations,
Recycle Bin, Explorer context menu, taskbar identity); macOS and Linux are
supported, with the OS-specific glue swapped per platform. Design language is
Qt widgets — QSS for chrome, custom `paintEvent` painting for the photo grid,
filmstrip, viewer canvas and overlays. There is no HTML/CSS anywhere in the app.

## Users

Confirmed with the user: **both audiences, pro-leaning.**

- **Primary:** a photographer culling a shoot — thousands of files from one
  card, reviewed fast and mostly from the keyboard, deciding what to keep and
  where it goes. Sessions are long and repetitive; the same few actions repeat
  hundreds of times.
- **Secondary:** someone browsing their own photo folders and occasionally
  sorting them. First contact (library home, empty states, first run) should be
  friendlier than the culling surfaces, which stay dense.

## Product Purpose

A fast, local photo and video viewer and culler. It opens a folder — or a single
file double-clicked in Explorer — and shows it immediately, then lets the user
review, rate, compare and sort images into destination folders. No catalog, no
import step, no cloud, no account. All app data is local and disposable; the
photos themselves are never moved or modified unless the user asks.

Success is that the app feels instant on ordinary hardware and that a long cull
can be driven entirely from the keyboard without waiting on it.

## Positioning

Catalog-free culling. Competitors either import into a managed library (Lightroom)
or are general-purpose viewers with no sorting workflow. PICker opens whatever is
on disk right now and specialises in the *decide-and-file* loop: one keystroke
sends a photo to a destination, with undo, conflict handling and a recoverable
move journal. Nothing is hidden behind an import.

## Operating Context

Four nested screens, moving down by opening and up with Escape:

    Library home → Folder browser → Gallery / Sort → Viewer (slideshow)

- **Library home** — cards for library root folders, plus recent folders.
- **Folder browser** — folder tiles plus a justified mosaic of the photos in
  the current folder; search with folder/library scope; multi-select with a
  bulk-action bar.
- **Viewer** — full-screen-capable single item: zoom/pan, rotate, crop, compare,
  histogram, focus peaking, EXIF overlay, info panel, filmstrip, toasts, video
  transport controls.
- **Sort (two-pane)** — source mosaic and destination mosaic side by side;
  Space copies across; both panes live-update from disk.
- **Sort-Multi** — the older workflow: up to 9 destination folders on number keys.
- Dialogs: Sort-Multi setup, Settings (category sidebar + paged content),
  conflict resolution, save-mode, library manager, first-run welcome.

Work happens on real photos: RAW, HEIC/AVIF, JPEG, PNG, TIFF, WEBP, SVG and
video. The interface surrounds images constantly, so chrome must not compete
with or tint them.

## Capabilities and Constraints

- PyQt6 / Qt 6 widgets. Styling is QSS plus hand-painted widgets; there is no
  component library and no web renderer.
- Themes: dark (default) and light, swappable at runtime via `theme.apply()`;
  widgets read colours through `theme.c(key)` at paint time.
- Runs on weak hardware and must stay responsive with tens of thousands of
  photos; per-frame paint cost is a real constraint (the grid repaints on every
  scroll frame).
- Single window; no web view, no external font CDN. Fonts must be ones the OS
  already has, or shipped with the app.
- Everything important is reachable from the keyboard.

## Brand Commitments

Confirmed as binding by the user:

- **Name:** PICker. Tagline in use: "capturing moments, minus the noise."
  Author: nebula3141. Licence: GPL-3.0.
- **Accent:** `#3b82f6` stays the accent across the app and the logo.
- **Dark is the default theme**; light must remain available and usable.
- **The aperture mark** (`picker/icon.py`, `docs/logo.png`) stays untouched.

## Evidence on Hand

- The working application in `src/` and its feature spec in `APPLICATION.md`.
- `CHANGELOG.md` through v4.11.0; `PRODUCTION-ROADMAP.md` for shipped/pending work.
- Marketing site at `docs/index.html` (separate surface, not the app).
- No customers, testimonials, reviews, benchmarks or press exist. Future work
  must not invent any.

## Product Principles

1. **Instant beats informative.** Anything that delays the first paint is a bug;
   detail arrives on demand.
2. **The photo is the subject.** Chrome is the frame, never the picture — it
   must not tint, compete with, or crowd the image.
3. **The keyboard is the primary input.** Every action has a key; the UI teaches
   those keys instead of hiding them.
4. **Repetition-first.** The actions performed hundreds of times per session get
   the shortest path and the clearest feedback.
5. **Nothing is destroyed silently.** Deletes go to the Recycle Bin, moves are
   journalled, edits never overwrite a RAW.

## Accessibility & Inclusion

Existing behaviour that this and future work must preserve (the user made this
explicit for the overhaul):

- `accessibleName` on interactive widgets (Settings dialog, loading screen).
- Full keyboard navigation with a **visible focus indicator** on folder tiles,
  mosaic tiles, dialogs and buttons; arrow/Home/End/PgUp/PgDn movement, Enter to
  open, Space to toggle or copy, Escape to go up a level.
- Alt-key mnemonics on menus (`&File`, `&View`, `&Help`).
- Minimum type sizes already raised for the VIDEO badge, info panel headings and
  status legend; text must not shrink below those.
- Both themes must stay usable; contrast must meet or exceed what ships today.
- Open (not yet built, from the roadmap): a high-contrast theme option, and
  toast messages announcing via `QAccessible.updateAccessibility`.
