"""Two-pane Sort — source mosaic on the left, destination mosaic on the right.

The fastest way to pull keepers out of a folder: arrow through the source
photos and press **Space** to copy the focused one (or the current selection)
into the destination. Both panes are live:

* a copy appears on the right **immediately** — its tile reuses the source's
  already-decoded thumbnail and aspect ratio, before the bytes have even
  finished copying (copies run on a background thread, in order);
* source tiles that already exist in the destination wear a green tick, so you
  can see at a glance what's been picked;
* both folders are watched on disk, so files added, removed or renamed by
  anything else (Explorer, another app, a camera import) show up within a
  fraction of a second — without rebuilding the grids.

Keys: arrows / Home / End / PgUp / PgDn move · Space copy · Enter preview ·
Tab switch pane · Ctrl+Z undo last copy · +/− tile size · Del delete (Recycle
Bin) · Esc back. The existing multi-destination workflow lives on unchanged as
**Sort-Multi**.
"""
from __future__ import annotations

import os
import shutil

from PyQt6.QtCore import (
    Qt, QEvent, QObject, QRect, QRunnable, QThreadPool, QTimer, QFileSystemWatcher,
    pyqtSignal, pyqtSlot,
)
from PyQt6.QtGui import QColor, QImage, QPainter, QPixmap
from PyQt6.QtWidgets import (
    QFileDialog, QHBoxLayout, QLabel, QPushButton, QScrollArea, QSplitter,
    QVBoxLayout, QWidget,
)

from . import album_browser_view as abv
from . import log
from . import settings as settings_mod
from . import theme as theme_mod
from . import thumb_store
from .album import ImageItem


def _norm(p: str) -> str:
    return os.path.normcase(os.path.abspath(p))


# ── Background copy (sequential, off the UI thread) ────────────────────────────

class _CopySignals(QObject):
    done = pyqtSignal(str, str, object, str)   # src, dst, (mtime, size) | None, error


class _CopyTask(QRunnable):
    def __init__(self, src: str, dst: str, replace: bool, signals: _CopySignals):
        super().__init__()
        self._src, self._dst, self._replace, self._signals = src, dst, replace, signals
        self.setAutoDelete(True)

    def run(self):
        err = ""
        st = None
        try:
            os.makedirs(os.path.dirname(self._dst), exist_ok=True)
            if self._replace and os.path.exists(self._dst):
                try:
                    os.remove(self._dst)
                except PermissionError:
                    abv._clear_readonly(self._dst)
                    os.remove(self._dst)
            try:
                shutil.copy2(self._src, self._dst)
            except PermissionError:
                abv._clear_readonly(self._src)
                shutil.copy2(self._src, self._dst)
            s = os.stat(self._dst)
            st = (s.st_mtime, s.st_size)
        except Exception as e:
            err = str(e) or e.__class__.__name__
        try:
            self._signals.done.emit(self._src, self._dst, st, err)
        except RuntimeError:
            pass


_COPY_POOL: QThreadPool | None = None


def _copy_pool() -> QThreadPool:
    global _COPY_POOL
    if _COPY_POOL is None:
        _COPY_POOL = QThreadPool()
        _COPY_POOL.setMaxThreadCount(1)     # keep copies in the order you pressed Space
    return _COPY_POOL


# ── One pane ───────────────────────────────────────────────────────────────────

