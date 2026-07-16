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

from PyQt6.QtWidgets import QApplication, QMainWindow, QMessageBox, QGraphicsOpacityEffect, QLabel
from PyQt6.QtCore import Qt, pyqtSlot, QTimer, qInstallMessageHandler, QtMsgType, QPropertyAnimation, QEasingCurve
from PyQt6.QtGui import QAction, QDragEnterEvent, QDropEvent


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

from picker.startup_dialog import StartupDialog
from picker.settings_dialog import SettingsDialog
from picker.image_manager import ImageManager
from picker.gallery_view import GalleryView
from picker.slideshow_view import SlideshowView
from picker.loading_screen import LoadingScreen
from picker.album_browser_view import AlbumBrowserView
from picker.icon import app_icon
from picker import __version__
from picker import recent as recent_mod
from picker import settings as settings_mod
from picker import theme as theme_mod
from picker import library as library_mod


class MainWindow(QMainWindow):
    def __init__(self, manager: ImageManager | None = None, parent=None,
                 skip_library: bool = False):
        super().__init__(parent)
        self._manager: ImageManager | None = manager
        self._slideshow: SlideshowView | None = None
        self._gallery: GalleryView | None = None
        self._album_browser: AlbumBrowserView | None = None
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
        if self._manager is not None:
            self._build_gallery()
            self._update_status()
        elif not skip_library:
            self._show_library_view()

        self.showMaximized()

    _opened_from_explorer = False

    # ── Open from CLI / IPC ─────────────────────────────────────────────────────

    def open_path(self, path: str):
        p = Path(path).resolve()
        if p.is_dir():
            self._swap_source(str(p))
        elif p.is_file():
            self._opened_from_explorer = True
            self._instant_open_file(str(p))

    def _instant_open_file(self, file_path: str):
        """Open image instantly — seed ±2 neighbors, scan rest in background."""
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

        def _on_progress(count):
            QTimer.singleShot(0, lambda: self._on_scan_progress(count))

        def _on_complete():
            QTimer.singleShot(0, self._on_scan_complete)

        mgr, seed_idx = ImageManager.create_seeded(
            source_folder=folder,
            target_file=file_path,
            seed_count=2,
            destinations=self._destinations,
            mode=self._mode,
            resolution_pct=self._resolution_pct,
            on_progress=_on_progress,
            on_complete=_on_complete,
        )
        self._manager = mgr
        log.info("instant open", file=file_path, seed_count=len(mgr.images))

        self._open_slideshow(seed_idx, filmstrip_hidden=True)
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
        fp = os.path.normcase(os.path.abspath(file_path))
        folder = os.path.dirname(fp)
        self._instant_open_file(file_path)

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
        """Switch to a new source folder and show its album browser."""
        if self._slideshow:
            self._slideshow.cleanup()
            self._slideshow = None
        if self._gallery:
            self._gallery.cleanup()
            self._gallery = None
        if self._album_browser:
            self._album_browser.cleanup()
            self._album_browser = None

        app = QApplication.instance()
        src_label = os.path.basename(new_source.rstrip("/\\")) or new_source
        from picker import log
        log.info("opening folder", path=new_source)
        loading = LoadingScreen(sub=f"Opening {src_label}…", parent=self)
        loading.show()
        loading.set_text("Building folder browser…")
        app.processEvents()

        # Inherit prior destinations/mode/resolution if a manager already existed,
        # otherwise pull defaults from settings.
        if self._manager:
            self._destinations = list(self._manager.destinations)
            self._mode = self._manager.mode
            self._resolution_pct = self._manager.resolution_pct

        self._source_folder = new_source
        self._manager = None
        self._current_folder = None

        recent_mod.add(new_source)
        library_mod.push_recent(new_source)
        settings_mod.set_value("last_folder", new_source)

        self._show_album_browser()
        app.processEvents()
        loading.close_smoothly()

    # ── Folder-tree browser ───────────────────────────────────────────────────

    def _show_album_browser(self):
        if self._source_folder is None:
            self._show_library_view()
            return
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
        self._album_browser = AlbumBrowserView(self._source_folder, self)
        self._album_browser.open_image.connect(self._open_image_in_folder)
        self._album_browser.back_requested.connect(self._show_library_view)
        self._set_central(self._album_browser)
        bar = self.statusBar()
        bar.showMessage(f"  {self._source_folder}")

    @pyqtSlot(str, int)
    def _open_image_in_folder(self, folder_path: str, image_idx: int):
        """User clicked an image in the folder browser — instant open."""
        from picker.image_manager import active_extensions
        exts = active_extensions(settings_mod.get("file_types"))
        try:
            files = sorted(
                f for f in os.listdir(folder_path)
                if os.path.isfile(os.path.join(folder_path, f))
                and os.path.splitext(f)[1].lower() in exts
            )
        except OSError:
            files = []
        if not files:
            QMessageBox.information(
                self, "No images",
                f"No supported media files found in:\n\n{folder_path}",
            )
            return
        if 0 <= image_idx < len(files):
            target = os.path.join(folder_path, files[image_idx])
        else:
            idx = settings_mod.get_position(folder_path)
            idx = max(0, min(idx, len(files) - 1))
            target = os.path.join(folder_path, files[idx])
        self._opened_from_explorer = False
        self._instant_open_file(target)

    # ── Menu ───────────────────────────────────────────────────────────────────

    def _build_menu(self):
        bar = self.menuBar()
        file_menu = bar.addMenu("&File")

        home_action = QAction("Home (Library)", self)
        home_action.setShortcut("Ctrl+H")
        home_action.triggered.connect(self._show_library_view)
        file_menu.addAction(home_action)

        open_folder_action = QAction("Open Folder…", self)
        open_folder_action.setShortcut("Ctrl+O")
        open_folder_action.triggered.connect(self._open_folder)
        file_menu.addAction(open_folder_action)

        sort_action = QAction("Sort Photos…", self)
        sort_action.setShortcut("Ctrl+Shift+S")
        sort_action.triggered.connect(self._change_source)
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

        fullscreen_action = QAction("Fullscreen", self)
        fullscreen_action.setShortcut("F11")
        fullscreen_action.triggered.connect(self._toggle_fullscreen)
        view_menu.addAction(fullscreen_action)

        shortcuts_action = QAction("Show Shortcuts", self)
        shortcuts_action.setShortcut("?")
        shortcuts_action.triggered.connect(self._show_shortcuts)
        view_menu.addAction(shortcuts_action)

        view_menu.addSeparator()

        self._theme_action = QAction("Light Theme", self, checkable=True)
        self._theme_action.setChecked(theme_mod.current() == "light")
        self._theme_action.setShortcut("Ctrl+T")
        self._theme_action.triggered.connect(self._toggle_theme)
        view_menu.addAction(self._theme_action)

        help_menu = bar.addMenu("&Help")
        keys_action = QAction("Keyboard Shortcuts", self)
        keys_action.triggered.connect(self._show_shortcuts)
        help_menu.addAction(keys_action)

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
        if self._gallery:
            self._gallery.cleanup()

        self._gallery = GalleryView(self._manager)
        self._gallery.open_slideshow.connect(self._open_slideshow)
        self._set_central(self._gallery)
        self._update_status()

    def _update_status(self):
        if self._gallery:
            bar = self.statusBar()
            bar.showMessage("  " + self._gallery.status_text())

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

        self._slideshow = SlideshowView(self._manager, idx, parent=self,
                                         filmstrip_hidden=filmstrip_hidden)
        self._slideshow.closed.connect(self._on_slideshow_closed)
        self._slideshow.status_changed.connect(self._on_status_changed)
        self._slideshow.fullscreen_requested.connect(self._toggle_fullscreen)
        self._slideshow.title_changed.connect(self._update_window_title)
        self.setCentralWidget(self._slideshow)
        self._slideshow.setFocus()
        if not filmstrip_hidden:
            self._fade_in(self._slideshow)
        if not self.isFullScreen():
            self._toggle_fullscreen()
        # Fullscreen toggle / fade can steal focus; re-assert it next tick so
        # arrow keys work without a click. (_post_show_settle also retries.)
        from PyQt6.QtCore import QTimer
        QTimer.singleShot(0, self._focus_slideshow)

    def _focus_slideshow(self):
        if self._slideshow is not None:
            self.activateWindow()
            self._slideshow.setFocus(Qt.FocusReason.OtherFocusReason)

    @pyqtSlot(int)
    def _on_slideshow_closed(self, last_idx: int):
        if self._slideshow:
            self._slideshow.cleanup()
            self._slideshow = None
        self.setWindowTitle(f"PICker {__version__}")
        if self._opened_from_explorer:
            self._opened_from_explorer = False
            # Configurable: on Esc from an Explorer-opened photo, either quit
            # (one-shot viewer) or drop into the folder's mosaic to keep browsing.
            if (settings_mod.get("explorer_escape_action") or "mosaic") == "close":
                self.close()
                return
            # else fall through → _show_album_browser() below (mosaic)
        if self._current_folder is not None and self._source_folder is not None:
            self._show_album_browser()
        else:
            self._build_gallery()
            if self._gallery:
                self._gallery.scroll_to(last_idx)
                self._update_status()

    def _fade_in(self, widget, duration: int = 250):
        effect = QGraphicsOpacityEffect(widget)
        widget.setGraphicsEffect(effect)
        anim = QPropertyAnimation(effect, b"opacity")
        anim.setDuration(duration)
        anim.setStartValue(0.0)
        anim.setEndValue(1.0)
        anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        anim.finished.connect(lambda: widget.setGraphicsEffect(None))
        anim.start()
        self._view_anim = anim

    def _set_central(self, widget, *, animate: bool = True):
        """Swap the central view with a cross-dissolve: the outgoing view is
        snapshotted and melted out on top while the incoming view fades in,
        so navigating between Library / folders / gallery feels fluid rather
        than a hard cut. Falls back to a plain swap when animation is off."""
        old = self.centralWidget()
        snap = None
        if (animate and bool(settings_mod.get("slideshow_animation"))
                and old is not None and old.isVisible()
                and old.width() > 1 and old.height() > 1):
            try:
                snap = old.grab()
            except Exception:
                snap = None

        self.setCentralWidget(widget)

        if snap is None or snap.isNull():
            if animate and bool(settings_mod.get("slideshow_animation")):
                self._fade_in(widget)
            return

        self._fade_in(widget, duration=300)

        # Outgoing snapshot laid over the new view, faded to transparent.
        overlay = QLabel(widget)
        overlay.setScaledContents(True)
        overlay.setPixmap(snap)
        overlay.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)

        def _place_and_run():
            if overlay.parent() is None:
                return
            overlay.setGeometry(widget.rect())
            overlay.raise_()
            overlay.show()
            eff = QGraphicsOpacityEffect(overlay)
            overlay.setGraphicsEffect(eff)
            anim = QPropertyAnimation(eff, b"opacity", overlay)
            anim.setDuration(300)
            anim.setStartValue(1.0)
            anim.setEndValue(0.0)
            anim.setEasingCurve(QEasingCurve.Type.InOutCubic)
            anim.finished.connect(overlay.deleteLater)
            anim.start()
            self._overlay_anim = anim

        # Defer one tick so the new central widget has its real geometry.
        from PyQt6.QtCore import QTimer
        QTimer.singleShot(0, _place_and_run)

    def _toggle_fullscreen(self):
        if self.isFullScreen():
            self.showMaximized()
            self.menuBar().show()
            self.statusBar().show()
            if self._slideshow:
                self._slideshow.set_chrome_visible(True)
        else:
            self.menuBar().hide()
            self.statusBar().hide()
            if self._slideshow:
                self._slideshow.set_chrome_visible(False)
            self.showFullScreen()

    @pyqtSlot()
    def _on_status_changed(self):
        if self._gallery:
            self._gallery.refresh_all()
            self._update_status()

    # ── Change source ──────────────────────────────────────────────────────────

    def _change_source(self):
        dlg = StartupDialog(self)
        dlg.prefill({
            "source_folder": (self._manager.source_folder if self._manager
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

        if self._slideshow:
            self._slideshow.cleanup()
            self._slideshow = None

        if self._gallery:
            self._gallery.cleanup()
        if self._album_browser:
            self._album_browser.cleanup()
            self._album_browser = None

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
            "  Click            Open in slideshow (fullscreen)",
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
            "  ESC              Exit fullscreen / Back to gallery",
        ]
        if self._manager and self._manager.has_destinations:
            dests = self._manager.destinations
            dest_lines = [f"  {i+1}                Send to \"{dests[i]['name']}\"" for i in range(len(dests))]
            lines += [
                "",
                "── Sort to destinations ───────",
                "  Enter            Send to active destination",
                "  Tab              Cycle active destination",
                *dest_lines,
                "  Ctrl+Z           Undo last action",
            ]
        QMessageBox.information(self, "Keyboard Shortcuts", "\n".join(lines))

    # ── Theme ──────────────────────────────────────────────────────────────────

    def _toggle_theme(self):
        new_name = "light" if self._theme_action.isChecked() else "dark"
        app = QApplication.instance()
        theme_mod.apply(app, new_name)
        settings_mod.set_value("theme", new_name)
        self.setStyleSheet(theme_mod.main_window_qss())
        if self._gallery:
            self._gallery.refresh_theme()
        if self._slideshow:
            self._slideshow.refresh_theme()
        self.update()

    # ── Settings ───────────────────────────────────────────────────────────────

    def _open_settings(self):
        dlg = SettingsDialog(self)
        dlg.exec()

    # ── Library home view ──────────────────────────────────────────────────────

    def _show_library_view(self):
        """Swap central widget to the library home screen."""
        from picker.library_view import LibraryView
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
        self.statusBar().clearMessage()
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
        from picker import index as index_mod
        from picker.image_manager import active_extensions
        from picker import log
        exts_set = active_extensions(settings_mod.get("file_types") or None)
        excl_hidden = bool(settings_mod.get("exclude_hidden"))
        inc_sub = bool(settings_mod.get("scan_recursive"))
        for root in roots:
            path = root["path"]
            if not os.path.isdir(path):
                continue
            try:
                index_mod.scan_root(
                    path,
                    include_subfolders=inc_sub,
                    exclude_hidden=excl_hidden,
                    extensions=exts_set,
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
            if result and updater.is_newer(result["tag"]):
                QTimer.singleShot(0, lambda: self._show_update_toast(result))
            else:
                QTimer.singleShot(0, lambda: QMessageBox.information(
                    self, "Updates", f"PICker {__version__} is up to date."))

        Thread(target=_worker, daemon=True).start()

    def _check_for_updates_auto(self):
        from picker import updater
        def _on_result(release):
            QTimer.singleShot(0, lambda: self._show_update_toast(release))
        updater.check_in_background(_on_result)

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
        if reply == QMessageBox.StandardButton.Yes and url:
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
        if key == Qt.Key.Key_F11:
            self._toggle_fullscreen()
        elif key == Qt.Key.Key_Question or (key == Qt.Key.Key_Slash and mods & Qt.KeyboardModifier.ShiftModifier):
            self._show_shortcuts()
        elif key == Qt.Key.Key_Escape:
            # In gallery (album opened), ESC returns to album browser.
            # Slideshow handles its own ESC; album browser routes ESC → library.
            if self._gallery is not None and self._slideshow is None and self._current_folder is not None:
                self._show_album_browser()
            elif self._album_browser is not None and self._slideshow is None:
                self._show_library_view()
            else:
                super().keyPressEvent(event)
        else:
            super().keyPressEvent(event)

    def closeEvent(self, event):
        if self._gallery:
            self._gallery.cleanup()
        if self._album_browser:
            self._album_browser.cleanup()
        if self._slideshow:
            self._slideshow.cleanup()
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

    theme_mod.apply(app, settings_mod.get("theme") or "dark")

    from PyQt6.QtCore import QTranslator, QLocale
    translator = QTranslator()
    locale = QLocale.system().name()
    translations_dir = Path(__file__).resolve().parent / "picker" / "translations"
    if translations_dir.is_dir() and translator.load(f"picker_{locale}", str(translations_dir)):
        app.installTranslator(translator)

    from picker import log
    log.info("PICker starting", version=__version__)

    if library_mod.seed_if_empty():
        log.info("seeded default library root", path=library_mod.default_pictures_folder())

    _check_crash_on_launch()

    has_path = bool(args.path)
    window = MainWindow(manager=None, skip_library=has_path)
    app.processEvents()

    _setup_ipc_listener(app, window)

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

    sys.exit(app.exec())


if __name__ == "__main__":
    main()
