"""Multi Sort's destinations, filling up live.

A panel beside the photo while you sort: one section per destination with its
key, name and count, and its photos newest first. A photo you send appears in
its destination the moment you press the key (its thumbnail is reused from the
one already on screen, so there is no decode and no delay). Anything sent this
session carries an undo button — click it to send that photo back: a copy is
deleted, a moved file returns to the source folder. Photos that were already in
the destination before this session are shown but never touched.

The folders are also watched on disk, so changes made outside the app appear
too. Listing and thumbnail decoding happen off the UI thread.
"""
from __future__ import annotations

import os

from PyQt6.QtCore import (
    QFileSystemWatcher, QObject, QRect, QRectF, QRunnable, QSize, Qt,
    QThreadPool, QTimer, QVariantAnimation, pyqtSignal,
)
from PyQt6.QtGui import QColor, QImage, QPainter, QPen, QPixmap
from PyQt6.QtWidgets import (
    QHBoxLayout, QLabel, QMenu, QScrollArea, QSizePolicy, QVBoxLayout, QWidget,
)

from . import instrument as inst
from . import motion
from . import theme as theme_mod
from . import thumb_store
from .media import is_image, is_video


def _c(key: str) -> QColor:
    v = theme_mod.c(key)
    return v if isinstance(v, QColor) else QColor(v)


def _norm(p: str) -> str:
    return os.path.normcase(os.path.abspath(p))


# ── Background listing ────────────────────────────────────────────────────────

class _ListSignals(QObject):
    listed = pyqtSignal(int, list)          # dest index, [paths newest first]


class _ListTask(QRunnable):
    def __init__(self, idx: int, folder: str, signals: _ListSignals):
        super().__init__()
        self._idx, self._folder, self._signals = idx, folder, signals
        self.setAutoDelete(True)

    def run(self):
        thumb_store.lower_worker_priority()
        found = []
        try:
            with os.scandir(self._folder) as it:
                for e in it:
                    if e.is_file() and (is_image(e.name) or is_video(e.name)):
                        try:
                            found.append((e.stat().st_mtime, e.path))
                        except OSError:
                            pass
        except OSError:
            pass
        found.sort(reverse=True)
        try:
            self._signals.listed.emit(self._idx, [p for _, p in found])
        except RuntimeError:
            pass


_POOL: QThreadPool | None = None


def _pool() -> QThreadPool:
    global _POOL
    if _POOL is None:
        _POOL = QThreadPool()
        _POOL.setMaxThreadCount(2)
    return _POOL


# ── One destination's photos ──────────────────────────────────────────────────

