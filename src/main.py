from __future__ import annotations

import os
import sys
from pathlib import Path

# ── Quiet Qt subsystems BEFORE any Qt module loads ────────────────────────────
# Qt 6.5+ ships with a built-in ffmpeg multimedia backend on Windows. By
# default it spews per-frame INFO/DEBUG lines to stderr and drowns our own
# log output. Switch to the OS-native Windows Media Foundation backend (no
# ffmpeg in-process; no log spam) and silence the remaining categories via
# QT_LOGGING_RULES. Both env vars MUST be set before QtCore is imported.
os.environ.setdefault("QT_MEDIA_BACKEND", "windows")
os.environ.setdefault(
    "QT_LOGGING_RULES",
    ";".join([
        "qt.multimedia.*=false",
        "qt.multimedia.ffmpeg.*=false",
        "qt.multimedia.player.*=false",
        "qt.multimedia.audiosink.*=false",
        "qt.qpa.fonts=false",
    ]),
)

from PyQt6.QtWidgets import QApplication, QMainWindow, QMessageBox, QWidget
from PyQt6.QtCore import Qt, pyqtSignal, pyqtSlot, QTimer, qInstallMessageHandler, QtMsgType, QEvent, QVariantAnimation
from PyQt6.QtGui import QAction, QColor, QDragEnterEvent, QDropEvent, QPainter


# ── Qt message handler ────────────────────────────────────────────────────────
# Even with QT_LOGGING_RULES some categories (CRITICAL / FATAL) still slip
# through. Route every Qt message into our own logger so the user sees ONE
# stream, formatted consistently, and can silence all of it with PICKER_LOG=0.

def _qt_message_handler(msg_type, _ctx, message):
    from picker import log
    text = str(message)
    # Drop the noisiest known offenders even when categories don't filter them.
    if any(s in text for s in (
        "Using Qt multimedia with FFmpeg",
        "Failed to detect FFmpeg",
        "Setting source to QUrl",
        "QMediaPlayer::setSource",
    )):
        return
    if msg_type == QtMsgType.QtFatalMsg:
        log.error("Qt fatal", text=text)
    elif msg_type == QtMsgType.QtCriticalMsg:
        log.error("Qt", text=text)
    elif msg_type == QtMsgType.QtWarningMsg:
        log.warn("Qt", text=text)
    # Qt info/debug: drop entirely (we already use our own logger).


qInstallMessageHandler(_qt_message_handler)


# ── Global exception hook ─────────────────────────────────────────────────────
# Without this, an unhandled exception in a Qt slot prints to stderr and may
# silently exit the event loop (Qt 6 abort_on_exception). Route every
# uncaught exception through our logger so users + bug reports get the trace.

def _excepthook(exc_type, exc_value, tb):
    import traceback
    from picker import log
    from picker.crash import write_crash
    text = "".join(traceback.format_exception(exc_type, exc_value, tb))
    log.error("uncaught exception", trace=text)
    write_crash(exc_type, exc_value, tb)
    sys.__excepthook__(exc_type, exc_value, tb)


sys.excepthook = _excepthook

import threading

def _thread_excepthook(args):
    from picker import log
    from picker.crash import write_crash
    import traceback
    text = "".join(traceback.format_exception(args.exc_type, args.exc_value, args.exc_traceback))
    log.error("uncaught exception in thread", thread=args.thread.name if args.thread else "?", trace=text)
    write_crash(args.exc_type, args.exc_value, args.exc_traceback)

threading.excepthook = _thread_excepthook

# Only what the first screen needs is imported here. The viewer, gallery,
# dialogs and their dependencies (Pillow, the folder browser, the loading
# screen) are pulled in the first time they're actually used — they cost ~200 ms
# of the ~320 ms that module imports used to add to every launch.
from typing import TYPE_CHECKING

from picker.icon import app_icon

if TYPE_CHECKING:   # annotations only — never imported at runtime
    from picker.album_browser_view import AlbumBrowserView
    from picker.gallery_view import GalleryView
    from picker.image_manager import ImageManager
    from picker.slideshow_view import SlideshowView
from picker import __version__
from picker import recent as recent_mod
from picker import settings as settings_mod
from picker import theme as theme_mod
from picker import motion as motion_mod
from picker import library as library_mod


class _LocationLabel(QWidget):
    """Where you are, bottom left: a folder mark and the path in the body text
    colour, middle-elided so the start and the name both stay readable.

    Click copies the name (the photo's file name, or the folder's name);
    double-click copies the full path. A short "Copied" confirmation shows in
    place, and right-click offers both as menu items."""

    def __init__(self):
        super().__init__()
        self._path = ""
        self._note = ""
        self.setMinimumWidth(120)
        self.setFixedHeight(26)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self._note_timer = QTimer(self)
        self._note_timer.setSingleShot(True)
        self._note_timer.setInterval(1400)
        self._note_timer.timeout.connect(self._clear_note)

    def set_path(self, path: str):
        path = path or ""
        if path != self._path:
            self._path = path
            self.setToolTip(f"{path}\nClick: copy the name  ·  Double-click: copy the full path"
                            if path else "")
            self.setAccessibleName(f"Location: {path}" if path else "Location")
            self.update()

    def _name(self) -> str:
        return os.path.basename(self._path.rstrip("/\\")) or self._path

    def _copy(self, text: str, note: str):
        if not text:
            return
        QApplication.clipboard().setText(text)
        self._note = note
        self._note_timer.start()
        self.update()

    def _clear_note(self):
        self._note = ""
        self.update()

    def mousePressEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton:
            self._copy(self._name(), "Copied name")
        elif e.button() == Qt.MouseButton.RightButton:
            from PyQt6.QtWidgets import QMenu
            menu = QMenu(self)
            a_name = menu.addAction(f"Copy name  ({self._name()})")
            a_full = menu.addAction("Copy full path")
            chosen = menu.exec(e.globalPosition().toPoint())
            if chosen is a_name:
                self._copy(self._name(), "Copied name")
            elif chosen is a_full:
                self._copy(self._path, "Copied full path")

    def mouseDoubleClickEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton:
            self._copy(self._path, "Copied full path")

    def paintEvent(self, _ev):
        if not self._path:
            return
        from PyQt6.QtCore import QRectF
        from PyQt6.QtGui import QFontMetrics
        from picker import instrument as inst
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        inst.draw_icon(p, "folder", QRectF(6, 5, 16, 16), theme_mod.c("accent_text"), 1.6)
        f = theme_mod.legend_font(10, bold=False, tracking=0.0)
        p.setFont(f)
        fm = QFontMetrics(f)
        note_w = 0
        if self._note:
            nf = theme_mod.legend_font(9, tracking=0.2)
            note_w = QFontMetrics(nf).horizontalAdvance(self._note) + 18
        avail = max(10, self.width() - 34 - note_w - (8 if note_w else 0))
        text = fm.elidedText(self._path, Qt.TextElideMode.ElideMiddle, avail)
        p.setPen(theme_mod.c("fg"))
        p.drawText(QRectF(28, 0, avail, self.height()),
                   Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, text)
        if self._note:
            x = 28 + fm.horizontalAdvance(text) + 10
            chip = QRectF(x, 4, note_w, self.height() - 8)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(theme_mod.ACCENT_SOLID))
            p.drawRoundedRect(chip, 4, 4)
            p.setPen(QColor(255, 255, 255))
            p.setFont(theme_mod.legend_font(9, tracking=0.2))
            p.drawText(chip, Qt.AlignmentFlag.AlignCenter, self._note)
        p.end()