class _Pane(QWidget):
    """Header + scrolling mosaic for one folder, kept in sync with the disk."""

    change_requested = pyqtSignal()
    items_changed = pyqtSignal()
    activated = pyqtSignal()          # clicked / focused

    def __init__(self, role: str, folder: str, cache_root: str | None, parent=None):
        super().__init__(parent)
        self._role = role
        self._folder = folder
        self._cache_root = cache_root or folder
        self._pending: dict[str, ImageItem] = {}   # optimistic tiles for in-flight copies
        self._list_gen = 0
        self._active = False
        self.mosaic: abv._ImageMosaic | None = None   # before any event filter can fire

        self._list_signals = abv._ListSignals()
        self._list_signals.listed.connect(self._on_listed)

        self._watcher = QFileSystemWatcher(self)
        self._watcher.directoryChanged.connect(self._on_dir_changed)
        self._relist_timer = QTimer(self)
        self._relist_timer.setSingleShot(True)
        self._relist_timer.setInterval(180)          # coalesce bursts of FS events
        self._relist_timer.timeout.connect(self.relist)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        self._header = QWidget()
        self._header.setObjectName("paneHeader")
        h = QHBoxLayout(self._header)
        h.setContentsMargins(14, 8, 10, 8)
        h.setSpacing(10)
        self._role_lbl = QLabel(role.upper())
        self._role_lbl.setObjectName("paneRole")
        h.addWidget(self._role_lbl)
        self._name_lbl = QLabel()
        self._name_lbl.setObjectName("paneName")
        h.addWidget(self._name_lbl, 1)
        self._count_lbl = QLabel("")
        self._count_lbl.setObjectName("paneCount")
        h.addWidget(self._count_lbl)
        self._change_btn = QPushButton("Change…")
        self._change_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._change_btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._change_btn.clicked.connect(self.change_requested.emit)
        h.addWidget(self._change_btn)
        outer.addWidget(self._header)

        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(True)
        self._scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        self._scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._scroll.viewport().installEventFilter(self)
        outer.addWidget(self._scroll, 1)

        self._empty = QLabel(self._scroll.viewport())
        self._empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._empty.setWordWrap(True)
        self._empty.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self._empty.hide()

        self._apply_style()
        self.set_folder(folder, cache_root)

    # ── folder / listing ─────────────────────────────────────────────────────
    @property
    def folder(self) -> str:
        return self._folder

    def set_folder(self, folder: str, cache_root: str | None = None):
        old = self._watcher.directories()
        if old:
            self._watcher.removePaths(old)
        self._folder = folder
        self._cache_root = cache_root or folder
        self._pending.clear()
        self._name_lbl.setText(os.path.basename(folder.rstrip("/\\")) or folder)
        self._name_lbl.setToolTip(folder)
        if os.path.isdir(folder):
            self._watcher.addPath(folder)
        hit = abv._cache_get(folder)
        self._build_mosaic(hit[1] if hit is not None else [])
        # Always confirm against disk (cheap, async); a cache hit painted already.
        self.relist()

    def _build_mosaic(self, items: list):
        if self.mosaic is not None:
            self.mosaic.cleanup()
            old = self._scroll.takeWidget()     # hand ownership back before replacing
            if old is not None:
                old.deleteLater()
        m = abv._ImageMosaic(items, self._cache_root, sort_mode=True)
        m.scroll_to.connect(self._reveal)
        m.installEventFilter(self)
        self.mosaic = m
        self._scroll.setWidget(m)
        m.set_viewport_width(max(0, self._scroll.viewport().width()))
        self._update_count()

    def relist(self):
        self._list_gen += 1
        abv._list_pool().start(abv._ListTask(self._list_gen, self._folder, self._list_signals), 10)

    def _on_dir_changed(self, _path: str):
        self._relist_timer.start()

    @pyqtSlot(int, str, object)
    def _on_listed(self, gen: int, path: str, result):
        if gen != self._list_gen or path != self._folder or self.mosaic is None:
            return
        _folders, items, _mtime = result
        listed = {it.path for it in items}
        # Keep optimistic tiles for copies still in flight.
        extra = [it for p, it in self._pending.items() if p not in listed]
        merged = sorted(items + extra, key=lambda it: it.name.lower())
        old = [(it.path, it.mtime, it.size) for it in self.mosaic.items()]
        new = [(it.path, it.mtime, it.size) for it in merged]
        if old != new:
            self.mosaic.set_items(merged)
            self._update_count()
            self.items_changed.emit()
        # Directory may have been created after we started watching nothing.
        if os.path.isdir(self._folder) and not self._watcher.directories():
            self._watcher.addPath(self._folder)

    def add_optimistic(self, item: ImageItem, aspect: float | None):
        """Show a tile for a copy that's still running."""
        if self.mosaic is None:
            return
        self._pending[item.path] = item
        items = [it for it in self.mosaic.items() if it.path != item.path] + [item]
        items.sort(key=lambda it: it.name.lower())
        self.mosaic.set_items(items, seed_aspects={item.path: aspect} if aspect else None)
        self._update_count()
        self.items_changed.emit()

    def finish_copy(self, dst: str, st):
        """A copy landed (st = (mtime, size)) or failed (st None)."""
        self._pending.pop(dst, None)
        if self.mosaic is None:
            return
        items = [it for it in self.mosaic.items() if it.path != dst]
        if st is not None:
            items.append(ImageItem(path=dst, name=os.path.basename(dst),
                                   mtime=st[0], size=st[1]))
        items.sort(key=lambda it: it.name.lower())
        self.mosaic.set_items(items)
        self._update_count()
        self.items_changed.emit()

    def remove_path(self, path: str):
        if self.mosaic is None:
            return
        self._pending.pop(path, None)
        items = [it for it in self.mosaic.items() if _norm(it.path) != _norm(path)]
        self.mosaic.set_items(items)
        self._update_count()
        self.items_changed.emit()

    def names(self) -> set[str]:
        return {it.name.lower() for it in self.mosaic.items()} if self.mosaic else set()

    def find(self, name: str) -> ImageItem | None:
        low = name.lower()
        for it in (self.mosaic.items() if self.mosaic else []):
            if it.name.lower() == low:
                return it
        return None

    # ── chrome ───────────────────────────────────────────────────────────────
    def set_active(self, on: bool):
        if on != self._active:
            self._active = on
            self._apply_style()

    def _apply_style(self):
        accent = theme_mod.c("accent").name()
        bar = "#15151a"
        line = accent if self._active else "#26262c"
        self._header.setStyleSheet(
            f"#paneHeader {{ background:{bar}; border-bottom:2px solid {line}; }}"
            "QLabel#paneRole { color:%s; font-size:10px; font-weight:800; letter-spacing:1.6px; }"
            "QLabel#paneName { color:#ececf1; font-size:13px; font-weight:600; }"
            "QLabel#paneCount { color:#8a8a93; font-size:11px; }"
            "QPushButton { background:#1f1f25; color:#d6d6dd; border:1px solid #2a2a30;"
            " border-radius:7px; padding:4px 10px; font-size:11px; }"
            "QPushButton:hover { border-color:%s; color:#fff; }"
            % (accent if self._active else "#6f6f78", accent))
        bg = theme_mod.c("canvas_bg")
        bgc = bg.name() if isinstance(bg, QColor) else bg
        self._scroll.setStyleSheet(f"QScrollArea {{ background:{bgc}; border:0; }}")
        self._empty.setStyleSheet("color:#6f6f78; font-size:13px; padding:24px;")

    def _update_count(self):
        n = len(self.mosaic.items()) if self.mosaic else 0
        self._count_lbl.setText(f"{n:,} photo{'s' if n != 1 else ''}")
        if n == 0:
            if not os.path.isdir(self._folder):
                msg = "This folder doesn't exist."
            elif self._role == "destination":
                msg = "Nothing here yet.\n\nFocus a photo on the left and press Space to copy it here."
            else:
                msg = "No photos in this folder.\n\nUse “Change…” to pick another source."
            self._empty.setText(msg)
            self._empty.setGeometry(self._scroll.viewport().rect())
            self._empty.show()
            self._empty.raise_()
        else:
            self._empty.hide()

    def _reveal(self, x: int, y: int):
        self._scroll.ensureVisible(int(x), int(y), 0, 140)

    def eventFilter(self, obj, ev):
        if obj is self._scroll.viewport() and ev.type() == QEvent.Type.Resize:
            if self.mosaic is not None:
                self.mosaic.set_viewport_width(max(0, ev.size().width()))
            self._empty.setGeometry(self._scroll.viewport().rect())
        elif obj is self.mosaic and ev.type() in (QEvent.Type.FocusIn,
                                                  QEvent.Type.MouseButtonPress):
            self.activated.emit()
        return super().eventFilter(obj, ev)

    def cleanup(self):
        self._list_gen += 1
        self._relist_timer.stop()
        dirs = self._watcher.directories()
        if dirs:
            self._watcher.removePaths(dirs)
        if self.mosaic is not None:
            self.mosaic.cleanup()