class _ThumbWall(QWidget):
    """Newest-first photos in rows of fixed height. Session sends get an undo
    button on hover; a new arrival is outlined briefly so the eye catches it."""

    TILE_H = 58
    GAP = 2
    MAX_SHOWN = 48
    undo_clicked = pyqtSignal(str)
    open_clicked = pyqtSignal(str)

    def __init__(self, section: "_DestSection"):
        super().__init__(section)
        self._section = section
        self._paths: list[str] = []
        self._rects: list[QRect] = []
        self._hover = -1
        self._fresh: dict[str, float] = {}             # path -> highlight 0..1
        self._fade = QVariantAnimation(self)
        self._fade.setDuration(900)
        self._fade.setStartValue(1.0)
        self._fade.setEndValue(0.0)
        self._fade.setEasingCurve(motion.ease())
        self._fade.valueChanged.connect(self._on_fade)
        self.setMouseTracking(True)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

    def set_paths(self, paths: list[str]):
        self._paths = list(paths)
        self._relayout()

    def mark_fresh(self, path: str):
        if motion.dur(motion.SHORT) <= 0:
            return
        self._fresh[path] = 1.0
        self._fade.stop()
        self._fade.start()

    def _on_fade(self, v):
        for k in list(self._fresh):
            self._fresh[k] = float(v)
        if float(v) <= 0.0:
            self._fresh.clear()
        self.update()

    def _aspect(self, path: str) -> float:
        pm = thumb_store.store().get(path)
        if pm is not None and pm.height() > 0:
            return pm.width() / pm.height()
        return 1.5

    def _relayout(self):
        w = max(60, self.width())
        x = y = 0
        rects = []
        for p in self._paths[:self.MAX_SHOWN]:
            tw = max(20, int(self._aspect(p) * self.TILE_H))
            tw = min(tw, w)
            if x and x + tw > w:
                x = 0
                y += self.TILE_H + self.GAP
            rects.append(QRect(x, y, tw, self.TILE_H))
            x += tw + self.GAP
        self._rects = rects
        h = (y + self.TILE_H) if rects else 0
        if self.height() != h:
            self.setFixedHeight(h)
        self.update()

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self._relayout()

    def _index_at(self, pos) -> int:
        for i, r in enumerate(self._rects):
            if r.contains(pos):
                return i
        return -1

    def _undo_rect(self, r: QRect) -> QRect:
        return QRect(r.right() - 22, r.top() + 3, 20, 20)

    def mouseMoveEvent(self, e):
        i = self._index_at(e.position().toPoint())
        if i != self._hover:
            self._hover = i
            path = self._paths[i] if i >= 0 else ""
            undoable = bool(path) and self._section.undoable(path)
            self.setToolTip(os.path.basename(path) + "\nClick to open"
                            + ("  ·  the undo button sends it back" if undoable else "")
                            if path else "")
            self.setCursor(Qt.CursorShape.PointingHandCursor if path
                           else Qt.CursorShape.ArrowCursor)
            self.update()

    def leaveEvent(self, e):
        self._hover = -1
        self.update()

    def mousePressEvent(self, e):
        i = self._index_at(e.position().toPoint())
        if i < 0:
            return
        path = self._paths[i]
        if e.button() == Qt.MouseButton.LeftButton:
            if self._section.undoable(path) and self._undo_rect(self._rects[i]).contains(
                    e.position().toPoint()):
                self.undo_clicked.emit(path)
            else:
                self.open_clicked.emit(path)
        elif e.button() == Qt.MouseButton.RightButton:
            menu = QMenu(self)
            act_open = menu.addAction("Open")
            act_undo = menu.addAction("Send back (undo)")
            act_undo.setEnabled(self._section.undoable(path))
            act_show = menu.addAction("Show in folder")
            chosen = menu.exec(e.globalPosition().toPoint())
            if chosen is act_open:
                self.open_clicked.emit(path)
            elif chosen is act_undo:
                self.undo_clicked.emit(path)
            elif chosen is act_show:
                from . import external
                try:
                    external.reveal_in_file_manager(path)
                except Exception:
                    pass

    def paintEvent(self, _ev):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        store = thumb_store.store()
        svc = thumb_store.service()
        src = self._section.cache_root
        for i, r in enumerate(self._rects):
            path = self._paths[i]
            pm = store.get(path)
            if pm is None:
                p.fillRect(r, _c("tile_placeholder"))
                svc.request(path, src, svc.PRI_NEAR, None)
            else:
                p.drawPixmap(QRectF(r), pm, _crop(pm, r))
            glow = self._fresh.get(path, 0.0)
            if glow > 0:
                c = _c("accent")
                c.setAlphaF(glow)
                p.setPen(QPen(c, 2))
                p.setBrush(Qt.BrushStyle.NoBrush)
                p.drawRect(QRectF(r).adjusted(1, 1, -1, -1))
            if i == self._hover and self._section.undoable(path):
                u = self._undo_rect(r)
                p.setPen(Qt.PenStyle.NoPen)
                p.setBrush(QColor(0, 0, 0, 170))
                p.drawEllipse(QRectF(u))
                inst.draw_icon(p, "undo", QRectF(u).adjusted(3, 3, -3, -3),
                               QColor(255, 255, 255), 1.6)
        more = len(self._paths) - len(self._rects)
        if more > 0 and self._rects:
            r = self._rects[-1]
            p.fillRect(r, QColor(0, 0, 0, 150))
            p.setPen(QColor(255, 255, 255))
            p.setFont(theme_mod.legend_font(9, tracking=0.0))
            p.drawText(QRectF(r), Qt.AlignmentFlag.AlignCenter, f"+{more + 1}")
        p.end()