class _Veil(QWidget):
    """A ground-coloured layer that dissolves off a newly shown view.

    Cheap by construction: one translucent fillRect per frame, transparent to
    the mouse, gone after the animation. The view beneath keeps its own fast
    paint path — nothing is snapshotted or re-rendered offscreen."""

    def __init__(self, target: QWidget):
        super().__init__(target)
        self._alpha = 1.0
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground, True)
        self.setGeometry(target.rect())
        target.installEventFilter(self)
        self._anim = QVariantAnimation(self)
        self._anim.setEasingCurve(motion_mod.ease())
        self._anim.valueChanged.connect(self._step)
        self._anim.finished.connect(self.deleteLater)

    def run(self, ms: int):
        self.raise_()
        self.show()
        self._anim.setDuration(ms)
        self._anim.setStartValue(1.0)
        self._anim.setEndValue(0.0)
        self._anim.start()

    def _step(self, v):
        self._alpha = float(v)
        self.update()

    def eventFilter(self, obj, ev):
        if ev.type() == QEvent.Type.Resize:
            self.setGeometry(obj.rect())
        return False

    def paintEvent(self, _ev):
        p = QPainter(self)
        col = QColor(theme_mod.c("bg"))
        col.setAlphaF(max(0.0, min(1.0, self._alpha)))
        p.fillRect(self.rect(), col)
        p.end()