# ── Big preview over the panes ─────────────────────────────────────────────────

class _Preview(QWidget):
    """Full-size look at one photo without leaving Sort. ←/→ step through the
    pane, Space copies (source pane) and advances, Enter/Esc close."""

    closed = pyqtSignal()
    step = pyqtSignal(int)
    copy_requested = pyqtSignal()

    def __init__(self, parent: QWidget):
        super().__init__(parent)
        from .slideshow_view import _LoaderSignals
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setAutoFillBackground(False)
        self._path = ""
        self._label = ""
        self._pm: QPixmap | None = None
        self._scaled: QPixmap | None = None
        self._is_full = False
        self._task = None
        self._signals = _LoaderSignals()
        self._signals.image_ready.connect(self._on_ready)
        self.hide()

    def show_path(self, path: str, label: str, pct: int):
        from .slideshow_view import (ImageLoadTask, _FRAMES, _decode_pool, _frame_key,
                                     display_pct)
        from .media import is_video
        self._path, self._label = path, label
        key = _frame_key(path)
        pm = _FRAMES.get(key)
        self._is_full = pm is not None
        if pm is None:
            pm = thumb_store.store().get(path)
        self._set_pm(pm)
        pool = _decode_pool()
        if self._task is not None:
            pool.tryTake(self._task)
            self._task = None
        if not self._is_full and not is_video(path):
            self._task = ImageLoadTask(path, display_pct(pct), self._signals)
            pool.start(self._task, 100)
        if not self.isVisible():
            self.setGeometry(self.parentWidget().rect())
            self.show()
        self.raise_()
        self.setFocus(Qt.FocusReason.OtherFocusReason)

    @pyqtSlot(str, QImage)
    def _on_ready(self, path: str, img: QImage):
        from .slideshow_view import _FRAMES, _frame_key
        if img.isNull():
            return
        pm = QPixmap.fromImage(img)
        _FRAMES.put(_frame_key(path), pm)
        if path == self._path:
            self._task = None
            self._is_full = True
            self._set_pm(pm)

    def _set_pm(self, pm: QPixmap | None):
        self._pm = pm
        self._scaled = None
        self.update()

    def resizeEvent(self, ev):
        self._scaled = None
        super().resizeEvent(ev)

    def paintEvent(self, ev):
        p = QPainter(self)
        p.fillRect(self.rect(), QColor(8, 8, 10, 245))
        area = self.rect().adjusted(24, 24, -24, -64)
        if self._pm is not None and not self._pm.isNull() and area.width() > 0:
            if self._scaled is None:
                dpr = self.devicePixelRatioF()
                self._scaled = self._pm.scaled(
                    int(area.width() * dpr), int(area.height() * dpr),
                    Qt.AspectRatioMode.KeepAspectRatio,
                    Qt.TransformationMode.SmoothTransformation)
                self._scaled.setDevicePixelRatio(dpr)
            sw = self._scaled.width() / self._scaled.devicePixelRatio()
            sh = self._scaled.height() / self._scaled.devicePixelRatio()
            x = area.x() + (area.width() - sw) / 2
            y = area.y() + (area.height() - sh) / 2
            p.drawPixmap(int(x), int(y), self._scaled)
        p.setPen(QColor(230, 230, 235))
        f = p.font(); f.setPointSize(11); f.setBold(True); p.setFont(f)
        p.drawText(QRect(0, self.height() - 56, self.width(), 22),
                   Qt.AlignmentFlag.AlignCenter, self._label)
        p.setPen(QColor(140, 140, 150))
        f.setPointSize(9); f.setBold(False); p.setFont(f)
        p.drawText(QRect(0, self.height() - 32, self.width(), 20), Qt.AlignmentFlag.AlignCenter,
                   "← →  previous / next     ·     Space  copy to destination"
                   "     ·     Enter / Esc  close")
        p.end()

    def mousePressEvent(self, ev):
        self.close_preview()

    def keyPressEvent(self, ev):
        k = ev.key()
        if k in (Qt.Key.Key_Escape, Qt.Key.Key_Return, Qt.Key.Key_Enter):
            self.close_preview()
        elif k in (Qt.Key.Key_Right, Qt.Key.Key_Down, Qt.Key.Key_D):
            self.step.emit(1)
        elif k in (Qt.Key.Key_Left, Qt.Key.Key_Up, Qt.Key.Key_A):
            self.step.emit(-1)
        elif k == Qt.Key.Key_Space:
            self.copy_requested.emit()
        else:
            ev.ignore()

    def close_preview(self):
        if self.isVisible():
            self.hide()
            self.closed.emit()

    def cleanup(self):
        if self._task is not None:
            from .slideshow_view import _decode_pool
            _decode_pool().tryTake(self._task)
            self._task = None