def _crop(pm, r: QRect) -> QRectF:
    """Centre part of ``pm`` with ``r``'s proportions — fill without stretching."""
    pw, ph = pm.width(), pm.height()
    if pw <= 0 or ph <= 0 or r.width() <= 0 or r.height() <= 0:
        return QRectF(0, 0, pw, ph)
    want, have = r.width() / r.height(), pw / ph
    if have > want:
        w = ph * want
        return QRectF((pw - w) / 2, 0, w, ph)
    h = pw / want
    return QRectF(0, (ph - h) / 2, pw, h)


class _DestSection(QWidget):
    def __init__(self, panel: "DestinationsPanel", idx: int, dest: dict):
        super().__init__()
        self._panel = panel
        self.idx = idx
        self.name = dest.get("name") or os.path.basename(dest.get("path", "").rstrip("/\\"))
        self.folder = dest.get("path", "")
        self.cache_root = panel.manager.source_folder
        self._paths: list[str] = []
        lay = QVBoxLayout(self)
        lay.setContentsMargins(10, 8, 8, 10)
        lay.setSpacing(6)
        head = QHBoxLayout()
        head.setSpacing(8)
        from . import sort_keys
        self.key_label = sort_keys.key_of(dest, idx)
        self._key = QLabel(self.key_label)
        self._key.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._key.setMinimumSize(20, 20)
        self._key.setContentsMargins(4, 0, 4, 0)
        self._key.setFont(theme_mod.readout_font(9))
        theme_mod.bind(self._key, lambda: f"background: {theme_mod.hexc('cap')};"
                                          f" color: {theme_mod.hexc('fg')}; border-radius: 4px;")
        head.addWidget(self._key)
        self._title = QLabel(self.name)
        self._title.setFont(theme_mod.legend_font(10, tracking=0.0))
        theme_mod.bind(self._title, lambda: f"color: {theme_mod.hexc('fg')}; background: transparent;")
        head.addWidget(self._title, 1)
        self._count = QLabel("")
        self._count.setFont(theme_mod.readout_font(9, bold=False))
        theme_mod.bind(self._count, lambda: f"color: {theme_mod.hexc('muted')}; background: transparent;")
        head.addWidget(self._count)
        lay.addLayout(head)
        self._wall = _ThumbWall(self)
        self._wall.undo_clicked.connect(panel.undo_requested.emit)
        self._wall.open_clicked.connect(
            lambda path, sec=self: panel.open_requested.emit(sec.idx, path))
        lay.addWidget(self._wall)
        self._empty = QLabel(f"Empty. Press {self.key_label or 'its key'}"
                             " to send the photo here.")
        self._empty.setWordWrap(True)
        self._empty.setFont(theme_mod.legend_font(8, bold=False, tracking=0.0))
        theme_mod.bind(self._empty, lambda: f"color: {theme_mod.hexc('faint')}; background: transparent;")
        lay.addWidget(self._empty)
        self.setAccessibleName(f"Destination {idx + 1}: {self.name}")
        self._refresh()

    def undoable(self, path: str) -> bool:
        return _norm(path) in self._panel.session

    def set_paths(self, paths: list[str]):
        self._paths = list(paths)
        self._wall.set_paths(self._paths)
        self._refresh()

    def add_front(self, path: str):
        n = _norm(path)
        self._paths = [path] + [p for p in self._paths if _norm(p) != n]
        self._wall.set_paths(self._paths)
        self._wall.mark_fresh(path)
        self._refresh()

    def remove(self, path: str):
        n = _norm(path)
        self._paths = [p for p in self._paths if _norm(p) != n]
        self._wall.set_paths(self._paths)
        self._refresh()

    def paths(self) -> list[str]:
        return list(self._paths)

    def has(self, path: str) -> bool:
        n = _norm(path)
        return any(_norm(p) == n for p in self._paths)

    def repaint_thumbs(self):
        self._wall._relayout()

    def _refresh(self):
        n = len(self._paths)
        self._count.setText(f"{n:,}")
        self._empty.setVisible(n == 0)
        self.setAccessibleDescription(f"{n} photo{'s' if n != 1 else ''}")


# ── Panel ─────────────────────────────────────────────────────────────────────