class MainWindow(QMainWindow):
    # Emitted from the warm-start worker thread; being a signal on a main-thread
    # QObject, it's delivered as a queued call on the UI thread. (A
    # QTimer.singleShot(0, lambda) from a plain thread is parked in that
    # thread — which has no event loop — and never fires.)
    _warm_listing_ready = pyqtSignal(str, object)
    _update_result = pyqtSignal(object, bool)     # (release | None, manual)
    _scan_progress = pyqtSignal(int)              # from the scanner thread

    def __init__(self, manager: ImageManager | None = None, parent=None,
                 skip_library: bool = False):
        super().__init__(parent)
        self._warm_listing_ready.connect(self._warm_thumbs)
        self._update_result.connect(self._on_update_result)
        self._scan_progress.connect(self._on_scan_progress)
        self._manager: ImageManager | None = manager
        self._slideshow: SlideshowView | None = None
        self._gallery: GalleryView | None = None
        self._album_browser: AlbumBrowserView | None = None
        # When an image is opened from the folder browser, the browser is parked
        # (kept alive, not destroyed) so returning is instant — no rescan. Only
        # rebuilt if files actually changed in the viewer (_album_dirty).
        self._parked_album: AlbumBrowserView | None = None
        self._parked_source: str | None = None
        self._album_dirty: bool = False
        self._library_view = None

        self._source_folder: str | None = manager.source_folder if manager else None
        self._current_folder: str | None = None
        self._destinations: list[dict] = list(manager.destinations) if manager else []
        self._mode: str = manager.mode if manager else (settings_mod.get("default_mode") or "copy")
        self._resolution_pct: int = (
            manager.resolution_pct if manager
            else int(settings_mod.get("default_resolution_pct") or 25)
        )

        self.setWindowTitle(f"PICker {__version__}")
        self.setWindowIcon(app_icon())
        self.setMinimumSize(900, 600)
        self.setAcceptDrops(True)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setStyleSheet(theme_mod.main_window_qss())

        self._build_menu()
        self._build_status_bar()
        if self._manager is not None:
            self._build_gallery()
            self._update_status()
        elif not skip_library:
            self._show_library_view()

        self._restore_window_geometry()

        # First-run coach marks — after the window is up, once.
        from PyQt6.QtCore import QTimer
        QTimer.singleShot(400, self._maybe_show_welcome)
        # Warm the folder you were last in while the window is idle.
        QTimer.singleShot(500, self._warm_start)

    def _warm_start(self):
        """Put the last-used folder's listing, sizes and first screen of
        thumbnails in RAM before they're asked for, so opening it is instant.
        Entirely background; nothing here touches the UI until the results are
        handed back."""
        import threading
        last = settings_mod.get("last_folder") or ""
        if not last or not os.path.isdir(last):
            return

        def work():
            try:
                from picker import album_browser_view as abv
                from picker import dim_cache
                dim_cache.preload(last)
                hit = abv._cache_get(last)
                if hit is None:
                    abv._cache_put(last, *abv._list_folder(last, last))
                    hit = abv._cache_get(last)
                items = list(hit[1][:60]) if hit else []
            except Exception:
                return
            try:
                self._warm_listing_ready.emit(last, items)
            except RuntimeError:
                pass   # window already closed

        threading.Thread(target=work, daemon=True).start()

    def _warm_thumbs(self, folder: str, items):
        try:
            from picker import thumb_store
            svc = thumb_store.service()
            for it in items:
                svc.request(it.path, folder, svc.PRI_PREFETCH, it.sig)
        except Exception:
            pass

    def _maybe_show_welcome(self):
        try:
            from picker import welcome
            if welcome.should_show():
                welcome.WelcomeDialog(self).exec()
        except Exception:
            pass

    def _show_welcome(self):
        """Re-open the coach marks on demand (Help menu)."""
        from picker import welcome
        welcome.WelcomeDialog(self).exec()

    def _build_status_bar(self):
        """One slim bottom bar for the whole window: where you are on the left,
        the controls you reach for on the right (tile size, full screen). It
        stays visible in full screen so the way out is always on screen."""
        from picker import instrument as inst
        bar = self.statusBar()
        bar.setSizeGripEnabled(False)
        self._location = _LocationLabel()
        bar.addWidget(self._location, 1)
        self._zoom = inst.ZoomSlider(90, 520,
                                     int(settings_mod.get("thumbnail_row_height") or 200),
                                     step=26, label="Tile size")
        self._zoom.valueChanged.connect(self._on_zoom_slider)
        self._zoom.hide()
        bar.addPermanentWidget(self._zoom)
        self._fs_btn = inst.KeyCap("", variant="dark", icon="fullscreen")
        self._fs_btn.setFixedSize(30, 26)
        self._fs_btn.setMinimumHeight(26)
        self._fs_btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._fs_btn.clicked.connect(self._toggle_fullscreen)
        bar.addPermanentWidget(self._fs_btn)
        self._sync_fullscreen_controls()

    def set_location(self, text: str):
        if getattr(self, "_location", None) is not None:
            self._location.set_path(text)

    def _on_zoom_slider(self, value: int):
        if self._album_browser is not None and self.centralWidget() is self._album_browser:
            self._album_browser.set_tile_size(value)

    def _on_browser_zoom(self, value: int):
        self._zoom.blockSignals(True)
        self._zoom.set_value(value)
        self._zoom.blockSignals(False)

    def setCentralWidget(self, widget):          # noqa: N802 — Qt name
        super().setCentralWidget(widget)
        self._sync_status_for(widget)

    def _sync_status_for(self, widget):
        """Show the size slider only where it does something; point the
        location at whatever the new view is showing."""
        if getattr(self, "_zoom", None) is None:
            return
        is_browser = widget is not None and widget is self._album_browser
        self._zoom.setVisible(is_browser)
        if is_browser:
            self._on_browser_zoom(self._album_browser.tile_size())
            self.set_location(self._album_browser.current_path)
        elif widget is not None and widget is self._library_view:
            self.set_location("")

    def _restore_window_geometry(self):
        """Restore last window size/position, then SHOW the window.

        restoreGeometry() only sets geometry — it never shows — so the window
        must be shown explicitly or the app starts up invisible. We also verify
        the restored frame actually lands on a connected screen (a saved
        position from an unplugged monitor would otherwise be off-screen)."""
        from PyQt6.QtCore import QByteArray
        geo = settings_mod.get("window_geometry") or ""
        restored = False
        if geo:
            try:
                restored = self.restoreGeometry(QByteArray.fromHex(geo.encode("ascii")))
            except Exception:
                restored = False
        if restored and not self._geometry_on_screen():
            restored = False
        if restored:
            self.show()
        else:
            self.showMaximized()
        if settings_mod.get("fullscreen"):
            self.set_fullscreen(True, persist=False)

    def _geometry_on_screen(self) -> bool:
        """True if the window's frame intersects some available screen area."""
        try:
            app = QApplication.instance()
            frame = self.frameGeometry()
            return any(s.availableGeometry().intersects(frame) for s in app.screens())
        except Exception:
            return True

    def _save_window_geometry(self):
        try:
            geo = bytes(self.saveGeometry().toHex()).decode("ascii")
            settings_mod.set_value("window_geometry", geo)
        except Exception:
            pass

    _opened_from_explorer = False

    # ── Open from CLI / IPC ─────────────────────────────────────────────────────

    def open_path(self, path: str):
        p = Path(path).resolve()
        if p.is_dir():
            self._swap_source(str(p))
        elif p.is_file():
            self._opened_from_explorer = True
            self._instant_open_file(str(p))

    def _instant_open_file(self, file_path: str, names: list[str] | None = None):
        """Open an image instantly. The folder listing is one scandir (or reused
        from the folder browser via ``names``), so the viewer starts complete —
        filmstrip and counter included — with no background re-scan."""
        from picker import log
        folder = str(Path(file_path).parent)

        if self._slideshow:
            self._slideshow.cleanup()
            self._slideshow = None
        if self._album_browser:
            self._album_browser.cleanup()
            self._album_browser = None
        if self._gallery:
            self._gallery.cleanup()
            self._gallery = None

        self._source_folder = folder
        self._current_folder = folder

        def _on_progress(count):          # scanner thread -> queued to UI
            try:
                self._scan_progress.emit(count)
            except RuntimeError:
                pass

        def _on_complete():
            QTimer.singleShot(0, self._on_scan_complete)

        from picker.image_manager import ImageManager
        mgr, seed_idx = ImageManager.create_seeded(
            source_folder=folder,
            target_file=file_path,
            seed_count=2,
            destinations=self._destinations,
            mode=self._mode,
            resolution_pct=self._resolution_pct,
            on_progress=_on_progress,
            on_complete=_on_complete,
            names=names,
        )
        self._manager = mgr
        log.info("instant open", file=file_path, count=len(mgr.images))

        self._open_slideshow(seed_idx, filmstrip_hidden=not mgr.scan_complete)
        self._update_window_title()

        QTimer.singleShot(0, lambda: recent_mod.add(folder))
        QTimer.singleShot(0, lambda: library_mod.push_recent(folder))

    def _on_scan_progress(self, count):
        if self._slideshow:
            self._slideshow.update_scan_progress(count)

    def _on_scan_complete(self):
        if self._slideshow:
            self._slideshow.on_scan_complete()

    def _open_file_in_browser(self, file_path: str):
        """Open image from album browser — also uses instant open."""
        self._instant_open_file(file_path)

    def _location_from_viewer(self):
        sv = self._slideshow
        if sv is not None and self._manager and 0 <= sv._idx < len(self._manager.images):
            self.set_location(self._manager.images[sv._idx].path)

    def _update_window_title(self):
        if self._manager and self._slideshow and 0 <= self._slideshow._idx < len(self._manager.images):
            fname = os.path.basename(self._manager.images[self._slideshow._idx].path)
            self.setWindowTitle(f"PICker — {fname}")
        else:
            self.setWindowTitle(f"PICker {__version__}")

    # ── Drag-drop ──────────────────────────────────────────────────────────────

    def dragEnterEvent(self, event: QDragEnterEvent):
        md = event.mimeData()
        if md.hasUrls():
            for url in md.urls():
                if url.isLocalFile():
                    p = Path(url.toLocalFile())
                    if p.is_dir() or p.is_file():
                        event.acceptProposedAction()
                        return
        event.ignore()

    def dropEvent(self, event: QDropEvent):
        from picker.image_manager import SUPPORTED_EXTENSIONS
        from picker.media import VIDEO_EXTENSIONS
        all_exts = SUPPORTED_EXTENSIONS | VIDEO_EXTENSIONS
        for url in event.mimeData().urls():
            if url.isLocalFile():
                path = url.toLocalFile()
                p = Path(path)
                if p.is_dir():
                    self._swap_source(path)
                    event.acceptProposedAction()
                    return
                if p.is_file() and p.suffix.lower() in all_exts:
                    self._instant_open_file(path)
                    event.acceptProposedAction()
                    return

    def _swap_source(self, new_source: str):
        """Switch to a new source folder and show its album browser.

        No loading screen: the browser paints its chrome at once and lists the
        folder on a worker (usually from the in-memory listing cache), so there's
        nothing to wait for — the old splash + processEvents() only added delay."""
        self._discard_parked_album()
        if self._slideshow:
            self._slideshow.cleanup()
            self._slideshow = None
        if self._gallery:
            self._gallery.cleanup()
            self._gallery = None
        if self._album_browser:
            self._album_browser.cleanup()
            self._album_browser = None

        from picker import log
        log.info("opening folder", path=new_source)

        # Inherit prior destinations/mode/resolution if a manager already existed,
        # otherwise pull defaults from settings.
        if self._manager:
            self._destinations = list(self._manager.destinations)
            self._mode = self._manager.mode
            self._resolution_pct = self._manager.resolution_pct

        self._source_folder = new_source
        self._manager = None
        self._current_folder = None

        self._show_album_browser()

        # Bookkeeping (JSON writes) after the browser is up, not before.
        def _remember(src=new_source):
            recent_mod.add(src)
            library_mod.push_recent(src)
            settings_mod.set_value("last_folder", src)
        QTimer.singleShot(0, _remember)

    # ── Folder-tree browser ───────────────────────────────────────────────────

    def _discard_parked_album(self):
        """Tear down a parked (kept-alive) browser we're no longer returning to.
        It was detached via takeCentralWidget (no parent), so we must free it
        ourselves or it would leak."""
        if self._parked_album is not None:
            try:
                self._parked_album.cleanup()
            except Exception:
                pass
            self._parked_album.deleteLater()
            self._parked_album = None
            self._parked_source = None

    def _show_album_browser(self):
        if self._source_folder is None:
            self._show_library_view()
            return
        self._discard_parked_album()
        if self._slideshow:
            self._slideshow.cleanup()
            self._slideshow = None
        if self._gallery:
            self._gallery.cleanup()
            self._gallery = None
        if self._album_browser:
            self._album_browser.cleanup()
            self._album_browser = None
        self._current_folder = None
        from picker.album_browser_view import AlbumBrowserView
        self._album_browser = AlbumBrowserView(self._source_folder, self)
        self._wire_browser(self._album_browser)
        self._set_central(self._album_browser)
        # Focus a tile once shown so the grid is keyboard-drivable without a click.
        from PyQt6.QtCore import QTimer
        QTimer.singleShot(0, self._album_browser.focus_content)
        self.set_location(self._album_browser.current_path)

    def _park_album_browser(self):
        """Detach the live browser WITHOUT deleting it so returning is instant:
        setCentralWidget() would deleteLater() the outgoing central widget;
        takeCentralWidget hands ownership back so the instance stays alive."""
        if self._album_browser is not None:
            self._discard_parked_album()   # drop any earlier stale parked view
            self.takeCentralWidget()
            self._album_browser.hide()
            self._parked_album = self._album_browser
            self._parked_source = self._source_folder
            self._album_browser = None     # stop teardown paths destroying it

    @pyqtSlot(str, str)
    def _open_image_in_folder(self, folder_path: str, target: str):
        """User clicked an image in the folder browser — instant open, by path.
        The browser already holds the folder listing, so the viewer reuses it
        instead of listing (and stat-ing) the folder again."""
        if not target or not os.path.isfile(target):
            # Gone since it was listed — leave the browser exactly as it is.
            QMessageBox.information(self, "Not found",
                                    f"This file no longer exists:\n\n{target}")
            if self._album_browser is not None:
                self._album_browser.reload_current()
            return
        names = None
        if self._album_browser is not None:
            names = self._album_browser.listing_names(folder_path)

        # Now that we're committed to opening, park the live browser so Escape
        # restores it instantly (no rescan).
        self._album_dirty = False
        self._park_album_browser()

        self._opened_from_explorer = False
        self._instant_open_file(target, names=names)

    # ── Sort (Multi Sort) ───────────────────────────────────────────────────────

    def _start_sort(self, source: str | None = None):
        """Sort a folder into up to 9 destinations (number keys). From the
        folder browser the current folder is prefilled as the source."""
        if not isinstance(source, str) or not source:
            source = (self._album_browser.current_path if self._album_browser is not None
                      else None)
        self._change_source(source)

    def _build_menu(self):
        bar = self.menuBar()
        # The menu strip is part of the front panel: same legend lettering as
        # every other label on the instrument (QSS font-family does not reach
        # QMenuBar items on Windows, so set it on the widget).
        bar.setFont(theme_mod.legend_font(9, tracking=1.2))
        file_menu = bar.addMenu("&File")

        home_action = QAction("Home (Library)", self)
        home_action.setShortcut("Ctrl+H")
        home_action.triggered.connect(self._show_library_view)
        file_menu.addAction(home_action)

        open_folder_action = QAction("Open Folder…", self)
        open_folder_action.setShortcut("Ctrl+O")
        open_folder_action.triggered.connect(self._open_folder)
        file_menu.addAction(open_folder_action)

        sort_action = QAction("Sort…", self)
        sort_action.setShortcuts(["Ctrl+Shift+S", "Ctrl+Alt+S"])
        sort_action.setStatusTip("Sort a folder into up to 9 destinations with the number keys")
        sort_action.triggered.connect(lambda: self._start_sort(None))
        file_menu.addAction(sort_action)

        file_menu.addSeparator()

        lib_menu = file_menu.addMenu("Library")
        add_root_action = QAction("Add Library Folder…", self)
        add_root_action.triggered.connect(self._add_library_root)
        lib_menu.addAction(add_root_action)

        manage_roots_action = QAction("Manage Library Folders…", self)
        manage_roots_action.triggered.connect(self._manage_library_roots)
        lib_menu.addAction(manage_roots_action)

        lib_menu.addSeparator()
        rescan_action = QAction("Rescan Library", self)
        rescan_action.triggered.connect(self._rescan_library)
        lib_menu.addAction(rescan_action)

        file_menu.addSeparator()

        settings_action = QAction("Settings…", self)
        settings_action.triggered.connect(self._open_settings)
        file_menu.addAction(settings_action)

        file_menu.addSeparator()

        quit_action = QAction("&Quit", self)
        quit_action.setShortcut("Ctrl+Q")
        quit_action.triggered.connect(self.close)
        file_menu.addAction(quit_action)

        view_menu = bar.addMenu("&View")

        self._fullscreen_action = QAction("Full Screen", self, checkable=True)
        self._fullscreen_action.setShortcut("F11")
        self._fullscreen_action.setStatusTip("Fill the screen; stays on until you turn it off")
        self._fullscreen_action.triggered.connect(lambda on: self.set_fullscreen(on))
        view_menu.addAction(self._fullscreen_action)

        shortcuts_action = QAction("Show Shortcuts", self)
        shortcuts_action.setShortcut("?")
        shortcuts_action.triggered.connect(self._show_shortcuts)
        view_menu.addAction(shortcuts_action)

        view_menu.addSeparator()

        # Appearance: two switches, both live — no restart, nothing rebuilt.
        self._light_action = QAction("Light Mode", self, checkable=True)
        self._light_action.setShortcut("Ctrl+T")
        self._light_action.setStatusTip("Switch between the dark and light appearance")
        self._light_action.setChecked(theme_mod.is_light())
        self._light_action.triggered.connect(
            lambda on: self.apply_theme("light" if on else "dark"))
        view_menu.addAction(self._light_action)
        self._motion_action = QAction("Animation", self, checkable=True)
        self._motion_action.setShortcut("Ctrl+Shift+M")
        self._motion_action.setStatusTip(
            "On: short fades give the interface presence. Off: every change is instant.")
        self._motion_action.setChecked(motion_mod.enabled())
        self._motion_action.triggered.connect(self.set_motion)
        view_menu.addAction(self._motion_action)

        help_menu = bar.addMenu("&Help")
        keys_action = QAction("Keyboard Shortcuts", self)
        keys_action.triggered.connect(self._show_shortcuts)
        help_menu.addAction(keys_action)

        welcome_action = QAction("Getting Started…", self)
        welcome_action.triggered.connect(self._show_welcome)
        help_menu.addAction(welcome_action)

        help_menu.addSeparator()

        diag_action = QAction("Copy Diagnostic Info", self)
        diag_action.triggered.connect(self._copy_diagnostics)
        help_menu.addAction(diag_action)

        open_log_action = QAction("Open Log Folder", self)
        open_log_action.triggered.connect(self._open_log_folder)
        help_menu.addAction(open_log_action)

        help_menu.addSeparator()

        update_action = QAction("Check for Updates…", self)
        update_action.triggered.connect(self._check_for_updates_manual)
        help_menu.addAction(update_action)

        help_menu.addSeparator()

        about_action = QAction("About PICker", self)
        about_action.triggered.connect(self._show_about)
        help_menu.addAction(about_action)

    # ── Gallery ────────────────────────────────────────────────────────────────

    def _build_gallery(self):
        self._discard_parked_album()
        if self._gallery:
            self._gallery.cleanup()

        from picker.gallery_view import GalleryView
        self._gallery = GalleryView(self._manager)
        self._gallery.open_slideshow.connect(self._open_slideshow)
        self._set_central(self._gallery)
        self._update_status()

    def _update_status(self):
        if self._gallery:
            self.set_location(self._manager.source_folder if self._manager else "")

    # ── Slideshow ──────────────────────────────────────────────────────────────

    @pyqtSlot(int)
    def _open_slideshow(self, idx: int, filmstrip_hidden: bool = False):
        if self._slideshow:
            self._slideshow.cleanup()
            self._slideshow = None
        if self._album_browser:
            self._album_browser.cleanup()
            self._album_browser = None
        if self._gallery:
            self._gallery.cleanup()
            self._gallery = None

        from picker.slideshow_view import SlideshowView
        self._slideshow = SlideshowView(self._manager, idx, parent=self,
                                         filmstrip_hidden=filmstrip_hidden)
        self._slideshow.closed.connect(self._on_slideshow_closed)
        self._slideshow.status_changed.connect(self._on_status_changed)
        self._slideshow.fullscreen_requested.connect(self._toggle_fullscreen)
        self._slideshow.title_changed.connect(self._update_window_title)
        self._slideshow.title_changed.connect(self._location_from_viewer)
        # A move/delete in the viewer marks the parked browser stale so it
        # refreshes on return; pure navigation leaves it clean for instant restore.
        self._slideshow.files_changed.connect(self._on_viewer_files_changed)
        self.setCentralWidget(self._slideshow)
        self._slideshow.setFocus()
        if not filmstrip_hidden:
            self._fade_in(self._slideshow)
        # Full screen is the user's mode, not the viewer's: leave it as it is.
        self._slideshow.set_chrome_visible(not self.isFullScreen())
        self.set_location(self._manager.images[idx].path
                          if 0 <= idx < len(self._manager.images) else "")
        # A fade can steal focus; re-assert it next tick so
        # arrow keys work without a click. (_post_show_settle also retries.)
        from PyQt6.QtCore import QTimer
        QTimer.singleShot(0, self._focus_slideshow)

    def _focus_slideshow(self):
        if self._slideshow is not None:
            self.activateWindow()
            self._slideshow.setFocus(Qt.FocusReason.OtherFocusReason)

    def _on_viewer_files_changed(self):
        """A move/delete happened in the viewer — the parked browser is now stale."""
        self._album_dirty = True

    @pyqtSlot(int)
    def _on_slideshow_closed(self, last_idx: int):
        if self._slideshow:
            self._slideshow.cleanup()
            self._slideshow = None
        self.setWindowTitle(f"PICker {__version__}")
        if self._opened_from_explorer:
            self._opened_from_explorer = False
            self._discard_parked_album()
            # Configurable: on Esc from an Explorer-opened photo, either quit
            # (one-shot viewer) or drop into the folder's mosaic to keep browsing.
            if (settings_mod.get("explorer_escape_action") or "mosaic") == "close":
                self.close()
                return
            # else fall through → _show_album_browser() below (mosaic)
        # Fast path: restore the live browser we parked on open. No rescan when
        # the user only navigated; a cheap one-folder refresh if files changed.
        if self._parked_album is not None:
            self._album_browser = self._parked_album
            self._parked_album = None
            self._source_folder = self._parked_source
            self._parked_source = None
            self._current_folder = None
            self._album_browser.show()            # was hidden while parked
            self._set_central(self._album_browser)
            if self._album_dirty:
                self._album_dirty = False
                self._album_browser.reload_current()
            self._album_browser.focus_content()   # keyboard-ready without a click
            if self._album_browser is not None:
                self.set_location(self._album_browser.current_path)
            return
        if self._current_folder is not None and self._source_folder is not None:
            self._show_album_browser()
        else:
            self._build_gallery()
            if self._gallery:
                self._gallery.scroll_to(last_idx)
                self._update_status()

    def _fade_in(self, widget, duration: int | None = None):
        """Presence without cost: a ground-coloured veil laid over the new view
        dissolves away. The view itself paints normally underneath — no
        offscreen snapshot, no QGraphicsEffect re-rendering the whole tree."""
        ms = motion_mod.dur(motion_mod.VIEW if duration is None else duration)
        if ms <= 0:
            return
        veil = _Veil(widget)
        veil.run(ms)
        self._view_anim = veil

    def _set_central(self, widget, *, animate: bool = True):
        """Swap the central view. With Animation on, the new view arrives under
        a short dissolve; with it off, the swap is a hard cut, instantly."""
        self.setCentralWidget(widget)
        if animate:
            self._fade_in(widget)

    def _toggle_fullscreen(self):
        self.set_fullscreen(not self.isFullScreen())

    def set_fullscreen(self, on: bool, *, persist: bool = True):
        """Full screen is a mode you choose (View > Full Screen, F11, F in the
        viewer, or the button at the bottom right) and it stays until you turn
        it off — opening or closing a photo never changes it. The status bar
        stays, so the way back is always on screen; the menu bar folds away."""
        on = bool(on)
        if on != self.isFullScreen():
            if on:
                self.menuBar().hide()
                self.showFullScreen()
            else:
                self.menuBar().show()
                self.showMaximized()
        self.statusBar().show()
        if self._slideshow:
            self._slideshow.set_chrome_visible(not on)
        if persist:
            settings_mod.set_value("fullscreen", on)
        self._sync_fullscreen_controls()

    def _sync_fullscreen_controls(self):
        on = self.isFullScreen()
        if getattr(self, "_fullscreen_action", None) is not None:
            self._fullscreen_action.setChecked(on)
        if getattr(self, "_fs_btn", None) is not None:
            self._fs_btn.set_icon("exit_fullscreen" if on else "fullscreen",
                                  "Exit full screen" if on else "Full screen")
            self._fs_btn.setToolTip(("Exit full screen" if on else "Full screen") + " (F11)")

    @pyqtSlot()
    def _on_status_changed(self):
        if self._gallery:
            self._gallery.refresh_all()
            self._update_status()

    # ── Change source ──────────────────────────────────────────────────────────

    def _change_source(self, source: str | None = None):
        from picker.startup_dialog import StartupDialog
        dlg = StartupDialog(self)
        dlg.prefill({
            "source_folder": source or (self._manager.source_folder if self._manager
                                        else (self._source_folder or "")),
            "destinations": list(self._destinations),
            "mode": self._mode,
            "resolution_pct": self._resolution_pct,
        })
        if dlg.exec() != StartupDialog.DialogCode.Accepted:
            return
        data = dlg.result_data()
        if not data:
            return

        self._discard_parked_album()
        if self._slideshow:
            self._slideshow.cleanup()
            self._slideshow = None

        if self._gallery:
            self._gallery.cleanup()
        if self._album_browser:
            self._album_browser.cleanup()
            self._album_browser = None

        from picker.image_manager import ImageManager
        from picker.loading_screen import LoadingScreen
        app = QApplication.instance()
        src_label = os.path.basename(data["source_folder"].rstrip("/\\")) or data["source_folder"]
        loading = LoadingScreen(sub=f"Scanning {src_label}…", parent=self)
        loading.show()
        app.processEvents()

        def _p(done, total, current):
            if total > 0:
                loading.set_progress(done / total)
                loading.set_text(f"{done} / {total} — {current}")
            else:
                loading.set_progress(-1.0)
                loading.set_text(current)
            app.processEvents()

        self._manager = ImageManager(
            source_folder=data["source_folder"],
            destinations=data["destinations"],
            mode=data["mode"],
            resolution_pct=data["resolution_pct"],
            progress_cb=_p,
        )
        # Keep MainWindow-level state in sync with the new manager.
        self._source_folder = data["source_folder"]
        self._destinations = list(data["destinations"])
        self._mode = data["mode"]
        self._resolution_pct = data["resolution_pct"]
        self._current_folder = None
        library_mod.push_recent(data["source_folder"])
        settings_mod.set_value("last_folder", data["source_folder"])

        self._build_gallery()
        loading.close_smoothly()

    # ── Shortcuts help ─────────────────────────────────────────────────────────

    def _show_shortcuts(self):
        if self._slideshow and hasattr(self._slideshow, '_shortcut_panel'):
            self._slideshow._show_cheatsheet()
            return
        lines = [
            "── Navigation ─────────────────",
            "  Ctrl+H           Home (Library)",
            "  ESC              Up one level (slideshow → albums → library)",
            "  F / F11          Fullscreen",
            "",
            "── Albums ─────────────────────",
            "  Click tile       Open album",
            "",
            "── Gallery ────────────────────",
            "  Click            Open in the viewer",
            "  Right-click      Open in external editor",
            "  Drop folder      Swap source folder",
            "",
            "── Slideshow ──────────────────",
            "  ← / →            Previous / Next image",
            "  Scroll           Zoom at mouse cursor",
            "  + / −            Zoom in / out",
            "  Drag             Pan when zoomed",
            "  0                Fit to window",
            "  Z / dbl-click    1:1 focus check",
            "  R                Rotate 90° clockwise (preview)",
            "  Ctrl+S           Save rotation to file",
            "  C                Crop (drag, Enter apply, Esc cancel)",
            "  I                Toggle image / video info panel",
            "  E                Reveal in Explorer",
            "",
            "── Video playback ─────────────",
            "  Space / K        Play / pause",
            "  , / .            Skip ±5 seconds",
            "  J / L            Skip ±10 seconds",
            "  ; / '            Frame step backward / forward",
            "  [ / ]            Speed down / up",
            "  M                Mute / unmute",
            "  Ctrl+L           Toggle loop",
            "",
            "── Overlays & Tools ───────────",
            "  H                Toggle histogram",
            "  P                Toggle focus peaking",
            "  O                Open with system default",
            "  Ctrl+O           Open with… (PS / LR)",
            "  ?                Toggle shortcut panel",
            "  ESC              Back to the folder (full screen stays as you set it)",
        ]
        if self._manager and self._manager.has_destinations:
            dests = self._manager.destinations
            from picker import sort_keys
            dest_lines = [f"  {sort_keys.key_of(dests[i], i):<17}Send to \"{dests[i]['name']}\""
                          for i in range(len(dests))]
            lines += [
                "",
                "── Sort to destinations ───────",
                "  Enter            Send to active destination",
                "  Tab              Cycle active destination",
                *dest_lines,
                "  Ctrl+Z           Undo last action",
            ]
        lines += ["", "── Rating ───────", "  Ctrl+1 – 5       Star rating (again to clear)"]
        QMessageBox.information(self, "Keyboard Shortcuts", "\n".join(lines))

    # ── Theme ──────────────────────────────────────────────────────────────────

    def apply_theme(self, name: str, *, persist: bool = True):
        """Switch dark/light, live. Painters read colours at paint time and
        stylesheets are bound (theme.bind), so this is a restyle + repaint:
        no view is rebuilt, scroll positions and selections stay put."""
        if name not in theme_mod.THEMES:
            return
        theme_mod.apply(QApplication.instance(), name)
        if persist:
            settings_mod.set_value("theme", name)
        self.setStyleSheet(theme_mod.main_window_qss())
        theme_mod.match_titlebar(self)
        if getattr(self, "_light_action", None) is not None:
            self._light_action.setChecked(name == "light")
        theme_mod.restyle()                       # every open window
        if self._parked_album is not None:        # hidden, but keep it current
            theme_mod.restyle(self._parked_album)
        if self._gallery:
            self._gallery.refresh_theme()
        if self._slideshow:
            self._slideshow.refresh_theme()

    def set_motion(self, on: bool, *, persist: bool = True):
        motion_mod.set_enabled(on, persist=persist)
        if getattr(self, "_motion_action", None) is not None:
            self._motion_action.setChecked(motion_mod.enabled())
        self.statusBar().showMessage(
            "Animation on" if motion_mod.enabled() else "Animation off: every change is instant",
            2000)

    def _wire_browser(self, browser):
        browser.tile_size_changed.connect(self._on_browser_zoom)
        browser.location_changed.connect(
            lambda path, b=browser: self.set_location(path) if self.centralWidget() is b else None)
        browser.open_image.connect(self._open_image_in_folder)
        browser.sort_requested.connect(self._start_sort)
        browser.back_requested.connect(self._show_library_view)

    def _open_settings(self):
        from picker.settings_dialog import SettingsDialog
        dlg = SettingsDialog(self)
        dlg.exec()
        # Appearance chosen in Settings applies now, not after a restart.
        chosen = settings_mod.get("theme") or "dark"
        if chosen != theme_mod.current():
            self.apply_theme(chosen, persist=False)
        motion_mod.invalidate()
        if getattr(self, "_motion_action", None) is not None:
            self._motion_action.setChecked(motion_mod.enabled())

    # ── Library home view ──────────────────────────────────────────────────────

    def _show_library_view(self):
        """Swap central widget to the library home screen."""
        from picker.library_view import LibraryView
        self._discard_parked_album()
        if self._slideshow:
            self._slideshow.cleanup()
            self._slideshow = None
        if self._gallery:
            self._gallery.cleanup()
            self._gallery = None
        if self._album_browser:
            self._album_browser.cleanup()
            self._album_browser = None
        self._current_folder = None
        self._library_view = LibraryView(self)
        self._library_view.open_folder.connect(self._swap_source)
        self._library_view.manage_requested.connect(self._manage_library_roots)
        self._library_view.add_requested.connect(self._add_library_root)
        self._set_central(self._library_view)
        self.set_location("")
        self._schedule_idle_index()

    # ── Background pre-index ────────────────────────────────────────────────────

    def _schedule_idle_index(self):
        if not bool(settings_mod.get("auto_scan_on_launch")):
            return
        QTimer.singleShot(3000, self._idle_index_tick)

    def _idle_index_tick(self):
        if self._slideshow or self._gallery:
            return
        roots = library_mod.roots()
        if not roots:
            return
        # Read settings on the UI thread, then walk + index on a background thread
        # so a large library can't freeze the window. scan_root opens its own
        # sqlite connection and touches no Qt objects, so this is safe.
        from picker.image_manager import active_extensions
        params = {
            "exts": active_extensions(settings_mod.get("file_types") or None),
            "excl_hidden": bool(settings_mod.get("exclude_hidden")),
            "inc_sub": bool(settings_mod.get("scan_recursive")),
        }
        import threading
        threading.Thread(target=self._run_idle_index,
                         args=(list(roots), params), daemon=True).start()

    def _run_idle_index(self, roots, params):
        from picker import index as index_mod
        from picker import log
        for root in roots:
            path = root.get("path")
            if not path or not os.path.isdir(path):
                continue
            try:
                index_mod.scan_root(
                    path,
                    include_subfolders=params["inc_sub"],
                    exclude_hidden=params["excl_hidden"],
                    extensions=params["exts"],
                )
                log.info("idle index done", root=path)
            except Exception as e:
                log.warn("idle index failed", root=path, err=str(e))

    # ── Library actions ────────────────────────────────────────────────────────

    def _open_folder(self):
        from PyQt6.QtWidgets import QFileDialog
        start = (self._manager.source_folder if self._manager
                 else (self._source_folder or os.path.expanduser("~")))
        folder = QFileDialog.getExistingDirectory(self, "Open Folder", start)
        if folder:
            self._swap_source(folder)

    def _add_library_root(self):
        from PyQt6.QtWidgets import QFileDialog
        start = os.path.expanduser("~")
        folder = QFileDialog.getExistingDirectory(self, "Add Library Folder", start)
        if not folder:
            return
        library_mod.add_root(folder)
        self._scan_root_with_progress(folder, title="Adding library folder")
        if self._library_view is not None:
            self._library_view.reload()

    def _scan_root_with_progress(self, root_path: str, title: str = "Scanning library"):
        """Foreground scan with a loading screen showing the current file."""
        from picker import index as index_mod
        from picker.image_manager import active_extensions
        from picker.loading_screen import LoadingScreen
        from picker import log

        app = QApplication.instance()
        label = os.path.basename(root_path.rstrip("/\\")) or root_path
        loading = LoadingScreen(sub=f"{title}: {label}", parent=self)
        loading.show()
        loading.set_text(f"Walking {label}…")
        loading.set_progress(-1.0)
        app.processEvents()

        exts_set = active_extensions(settings_mod.get("file_types") or None)
        excl_hidden = bool(settings_mod.get("exclude_hidden"))
        inc_sub = bool(settings_mod.get("scan_recursive"))

        # Throttle UI updates so we don't drown the event loop on big roots.
        last_tick = [0.0]
        import time as _time

        def _p(done, total, current):
            now = _time.monotonic()
            if now - last_tick[0] < 0.05 and done < total:
                return
            last_tick[0] = now
            try:
                rel = os.path.relpath(current, root_path)
            except ValueError:
                rel = current
            if total > 0:
                loading.set_progress(done / total)
                loading.set_text(f"{done} / {total} — {rel}")
            else:
                loading.set_progress(-1.0)
                loading.set_text(rel)
            app.processEvents()

        try:
            new_stat = library_mod.compute_stat(root_path, exts=exts_set)
            with log.timed("scan_root", root=root_path):
                stats = index_mod.scan_root(
                    root_path,
                    include_subfolders=inc_sub,
                    exclude_hidden=excl_hidden,
                    extensions=exts_set,
                    progress_cb=_p,
                )
            library_mod.update_root(root_path, stat=new_stat)
            log.info("scan done", root=root_path, **stats)
            loading.set_text(
                f"Done — {stats.get('added', 0)} added, "
                f"{stats.get('updated', 0)} updated, "
                f"{stats.get('skipped', 0)} unchanged"
            )
            loading.set_progress(1.0)
            app.processEvents()
        except Exception as e:
            log.error("scan failed", root=root_path, err=str(e))
            loading.set_text(f"Failed: {e}")
            app.processEvents()
        finally:
            loading.close_smoothly()

    def _manage_library_roots(self):
        from picker.library_manager_dialog import LibraryManagerDialog
        LibraryManagerDialog(self).exec()
        if self._library_view is not None:
            self._library_view.reload()

    def _rescan_library(self):
        # Foreground rescan with a loading screen so users can see what's happening.
        roots = library_mod.roots()
        if not roots:
            QMessageBox.information(self, "Rescan Library", "No library folders configured.")
            return
        for rec in roots:
            self._scan_root_with_progress(rec["path"], title="Rescanning library")
        if self._library_view is not None:
            self._library_view.reload()

    def _kick_background_scan(self, force: bool = False):
        """Rescan all library roots. Uses quick-diff (folder stat snapshot) to
        skip roots whose file count / total size / max mtime match the last
        scan — avoids re-walking huge unchanged libraries on every launch."""
        from picker import log
        if not settings_mod.get("auto_scan_on_launch") and not force:
            log.info("scan skipped (auto_scan disabled)")
            return
        root_recs = library_mod.roots()
        if not root_recs:
            log.info("scan skipped (no roots)")
            return
        from picker import index as index_mod
        from picker.image_manager import active_extensions
        exts_set = active_extensions(settings_mod.get("file_types") or None)
        inc_sub = bool(settings_mod.get("scan_recursive"))
        excl_hidden = bool(settings_mod.get("exclude_hidden"))

        def _run():
            for rec in root_recs:
                path = rec["path"]
                prev_stat = rec.get("stat")
                new_stat = library_mod.compute_stat(path, exts=exts_set)
                if not force and prev_stat and not library_mod.stat_differs(prev_stat, new_stat):
                    log.info("root unchanged — skip deep walk", root=path,
                             count=new_stat["count"])
                    continue
                log.info("root changed — deep scan", root=path,
                         prev=prev_stat, new=new_stat)
                try:
                    with log.timed("scan_root", root=path):
                        stats = index_mod.scan_root(
                            path,
                            include_subfolders=inc_sub,
                            exclude_hidden=excl_hidden,
                            extensions=exts_set,
                        )
                    log.info("scan done", root=path, **stats)
                    library_mod.update_root(path, stat=new_stat)
                except Exception as e:
                    log.error("scan failed", root=path, err=str(e))
        QTimer.singleShot(200, _run)

    # ── About ──────────────────────────────────────────────────────────────────

    def _copy_diagnostics(self):
        from picker.crash import diagnostics
        app = QApplication.instance()
        if app:
            app.clipboard().setText(diagnostics())
            self.statusBar().showMessage("Diagnostic info copied to clipboard", 3000)

    def _open_log_folder(self):
        from picker import log
        import subprocess
        try:
            subprocess.Popen(["explorer", log.log_dir()])
        except Exception:
            pass

    def _check_for_updates_manual(self):
        from picker import updater
        from threading import Thread
        self.statusBar().showMessage("Checking for updates…", 3000)

        def _worker():
            result = updater.check_now()
            newer = result if (result and updater.is_newer(result["tag"])) else None
            try:
                self._update_result.emit(newer, True)   # queued onto the UI thread
            except RuntimeError:
                pass

        Thread(target=_worker, daemon=True).start()

    def _check_for_updates_auto(self):
        from picker import updater

        def _on_result(release):          # runs on the checker's thread
            try:
                self._update_result.emit(release, False)
            except RuntimeError:
                pass
        updater.check_in_background(_on_result)

    def _on_update_result(self, release, manual: bool):
        if release:
            self._show_update_toast(release)
        elif manual:
            QMessageBox.information(self, "Updates", f"PICker {__version__} is up to date.")

    def _show_update_toast(self, release: dict):
        import webbrowser
        tag = release.get("tag", "")
        url = release.get("url", "")
        name = release.get("name", tag)
        reply = QMessageBox.information(
            self, "Update Available",
            f"PICker {name} is available (you have {__version__}).\n\n"
            f"Download from the releases page?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        # The URL comes from the network: open it only if it is this project's
        # GitHub page over https. webbrowser.open() on Windows falls back to
        # os.startfile, so a file:// or bare path must never reach it.
        safe = url.lower().startswith("https://github.com/nebula3141/picker/")
        if reply == QMessageBox.StandardButton.Yes and url and safe:
            webbrowser.open(url)

    def _show_about(self):
        box = QMessageBox(self)
        box.setWindowTitle("About PICker")
        box.setIconPixmap(app_icon().pixmap(96, 96))
        box.setTextFormat(Qt.TextFormat.RichText)
        box.setText(
            "<div style='text-align:center;'>"
            "<h2 style='margin:0;'>PICker</h2>"
            f"<p style='color:#5a9bff; margin:2px 0 8px 0; font-size:11px; letter-spacing:1px;'>"
            f"VERSION {__version__}</p>"
            "<p style='color:#aaa; margin:4px 0 14px 0;'>"
            "capturing moments, minus the noise."
            "</p>"
            "<p style='margin:0 0 14px 0;'>"
            "Built for speed, clarity, and the kind of photos<br>"
            "you actually want to keep."
            "</p>"
            "<p style='color:#888; font-size:11px; margin-top:16px;'>"
            "by <b>nebula3141</b>"
            "</p>"
            "</div>"
        )
        box.setStandardButtons(QMessageBox.StandardButton.Ok)
        box.exec()

    # ── Close ──────────────────────────────────────────────────────────────────

    def keyPressEvent(self, event):
        key = event.key()
        mods = event.modifiers()
        ctrl = bool(mods & Qt.KeyboardModifier.ControlModifier)
        if key == Qt.Key.Key_F11:
            self._toggle_fullscreen()
        elif ctrl and key == Qt.Key.Key_F:
            if self._album_browser is not None and self._slideshow is None:
                self._album_browser.focus_search()
        elif key == Qt.Key.Key_Question or (key == Qt.Key.Key_Slash and mods & Qt.KeyboardModifier.ShiftModifier):
            self._show_shortcuts()
        elif key == Qt.Key.Key_Escape:
            # An active search is the innermost thing Esc should undo.
            if (self._album_browser is not None and self._slideshow is None
                    and self._album_browser.clear_search()):
                return
            # In gallery (album opened), ESC returns to album browser.
            # Slideshow handles its own ESC; album browser routes ESC → library.
            if self._gallery is not None and self._slideshow is None and self._current_folder is not None:
                self._show_album_browser()
            elif self._album_browser is not None and self._slideshow is None:
                # Go UP one folder level (hierarchical back); go_back() drops to
                # the library only when already at the source root.
                self._album_browser.go_back()
            else:
                super().keyPressEvent(event)
        else:
            super().keyPressEvent(event)

    def closeEvent(self, event):
        # Only confirm when there's still work in flight (a big folder mid-scan) —
        # a quit prompt on an idle app is just friction.
        mgr = self._manager
        if mgr is not None and hasattr(mgr, "scan_complete") and not mgr.scan_complete:
            reply = QMessageBox.question(
                self, "Quit PICker",
                "PICker is still loading this folder. Quit anyway?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No)
            if reply != QMessageBox.StandardButton.Yes:
                event.ignore()
                return
        self._save_window_geometry()
        if self._gallery:
            self._gallery.cleanup()
        if self._album_browser:
            self._album_browser.cleanup()
        self._discard_parked_album()
        if self._slideshow:
            self._slideshow.cleanup()
        # Catch-all: persist any image dimensions measured but not yet flushed
        # (e.g. a folder still mid-load) and the lazily-written viewer positions,
        # so the next launch opens instantly and resumes where you were.
        try:
            from picker import dim_cache, listing_cache, thumb_store
            thumb_store.service().shutdown(wait_ms=150)
            dim_cache.flush()
            settings_mod.flush_positions()
            settings_mod.flush_settings()
            listing_cache.save()      # next launch opens these folders from memory
            from picker import library_view as _lv
            if _lv._CARD_POOL is not None:
                _lv._CARD_POOL.clear()    # drop queued card counts; don't make exit wait
        except Exception:
            pass
        super().closeEvent(event)


# ── Entry point ────────────────────────────────────────────────────────────────

def _parse_args():
    import argparse
    parser = argparse.ArgumentParser(
        prog="PICker",
        description="PICker — photo library & sorter",
    )
    parser.add_argument("path", nargs="?", default=None,
                        help="File or folder to open")
    parser.add_argument("--version", action="version",
                        version=f"PICker {__version__}")
    parser.add_argument("--reset-settings", action="store_true",
                        help="Delete settings and start fresh")
    parser.add_argument("--portable", action="store_true",
                        help="Store all data next to the executable")
    parser.add_argument("--log", action="store_true",
                        help="Enable logging regardless of settings")
    parser.add_argument("--new-window", action="store_true",
                        help="Force a new window (skip single-instance)")
    return parser.parse_args()


def _check_single_instance(app, args) -> bool:
    """Try to connect to existing PICker instance. Returns True if we should exit."""
    if args.new_window:
        return False
    from PyQt6.QtNetwork import QLocalSocket, QLocalServer
    socket = QLocalSocket()
    socket.connectToServer("PICker-SingleInstance")
    if socket.waitForConnected(500):
        msg = args.path or ""
        socket.write(msg.encode("utf-8"))
        socket.waitForBytesWritten(1000)
        socket.disconnectFromServer()
        return True
    server = QLocalServer()
    server.removeServer("PICker-SingleInstance")
    server.listen("PICker-SingleInstance")
    app._ipc_server = server
    return False


def _setup_ipc_listener(app, window):
    server = getattr(app, "_ipc_server", None)
    if not server:
        return
    def on_new_connection():
        conn = server.nextPendingConnection()
        if not conn:
            return
        conn.waitForReadyRead(1000)
        data = bytes(conn.readAll()).decode("utf-8", errors="replace").strip()
        conn.close()
        if data:
            window.open_path(data)
        window.activateWindow()
        window.raise_()
    server.newConnection.connect(on_new_connection)


def _check_crash_on_launch():
    from picker.crash import last_crash, clear_last_crash
    report = last_crash()
    if not report:
        return
    box = QMessageBox()
    box.setWindowTitle("PICker — Crash Report")
    box.setIcon(QMessageBox.Icon.Warning)
    box.setText("PICker crashed last time it ran.")
    box.setInformativeText("Copy the crash report to clipboard?")
    copy_btn = box.addButton("Copy Report", QMessageBox.ButtonRole.ActionRole)
    box.addButton("Dismiss", QMessageBox.ButtonRole.RejectRole)
    box.exec()
    if box.clickedButton() == copy_btn:
        app = QApplication.instance()
        if app:
            app.clipboard().setText(report)
    clear_last_crash()


def _detect_portable() -> bool:
    if getattr(sys, "frozen", False):
        base = Path(sys.executable).parent
    else:
        base = Path(__file__).resolve().parent.parent
    return (base / "portable.txt").exists() or (base / ".portable").exists()


def _set_app_user_model_id(app_id: str = "PICker.PhotoCuller") -> None:
    """Windows-only: register an explicit AppUserModelID so the OS shows our
    window icon on the taskbar (and groups windows under our app, not the
    Python host). No-op / harmless elsewhere."""
    if sys.platform != "win32":
        return
    try:
        import ctypes
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(app_id)
    except Exception as e:
        try:
            from picker import log
            log.warn("could not set AppUserModelID", err=str(e))
        except Exception:
            pass


def main():
    args = _parse_args()
    print(f"[PICker DEBUG] sys.argv={sys.argv}", flush=True)
    print(f"[PICker DEBUG] args.path={args.path!r}", flush=True)

    if args.portable or _detect_portable():
        settings_mod.enable_portable()

    if args.reset_settings:
        p = settings_mod._settings_file()
        if p.exists():
            p.unlink()
        print("Settings reset.")
        return

    if args.log:
        import os
        os.environ["PICKER_LOG"] = "1"

    # Windows: bind an explicit AppUserModelID *before* any window exists so the
    # taskbar uses our window icon instead of grouping under the host process
    # (python.exe / the launcher), which otherwise shows a generic/Python icon.
    _set_app_user_model_id()

    app = QApplication(sys.argv)
    app.setApplicationName("PICker")
    app.setApplicationDisplayName("PICker")
    app.setOrganizationName("PICker")
    app.setWindowIcon(app_icon())
    app.setStyle("Fusion")

    if _check_single_instance(app, args):
        return

    # Two modes. Retired finishes from 5.0 betas map onto the nearer one.
    stored = settings_mod.get("theme") or "dark"
    stored = {"sandstone": "light"}.get(stored, stored)
    theme_mod.apply(app, stored if stored in theme_mod.THEMES else "dark")

    from PyQt6.QtCore import QTranslator, QLocale
    translator = QTranslator()
    locale = QLocale.system().name()
    translations_dir = Path(__file__).resolve().parent / "picker" / "translations"
    if translations_dir.is_dir() and translator.load(f"picker_{locale}", str(translations_dir)):
        app.installTranslator(translator)

    from picker import listing_cache, log
    listing_cache.load_async()   # folder index from the last run, off-thread
    log.info("PICker starting", version=__version__)

    if library_mod.seed_if_empty():
        log.info("seeded default library root", path=library_mod.default_pictures_folder())

    _check_crash_on_launch()

    has_path = bool(args.path)
    window = MainWindow(manager=None, skip_library=has_path)
    app.processEvents()

    _setup_ipc_listener(app, window)
    theme_mod.match_titlebar(window)     # native frame follows the mode

    if has_path:
        from PyQt6.QtGui import QCursor
        cursor_screen = app.screenAt(QCursor.pos())
        if cursor_screen and cursor_screen != app.primaryScreen():
            geo = cursor_screen.availableGeometry()
            window.setGeometry(geo)
            window.showMaximized()
        window.open_path(args.path)

    try:
        window._kick_background_scan()
    except Exception as e:
        log.error("background scan kick failed", err=str(e))

    QTimer.singleShot(10000, window._check_for_updates_auto)

    # Rewrite "Open with PICker" registry commands left by older builds (their
    # exe path was unquoted) — off the startup path, and only if stale.
    def _repair_assoc():
        try:
            from picker import file_assoc
            file_assoc.repair_if_stale()
        except Exception as e:
            log.error("file association repair failed", err=str(e))
    QTimer.singleShot(4000, _repair_assoc)

    sys.exit(app.exec())


if __name__ == "__main__":
    main()