# ── The view ───────────────────────────────────────────────────────────────────

class SortView(QWidget):
    closed = pyqtSignal()
    source_changed = pyqtSignal(str)

    UNDO_MAX = 200

    def __init__(self, source: str, destination: str, parent=None,
                 cache_root: str | None = None, resolution_pct: int = 50):
        super().__init__(parent)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self._pct = resolution_pct
        self._copied = 0
        self._undo: list[str] = []            # dst paths this session created
        self._inflight: dict[str, str] = {}   # dst -> src
        self._auto_advance = bool(settings_mod.get("auto_advance_on_send"))
        self._copy_signals = _CopySignals()
        self._copy_signals.done.connect(self._on_copy_done)

        self.setAutoFillBackground(True)
        pal = self.palette()
        pal.setColor(self.backgroundRole(), theme_mod.c("canvas_bg"))
        self.setPalette(pal)

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        root.addWidget(self._build_top_bar())

        self._body = QWidget()
        body_lay = QVBoxLayout(self._body)
        body_lay.setContentsMargins(0, 0, 0, 0)
        self._splitter = QSplitter(Qt.Orientation.Horizontal)
        self._splitter.setHandleWidth(3)
        self._splitter.setStyleSheet("QSplitter::handle { background:#26262c; }")
        self._src = _Pane("source", source, cache_root)
        self._dst = _Pane("destination", destination, None)
        self._splitter.addWidget(self._src)
        self._splitter.addWidget(self._dst)
        self._splitter.setSizes([1, 1])
        body_lay.addWidget(self._splitter)
        root.addWidget(self._body, 1)
        root.addWidget(self._build_hint_bar())

        for pane in (self._src, self._dst):
            pane.change_requested.connect(lambda p=pane: self._change_folder(p))
            pane.activated.connect(lambda p=pane: self._set_active(p))
            pane.mosaic.installEventFilter(self)
        self._dst.items_changed.connect(self._refresh_marks)
        self._src.items_changed.connect(self._refresh_marks)
        self._wire_mosaic(self._src)
        self._wire_mosaic(self._dst)

        self._preview = _Preview(self._body)
        self._preview.closed.connect(self._on_preview_closed)
        self._preview.step.connect(self._preview_step)
        self._preview.copy_requested.connect(self._preview_copy)
        self._preview_pane: _Pane | None = None

        from .slideshow_view import ToastWidget
        self._toast = ToastWidget(self)

        self._active: _Pane = self._src
        self._set_active(self._src)
        self._update_top()
        QTimer.singleShot(0, self.focus_source)

    # ── chrome ───────────────────────────────────────────────────────────────
    def _build_top_bar(self) -> QWidget:
        bar = QWidget()
        bar.setObjectName("sortTop")
        bar.setStyleSheet(
            "#sortTop { background:#111114; border-bottom:1px solid #1f1f25; }"
            "QPushButton { background:#1b1b20; color:#e6e6ea; border:1px solid #2a2a30;"
            " border-radius:9px; padding:7px 14px; font-size:12px; font-weight:600; }"
            "QPushButton:hover { background:#23232a; border-color:#3b82f6; }"
            "QPushButton:disabled { color:#55555c; }"
            "QLabel#sortTitle { color:#fff; font-size:15px; font-weight:700; }"
            "QLabel#sortFlow { color:#9aa4b8; font-size:12px; }"
            "QLabel#sortStat { color:#4ade80; font-size:12px; font-weight:700; }")
        lay = QHBoxLayout(bar)
        lay.setContentsMargins(14, 9, 14, 9)
        lay.setSpacing(12)
        back = QPushButton("‹  Back")
        back.setToolTip("Back (Esc)")
        back.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        back.clicked.connect(self._request_close)
        lay.addWidget(back)
        title = QLabel("Sort")
        title.setObjectName("sortTitle")
        lay.addWidget(title)
        self._flow_lbl = QLabel("")
        self._flow_lbl.setObjectName("sortFlow")
        lay.addWidget(self._flow_lbl, 1)
        self._stat_lbl = QLabel("")
        self._stat_lbl.setObjectName("sortStat")
        lay.addWidget(self._stat_lbl)
        self._swap_btn = QPushButton("⇄ Swap")
        self._swap_btn.setToolTip("Swap source and destination")
        self._swap_btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._swap_btn.clicked.connect(self._swap)
        lay.addWidget(self._swap_btn)
        self._undo_btn = QPushButton("↩ Undo")
        self._undo_btn.setToolTip("Undo last copy (Ctrl+Z)")
        self._undo_btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._undo_btn.clicked.connect(self._undo_last)
        lay.addWidget(self._undo_btn)
        return bar

    def _build_hint_bar(self) -> QWidget:
        lbl = QLabel()
        lbl.setTextFormat(Qt.TextFormat.RichText)
        lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lbl.setStyleSheet("background:#111114; color:#9a9aa3; padding:8px; font-size:12px;"
                          " border-top:1px solid #1f1f25;")

        def chip(k, t):
            return ('<span style="background:#26262c; color:#f0f0f0; font-weight:700;'
                    f' padding:2px 8px; border-radius:4px;">{k}</span>&nbsp;&nbsp;{t}')
        sep = "&nbsp;&nbsp;&nbsp;&nbsp;·&nbsp;&nbsp;&nbsp;&nbsp;"
        lbl.setText(sep.join([
            chip("Space", "Copy →"), chip("← ↑ → ↓", "Move"), chip("Enter", "Preview"),
            chip("Tab", "Switch pane"), chip("Ctrl+Z", "Undo"), chip("+ / −", "Tile size"),
            chip("Esc", "Back"),
        ]))
        return lbl

    def _update_top(self):
        s = os.path.basename(self._src.folder.rstrip("/\\")) or self._src.folder
        d = os.path.basename(self._dst.folder.rstrip("/\\")) or self._dst.folder
        self._flow_lbl.setText(f"{s}   →   {d}")
        pending = len(self._inflight)
        txt = f"✓ {self._copied:,} copied" if self._copied else ""
        if pending:
            txt += f"   ·   {pending} copying…"
        self._stat_lbl.setText(txt)
        self._undo_btn.setEnabled(bool(self._undo))

    def _wire_mosaic(self, pane: _Pane):
        m = pane.mosaic
        if pane is self._src:
            m.space_pressed.connect(self._copy_selection)
        m.enter_pressed.connect(lambda p=pane: self._open_preview(p))
        m.removed.connect(lambda paths, p=pane: [p.remove_path(x) for x in paths])

    def focus_source(self):
        if self._src.mosaic is not None:
            self._src.mosaic.setFocus(Qt.FocusReason.OtherFocusReason)

    def _set_active(self, pane: _Pane):
        self._active = pane
        self._src.set_active(pane is self._src)
        self._dst.set_active(pane is self._dst)

    def resizeEvent(self, ev):
        super().resizeEvent(ev)
        if self._preview.isVisible():
            self._preview.setGeometry(self._body.rect())
        self._toast._reposition()

    # ── keyboard routing ─────────────────────────────────────────────────────
    def eventFilter(self, obj, ev):
        if ev.type() == QEvent.Type.KeyPress:
            key = ev.key()
            ctrl = bool(ev.modifiers() & Qt.KeyboardModifier.ControlModifier)
            if key in (Qt.Key.Key_Tab, Qt.Key.Key_Backtab):
                target = self._dst if self._active is self._src else self._src
                self._set_active(target)
                if target.mosaic is not None:
                    target.mosaic.setFocus(Qt.FocusReason.TabFocusReason)
                return True
            if ctrl and key == Qt.Key.Key_Z:
                self._undo_last()
                return True
        return super().eventFilter(obj, ev)

    def keyPressEvent(self, ev):
        key = ev.key()
        if key == Qt.Key.Key_Escape:
            self._request_close()
            return
        if key == Qt.Key.Key_Z and ev.modifiers() & Qt.KeyboardModifier.ControlModifier:
            self._undo_last()
            return
        super().keyPressEvent(ev)

    def _request_close(self):
        if self._preview.isVisible():
            self._preview.close_preview()
            return
        self.closed.emit()

    # ── copying ──────────────────────────────────────────────────────────────
    def _same_folder(self) -> bool:
        return _norm(self._src.folder) == _norm(self._dst.folder)

    def _copy_selection(self):
        m = self._src.mosaic
        if m is None or not m.items():
            return
        if self._same_folder():
            self._toast.show_message("Source and destination are the same folder", ms=2200)
            return
        idxs = sorted(m._selected) if m._selected else ([m._cursor] if m._cursor >= 0 else [])
        items = [m.items()[i] for i in idxs if 0 <= i < len(m.items())]
        if not items:
            return
        queued = 0
        already = 0
        self._resolver = None     # built on the first real name clash of this batch
        for it in items:
            res = self._queue_copy(it)
            if res == "cancel":
                break
            if res == "queued":
                queued += 1
            elif res == "already":
                already += 1
        if queued:
            name = items[0].name if len(items) == 1 else f"{queued} photos"
            dest = os.path.basename(self._dst.folder.rstrip("/\\")) or self._dst.folder
            self._toast.show_message(f"✓  {name}  →  {dest}", ms=1300)
            if bool(settings_mod.get("send_feedback")):
                self._flash(self._dst)
        elif already:
            self._toast.show_message("Already in the destination", ms=1200)
        if m._selected:
            m._set_selection(set())
        if self._auto_advance and (queued or already):
            nxt = max(idxs) + 1
            if nxt < len(m.items()):
                m.set_cursor(nxt)
        self._update_top()

    def _queue_copy(self, item: ImageItem) -> str:
        """Returns 'queued' | 'already' | 'skip' | 'cancel'."""
        dst = os.path.join(self._dst.folder, item.name)
        if _norm(dst) in {_norm(d) for d in self._inflight}:
            return "already"
        existing = self._dst.find(item.name)
        replace = False
        if existing is not None or os.path.exists(dst):
            if existing is not None and existing.mtime is None:
                return "already"        # optimistic tile of an earlier copy
            try:
                size = existing.size if existing is not None else os.path.getsize(dst)
            except OSError:
                size = -1
            if size == item.size:
                return "already"        # same name + same size: treat as the same photo
            if getattr(self, "_resolver", None) is None:
                self._resolver = abv.make_conflict_resolver(self)
            choice = self._resolver(item.path, dst)
            if choice == "cancel":
                return "cancel"
            if choice == "skip":
                return "skip"
            if choice == "replace":
                replace = True
            else:
                dst = abv._unique_dest(dst)
        # Optimistic: the tile shows up on the right right now, using the
        # source's decoded thumbnail + shape, while bytes copy in the background.
        store = thumb_store.store()
        if replace:
            store.invalidate(dst)
        store.alias(item.path, dst)
        aspect = self._src.mosaic.aspect_of(item.path) if self._src.mosaic else None
        self._dst.add_optimistic(ImageItem(path=dst, name=os.path.basename(dst)), aspect)
        self._inflight[dst] = item.path
        _copy_pool().start(_CopyTask(item.path, dst, replace, self._copy_signals))
        return "queued"

    @pyqtSlot(str, str, object, str)
    def _on_copy_done(self, src: str, dst: str, st, err: str):
        self._inflight.pop(dst, None)
        if err:
            log.warn("sort.copy failed", src=src, dst=dst, err=err)
            thumb_store.store().invalidate(dst)
            self._dst.finish_copy(dst, None)
            from .slideshow_view import _friendly_error
            self._toast.show_message(_friendly_error(err), ms=3200)
        else:
            self._copied += 1
            self._undo.append(dst)
            del self._undo[:-self.UNDO_MAX]
            self._dst.finish_copy(dst, st)
            abv.invalidate_scan_cache(self._dst.folder)
        self._update_top()

    def _undo_last(self):
        if not self._undo:
            self._toast.show_message("Nothing to undo", ms=1000)
            return
        dst = self._undo.pop()
        try:
            if os.path.isfile(dst):
                os.remove(dst)
            thumb_store.store().invalidate(dst)
            self._dst.remove_path(dst)
            self._copied = max(0, self._copied - 1)
            abv.invalidate_scan_cache(self._dst.folder)
            self._toast.show_message(f"↩  Removed {os.path.basename(dst)}", ms=1300)
            # Put focus back on the photo that was copied, ready to reconsider.
            m = self._src.mosaic
            if m is not None:
                for i, it in enumerate(m.items()):
                    if it.name.lower() == os.path.basename(dst).lower():
                        m.set_cursor(i)
                        break
        except OSError as e:
            from .slideshow_view import _friendly_error
            self._toast.show_message(_friendly_error(str(e)), ms=3000)
        self._update_top()

    def _refresh_marks(self):
        if self._src.mosaic is not None:
            self._src.mosaic.set_marked(self._dst.names())

    def _flash(self, pane: _Pane):
        pane._header.setStyleSheet(pane._header.styleSheet()
                                   .replace("#15151a", "#16301f"))
        QTimer.singleShot(160, pane._apply_style)

    # ── folders ──────────────────────────────────────────────────────────────
    def _change_folder(self, pane: _Pane):
        role = "source" if pane is self._src else "destination"
        start = pane.folder if os.path.isdir(pane.folder) else os.path.expanduser("~")
        folder = QFileDialog.getExistingDirectory(self, f"Choose {role} folder", start)
        if not folder:
            return
        self._set_pane_folder(pane, folder)

    def _set_pane_folder(self, pane: _Pane, folder: str):
        other = self._dst if pane is self._src else self._src
        if _norm(folder) == _norm(other.folder):
            self._toast.show_message("Source and destination can't be the same folder", ms=2200)
            return
        pane.set_folder(folder)
        pane.mosaic.installEventFilter(self)
        self._wire_mosaic(pane)
        settings_mod.set_value("sort_last_source" if pane is self._src
                               else "sort_last_destination", folder)
        if pane is self._src:
            self.source_changed.emit(folder)
        self._refresh_marks()
        self._update_top()
        self._set_active(pane)
        pane.mosaic.setFocus(Qt.FocusReason.OtherFocusReason)

    def _swap(self):
        s, d = self._src.folder, self._dst.folder
        self._dst.set_folder(s)
        self._src.set_folder(d)
        for pane in (self._src, self._dst):
            pane.mosaic.installEventFilter(self)
            self._wire_mosaic(pane)
        settings_mod.set_value("sort_last_source", d)
        settings_mod.set_value("sort_last_destination", s)
        self.source_changed.emit(d)
        self._refresh_marks()
        self._update_top()
        self._set_active(self._src)
        self.focus_source()

    # ── preview ──────────────────────────────────────────────────────────────
    def _open_preview(self, pane: _Pane):
        it = pane.mosaic.cursor_item() if pane.mosaic else None
        if it is None:
            return
        self._preview_pane = pane
        self._show_preview_item(pane)

    def _show_preview_item(self, pane: _Pane):
        m = pane.mosaic
        it = m.cursor_item()
        if it is None:
            return
        n = len(m.items())
        tick = "   ✓ in destination" if (pane is self._src and it.name.lower() in self._dst.names()) else ""
        self._preview.show_path(it.path, f"{it.name}   ·   {m._cursor + 1} / {n}{tick}", self._pct)

    def _preview_step(self, delta: int):
        pane = self._preview_pane
        if pane is None or pane.mosaic is None:
            return
        m = pane.mosaic
        m.set_cursor(max(0, min(len(m.items()) - 1, m._cursor + delta)))
        self._show_preview_item(pane)

    def _preview_copy(self):
        if self._preview_pane is not self._src:
            return
        self._copy_selection()               # auto-advances the cursor when enabled
        self._show_preview_item(self._src)

    def _on_preview_closed(self):
        pane = self._preview_pane or self._src
        self._preview_pane = None
        if pane.mosaic is not None:
            pane.mosaic.setFocus(Qt.FocusReason.OtherFocusReason)

    # ── lifecycle ────────────────────────────────────────────────────────────
    def cleanup(self):
        self._preview.cleanup()
        self._src.cleanup()
        self._dst.cleanup()
        try:
            self._copy_signals.done.disconnect(self._on_copy_done)
        except (TypeError, RuntimeError):
            pass