class DestinationsPanel(QWidget):
    """See module docstring."""

    WIDTH = 290
    undo_requested = pyqtSignal(str)         # a destination path to send back
    open_requested = pyqtSignal(int, str)    # (destination index, path) to preview

    def __init__(self, manager, parent=None):
        super().__init__(parent)
        self.manager = manager
        self.session: set[str] = set(manager.session_sends())
        self.setFixedWidth(self.WIDTH)
        self.setObjectName("destPanel")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        theme_mod.bind(self, lambda: f"#destPanel {{ background: {theme_mod.hexc('bg')};"
                                     f" border-left: 1px solid {theme_mod.hexc('rail')}; }}")
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        head = QHBoxLayout()
        head.setContentsMargins(10, 8, 8, 6)
        title = inst.legend("Destinations", size=8)
        head.addWidget(title, 1)
        self._undo_btn = inst.KeyCap("Undo", variant="dark", icon="undo", hint="Ctrl+Z")
        self._undo_btn.setToolTip("Send the last photo back (Ctrl+Z)")
        self._undo_btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        head.addWidget(self._undo_btn)
        outer.addLayout(head)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        theme_mod.bind(scroll, lambda: "QScrollArea { background: transparent; border: 0; }"
                                       "QScrollArea > QWidget > QWidget { background: transparent; }")
        host = QWidget()
        self._list = QVBoxLayout(host)
        self._list.setContentsMargins(0, 0, 0, 0)
        self._list.setSpacing(0)
        self._sections: list[_DestSection] = []
        for i, d in enumerate(manager.destinations):
            sec = _DestSection(self, i, d)
            self._sections.append(sec)
            self._list.addWidget(sec)
            if i < len(manager.destinations) - 1:
                line = QWidget()
                line.setFixedHeight(1)
                theme_mod.bind(line, lambda: f"background: {theme_mod.hexc('rail')};")
                self._list.addWidget(line)
        self._list.addStretch(1)
        scroll.setWidget(host)
        outer.addWidget(scroll, 1)

        self._signals = _ListSignals()
        self._signals.listed.connect(self._on_listed)
        self._watcher = QFileSystemWatcher(self)
        folders = [s.folder for s in self._sections if os.path.isdir(s.folder)]
        if folders:
            self._watcher.addPaths(folders)
        self._watcher.directoryChanged.connect(self._on_dir_changed)
        self._dirty: set[int] = set()
        self._relist_timer = QTimer(self)
        self._relist_timer.setSingleShot(True)
        self._relist_timer.setInterval(350)
        self._relist_timer.timeout.connect(self._relist_dirty)

        thumb_store.service().thumb_ready.connect(self._on_thumb_ready)
        manager.add_listener(self._on_manager_event)
        for s in self._sections:
            _pool().start(_ListTask(s.idx, s.folder, self._signals))

    @property
    def undo_button(self):
        return self._undo_btn

    def section(self, idx: int) -> "_DestSection":
        return self._sections[idx]

    # ── live updates ──────────────────────────────────────────────────────
    def _on_manager_event(self, event: str, dest_idx: int, dest_path: str, src_path: str):
        if dest_idx is None or not (0 <= dest_idx < len(self._sections)):
            return
        sec = self._sections[dest_idx]
        self.session = set(self.manager.session_sends())
        if event == "send":
            # Same pixels as the photo on screen: reuse its thumbnail, no decode.
            store = thumb_store.store()
            pm = store.get(src_path)
            if pm is not None and not store.contains(dest_path):
                store.put(dest_path, pm)
            sec.add_front(dest_path)
        elif event == "undo":
            sec.remove(dest_path)

    def _on_listed(self, idx: int, paths: list):
        if 0 <= idx < len(self._sections):
            self._sections[idx].set_paths(paths)

    def _on_dir_changed(self, folder: str):
        n = _norm(folder)
        for s in self._sections:
            if _norm(s.folder) == n:
                self._dirty.add(s.idx)
        self._relist_timer.start()

    def _relist_dirty(self):
        for idx in list(self._dirty):
            s = self._sections[idx]
            _pool().start(_ListTask(idx, s.folder, self._signals))
        self._dirty.clear()

    def _on_thumb_ready(self, path: str):
        n = _norm(path)
        for s in self._sections:
            if s.has(path) or any(_norm(p) == n for p in s._paths[:_ThumbWall.MAX_SHOWN]):
                s.repaint_thumbs()

    def cleanup(self):
        self.manager.remove_listener(self._on_manager_event)
        try:
            thumb_store.service().thumb_ready.disconnect(self._on_thumb_ready)
        except (TypeError, RuntimeError):
            pass
        try:
            self._watcher.removePaths(self._watcher.directories())
        except Exception:
            pass

    def sizeHint(self) -> QSize:
        return QSize(self.WIDTH, 400)



# ── Preview: open a destination's photo and step through that destination ───

class _DecodeSignals(QObject):
    done = pyqtSignal(int, str, QImage)


class _DecodeTask(QRunnable):
    def __init__(self, gen: int, path: str, pct: int, signals: _DecodeSignals):
        super().__init__()
        self._gen, self._path, self._pct, self._signals = gen, path, pct, signals
        self.setAutoDelete(True)

    def run(self):
        from .slideshow_view import decode_display_image
        try:
            img = decode_display_image(self._path, self._pct)
        except Exception:
            img = QImage()
        try:
            self._signals.done.emit(self._gen, self._path, img)
        except RuntimeError:
            pass


class DestPreview(QWidget):
    """A destination's photo, large, over the sorting view. Left/Right (or
    A/D, the side arrows, the wheel) step through that destination; Home/End
    jump; Esc or the close button returns to sorting. The thumbnail shows at
    once and is replaced by the full decode when it lands."""

    closed = pyqtSignal()
    undo_requested = pyqtSignal(str)

    HEAD_H = 40

    def __init__(self, parent: QWidget):
        super().__init__(parent)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setAccessibleName("Destination preview")
        self._title = ""
        self._paths: list[str] = []
        self._i = 0
        self._undoable = lambda p: False
        self._pct = 50
        self._full: dict[str, QPixmap] = {}
        self._gen = 0
        self._signals = _DecodeSignals()
        self._signals.done.connect(self._on_decoded)
        self._hover = ""
        self.setMouseTracking(True)
        head = QHBoxLayout()
        head.setContentsMargins(12, 6, 8, 6)
        head.setSpacing(8)
        self._caption = QLabel()
        self._caption.setFont(theme_mod.legend_font(10, bold=False, tracking=0.0))
        theme_mod.bind(self._caption, lambda: f"color: {theme_mod.hexc('fg')}; background: transparent;")
        head.addWidget(self._caption, 1)
        self._back_btn = inst.KeyCap("Send back", variant="dark", icon="undo")
        self._back_btn.setToolTip("Undo this send: a copy is deleted, a moved photo returns")
        self._back_btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._back_btn.clicked.connect(self._send_back)
        head.addWidget(self._back_btn)
        close = inst.KeyCap("", variant="dark", icon="close")
        close.setFixedSize(30, 28)
        close.setMinimumHeight(28)
        close.setAccessibleName("Close preview")
        close.setToolTip("Close (Esc)")
        close.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        close.clicked.connect(self.close_preview)
        head.addWidget(close)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        bar = QWidget()
        bar.setFixedHeight(self.HEAD_H)
        bar.setLayout(head)
        outer.addWidget(bar)
        outer.addStretch(1)
        self.hide()

    # ── open / move ───────────────────────────────────────────────────────
    def open(self, title: str, paths: list[str], path: str, *, undoable, pct: int):
        self._title = title
        self._paths = list(paths)
        self._undoable = undoable
        self._pct = pct
        n = _norm(path)
        self._i = next((k for k, p in enumerate(self._paths) if _norm(p) == n), 0)
        self.setGeometry(self.parentWidget().rect())
        self.show()
        self.raise_()
        self.setFocus(Qt.FocusReason.OtherFocusReason)
        self._show_current()

    def step(self, delta: int):
        if not self._paths:
            return
        self._i = max(0, min(len(self._paths) - 1, self._i + delta))
        self._show_current()

    def _current(self) -> str:
        return self._paths[self._i] if 0 <= self._i < len(self._paths) else ""

    def _show_current(self):
        path = self._current()
        if not path:
            self.close_preview()
            return
        self._caption.setText(f"{self._title}  \u00b7  {self._i + 1} of {len(self._paths)}"
                              f"  \u00b7  {os.path.basename(path)}")
        self._back_btn.setVisible(bool(self._undoable(path)))
        if path not in self._full:
            self._gen += 1
            _pool().start(_DecodeTask(self._gen, path, self._pct, self._signals))
        self.update()

    def _on_decoded(self, gen: int, path: str, img: QImage):
        if img.isNull():
            return
        self._full[path] = QPixmap.fromImage(img)
        while len(self._full) > 6:
            self._full.pop(next(iter(self._full)))
        if path == self._current():
            self.update()

    def remove_current(self):
        """The photo just left this destination (sent back): show the next."""
        if 0 <= self._i < len(self._paths):
            self._paths.pop(self._i)
            self._i = min(self._i, len(self._paths) - 1)
        self._show_current()

    def _send_back(self):
        path = self._current()
        if path:
            self.undo_requested.emit(path)

    def close_preview(self):
        self.hide()
        self.closed.emit()

    # ── input ─────────────────────────────────────────────────────────────
    def keyPressEvent(self, e):
        k = e.key()
        if k in (Qt.Key.Key_Right, Qt.Key.Key_D, Qt.Key.Key_Space):
            self.step(+1)
        elif k in (Qt.Key.Key_Left, Qt.Key.Key_A, Qt.Key.Key_Backspace):
            self.step(-1)
        elif k == Qt.Key.Key_Home:
            self.step(-len(self._paths))
        elif k == Qt.Key.Key_End:
            self.step(len(self._paths))
        elif k == Qt.Key.Key_Escape:
            self.close_preview()
        else:
            super().keyPressEvent(e)
            return
        e.accept()

    def wheelEvent(self, e):
        self.step(-1 if e.angleDelta().y() > 0 else +1)
        e.accept()

    def _arrow_rects(self):
        h = self.height()
        mid = self.HEAD_H + (h - self.HEAD_H) // 2
        return (QRect(8, mid - 28, 44, 56), QRect(self.width() - 52, mid - 28, 44, 56))

    def mouseMoveEvent(self, e):
        left, right = self._arrow_rects()
        pos = e.position().toPoint()
        hover = "l" if left.contains(pos) else "r" if right.contains(pos) else ""
        if hover != self._hover:
            self._hover = hover
            self.update()

    def mousePressEvent(self, e):
        left, right = self._arrow_rects()
        pos = e.position().toPoint()
        if left.contains(pos):
            self.step(-1)
        elif right.contains(pos):
            self.step(+1)
        self.setFocus(Qt.FocusReason.MouseFocusReason)

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self.update()

    # ── paint ─────────────────────────────────────────────────────────────
    def paintEvent(self, _ev):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        p.fillRect(self.rect(), _c("well"))
        p.fillRect(QRect(0, 0, self.width(), self.HEAD_H), theme_mod.solid("deck_high"))
        area = QRect(0, self.HEAD_H, self.width(), self.height() - self.HEAD_H).adjusted(60, 12, -60, -12)
        path = self._current()
        pm = self._full.get(path) or thumb_store.store().get(path)
        if pm is not None and pm.width() > 0 and pm.height() > 0 and area.width() > 0:
            s = min(area.width() / pm.width(), area.height() / pm.height())
            w, h = pm.width() * s, pm.height() * s
            p.drawPixmap(QRectF(area.center().x() - w / 2, area.center().y() - h / 2, w, h),
                         pm, QRectF(pm.rect()))
        left, right = self._arrow_rects()
        for which, r, icon, enabled in (("l", left, "back", self._i > 0),
                                          ("r", right, "forward", self._i < len(self._paths) - 1)):
            if not enabled:
                continue
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(0, 0, 0, 150 if self._hover == which else 90))
            p.drawRoundedRect(QRectF(r), 8, 8)
            inst.draw_icon(p, icon, QRectF(r).adjusted(10, 16, -10, -16), QColor(255, 255, 255), 2.2)
        if self.hasFocus():
            p.setPen(QPen(_c("focus"), 2))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawRect(QRectF(self.rect()).adjusted(1, 1, -1, -1))
        p.end()
