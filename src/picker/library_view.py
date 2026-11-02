"""Library home — the instrument at rest.

The chassis, a top rail carrying the amber readout, and the library folders as
film-back plates: square, hairline-edged, the cover sitting in a neutral well
so nothing tints it. Focus and hover are marked with corner registration
brackets rather than a colour wash. The one lit key on this screen is ADD
FOLDER.
"""
import os

from PyQt6.QtCore import Qt, QSize, QObject, QRect, QRectF, QRunnable, QThreadPool, pyqtSignal
from PyQt6.QtGui import QPixmap, QColor, QPainter, QImage, QImageReader, QPen, QFontMetrics
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QScrollArea, QGridLayout, QFrame,
)

from . import library as library_mod
from . import index as index_mod
from . import instrument as inst
from . import log
from . import theme as theme_mod


CARD_W = 268
CARD_H = 210
COVER_H = 146
MIN_COLS = 1
MAX_COLS = 8


def _c(key):
    return theme_mod.c(key)


# ── Background card data ───────────────────────────────────────────────────────
# Each card used to run two index queries AND decode its cover photo at full
# resolution on the UI thread while the home screen was being built. Now the
# plates paint immediately and a worker fills in count + a screen-sized cover.

class _CardSignals(QObject):
    ready = pyqtSignal(str, int, QImage)   # root path, photo count, cover (may be null)


_CARD_POOL: QThreadPool | None = None


def _card_pool() -> QThreadPool:
    """Cards get their own two threads instead of the app-wide pool, so a slow
    count never queues ahead of browser work — and queued cards can be dropped
    when the home screen goes away (see LibraryView.cleanup)."""
    global _CARD_POOL
    if _CARD_POOL is None:
        _CARD_POOL = QThreadPool()
        _CARD_POOL.setMaxThreadCount(2)
    return _CARD_POOL


class _CardTask(QRunnable):
    def __init__(self, root: dict, signals: _CardSignals):
        super().__init__()
        self._root, self._signals = dict(root), signals
        self.setAutoDelete(True)

    def run(self):
        from .thumb_store import lower_worker_priority
        lower_worker_priority()
        path = self._root.get("path", "")
        # The index answers with one indexed range query, so it goes first.
        # Walking the folder tree is only for a root the index has never seen
        # (so a plate is never blank before the first index pass) — walking
        # every root on every home-screen build competed with the listing and
        # thumbnail I/O on big libraries and slow drives.
        n, cover = 0, ""
        try:
            n = index_mod.folder_count_recursive(path)
        except Exception:
            n = 0
        if not n:
            try:
                from . import album as album_mod
                n, cover = album_mod.folder_stat(path)
            except Exception:
                n, cover = 0, ""
        chosen = self._root.get("cover") or ""
        if chosen and os.path.isfile(chosen):
            cover = chosen
        if not (cover and os.path.isfile(cover)):
            cover = ""
            try:
                rows = index_mod.search(root=path, order_by="date_taken", limit=1)
                if rows:
                    cover = rows[0]["path"]
            except Exception:
                cover = ""
        img = QImage()
        if cover:
            reader = QImageReader(cover)
            reader.setAutoTransform(True)
            size = reader.size()
            if size.isValid() and size.width() > 0 and size.height() > 0:
                s = max((CARD_W * 2) / size.width(), (COVER_H * 2) / size.height())
                if s < 1.0:
                    reader.setScaledSize(QSize(max(1, round(size.width() * s)),
                                               max(1, round(size.height() * s))))
            img = reader.read()
        try:
            self._signals.ready.emit(path, n, img)
        except RuntimeError:
            pass


class _RootCard(QFrame):
    """A library folder as a film-back plate."""

    clicked = pyqtSignal(str)   # emits root path

    def __init__(self, root: dict, parent=None):
        super().__init__(parent)
        self._root = root
        self._cover: QPixmap | None = None
        self._count = -1
        self._hover = False
        self._label = root.get("label") or os.path.basename(root["path"].rstrip("/\\"))
        self.setFixedSize(CARD_W, CARD_H)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setToolTip(root.get("path", ""))
        self.setAttribute(Qt.WidgetAttribute.WA_Hover, True)
        # Keyboard-reachable like every other tile in the app.
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setAccessibleName(f"Library folder {self._label}")

    def apply_data(self, n: int, cover: QImage):
        self._count = n
        if cover is not None and not cover.isNull():
            self._cover = QPixmap.fromImage(cover)
        self.update()

    def enterEvent(self, e):
        self._hover = True
        self.update()

    def leaveEvent(self, e):
        self._hover = False
        self.update()

    def focusInEvent(self, e):
        self.update()

    def focusOutEvent(self, e):
        self.update()

    def keyPressEvent(self, e):
        if e.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter, Qt.Key.Key_Space):
            self.clicked.emit(self._root["path"])
            return
        super().keyPressEvent(e)

    def mousePressEvent(self, event):
        super().mousePressEvent(event)
        if event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit(self._root["path"])

    def _scaled_cover(self, size):
        """The cover scaled once per size, not once per paint — hover and focus
        repaints stay a blit."""
        key = (size.width(), size.height(), self._cover.cacheKey())
        if getattr(self, "_scaled_key", None) != key:
            self._scaled = self._cover.scaled(
                size, Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                Qt.TransformationMode.SmoothTransformation)
            self._scaled_key = key
        return self._scaled

    def paintEvent(self, event):
        from PyQt6.QtGui import QPainterPath
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = self.rect()
        rad = theme_mod.RADIUS
        clip = QPainterPath()
        clip.addRoundedRect(QRectF(r).adjusted(0.5, 0.5, -0.5, -0.5), rad, rad)
        p.setClipPath(clip)
        p.fillRect(r, _c("deck_high") if self._hover else _c("deck"))

        well = QRect(r.left(), r.top(), r.width(), COVER_H)
        p.fillRect(well, _c("well"))
        if self._cover is not None and not self._cover.isNull():
            scaled = self._scaled_cover(well.size())
            sx = max(0, (scaled.width() - well.width()) // 2)
            sy = max(0, (scaled.height() - well.height()) // 2)
            p.drawPixmap(well, scaled, QRect(sx, sy, well.width(), well.height()))
        else:
            inst.draw_icon(p, "folder",
                           QRectF(well.center().x() - 17, well.center().y() - 17, 34, 34),
                           _c("faint"), 1.6)

        # Name in the UI face at its real case; count in steady digits.
        p.setFont(theme_mod.legend_font(10, tracking=0.0))
        p.setPen(_c("fg"))
        name_rect = QRect(r.left() + 14, well.bottom() + 12, r.width() - 28, 18)
        fm = QFontMetrics(p.font())
        p.drawText(name_rect, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                   fm.elidedText(self._label, Qt.TextElideMode.ElideMiddle,
                                 name_rect.width()))
        p.setFont(theme_mod.legend_font(9, bold=False, tracking=0.0))
        p.setPen(_c("muted"))
        count = ("Reading…" if self._count < 0
                 else f"{self._count:,} photos" if self._count else "Empty")
        p.drawText(QRect(r.left() + 14, well.bottom() + 31, r.width() - 28, 16),
                   Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, count)

        p.setClipping(False)
        edge = _c("focus") if self.hasFocus() else (_c("glass_edge") if self._hover else _c("rail"))
        p.setPen(QPen(edge, 2 if self.hasFocus() else 1))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawRoundedRect(QRectF(r).adjusted(1, 1, -1, -1) if self.hasFocus()
                          else QRectF(r).adjusted(0.5, 0.5, -0.5, -0.5), rad, rad)
        if self.hasFocus():
            inst.draw_brackets(p, well, QColor(255, 255, 255), arm=16, width=2, inset=8)
        p.end()


class _RecentKey(inst.KeyCap):
    def __init__(self, path: str, parent=None):
        name = os.path.basename(path.rstrip("/\\")) or path
        super().__init__(name, variant="dark", icon="folder", parent=parent)
        self._path = path
        self.setToolTip(path)


class LibraryView(QWidget):
    """Home screen. Emits open_folder(path) on plate activation."""

    open_folder = pyqtSignal(str)
    manage_requested = pyqtSignal()
    add_requested = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAutoFillBackground(True)
        pal = self.palette()
        pal.setColor(self.backgroundRole(), _c("chassis"))
        self.setPalette(pal)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        # ── Top rail: wordmark, readout, the lit key ───────────────────────
        rail = QWidget()
        rail.setObjectName("libRail")
        theme_mod.bind(rail, lambda: f"#libRail {{ background: transparent;"
            f" border-bottom: 1px solid {theme_mod.hexc('rail')}; }}")
        rl = QHBoxLayout(rail)
        rl.setContentsMargins(20, 12, 16, 12)
        rl.setSpacing(16)

        mark = QLabel("PICker")
        mark.setFont(theme_mod.display_font(15))
        theme_mod.bind(mark, lambda: f"color: {theme_mod.hexc('fg')}; background: transparent;")
        rl.addWidget(mark)

        self._screen = inst.Screen(height=40)
        rl.addWidget(self._screen)
        rl.addStretch(1)

        self._manage_btn = inst.KeyCap("Manage", variant="putty")
        self._manage_btn.clicked.connect(self.manage_requested.emit)
        rl.addWidget(self._manage_btn)

        self._add_btn = inst.KeyCap("Add folder", variant="commit", icon="plus")
        self._add_btn.clicked.connect(self.add_requested.emit)
        rl.addWidget(self._add_btn)
        outer.addWidget(rail)

        # ── Plates ─────────────────────────────────────────────────────────
        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(True)
        self._scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        theme_mod.bind(self._scroll, lambda: f"QScrollArea {{ background: {theme_mod.hexc('chassis')}; }}")
        self._host = QWidget()
        theme_mod.bind(self._host, lambda: f"background: {theme_mod.hexc('chassis')};")
        self._host_layout = QVBoxLayout(self._host)
        self._host_layout.setContentsMargins(20, 18, 20, 18)
        self._host_layout.setSpacing(18)
        self._scroll.setWidget(self._host)
        outer.addWidget(self._scroll, 1)

        # ── Recents strip, pinned to the bottom rail ───────────────────────
        self._recents_bar = QWidget()
        self._recents_bar.setObjectName("recentsRail")
        theme_mod.bind(self._recents_bar, lambda: f"#recentsRail {{ background: {theme_mod.hexc('deck')};"
            f" border-top: 1px solid {theme_mod.hexc('rail')}; }}")
        self._recents_layout = QHBoxLayout(self._recents_bar)
        self._recents_layout.setContentsMargins(20, 10, 20, 10)
        self._recents_layout.setSpacing(8)
        outer.addWidget(self._recents_bar)

        self._card_signals = _CardSignals()
        self._card_signals.ready.connect(self._on_card_data)
        self._cards_by_path: dict[str, _RootCard] = {}
        self._cards: list[_RootCard] = []
        self._counts: dict[str, int] = {}

        self.reload()

    # ── data ──────────────────────────────────────────────────────────────
    def _on_card_data(self, path: str, n: int, cover: QImage):
        self._counts[path] = n
        card = self._cards_by_path.get(path)
        if card is not None:
            try:
                card.apply_data(n, cover)
            except RuntimeError:
                pass
        self._update_screen()

    def _update_screen(self):
        roots = len(self._cards)
        total = sum(v for v in self._counts.values() if v > 0)
        pending = roots - len([v for v in self._counts.values()])
        self._screen.set_fields(
            [("FOLDERS", f"{roots}"), ("PHOTOS", f"{total:,}" if total else "—")],
            "Reading…" if pending > 0 else "Ready")

    def reload(self):
        """Rebuild plates and the recents rail from current library state."""
        log.info("library view reload")
        while self._host_layout.count():
            item = self._host_layout.takeAt(0)
            w = item.widget()
            if w:
                w.deleteLater()

        roots = library_mod.roots()
        self._cards = []
        self._cards_by_path = {}
        self._counts = {}

        section = QWidget()
        sec_l = QVBoxLayout(section)
        sec_l.setContentsMargins(0, 0, 0, 0)
        sec_l.setSpacing(10)
        sec_l.addWidget(inst.legend("LIBRARY FOLDERS"))

        if not roots:
            empty = QWidget()
            empty.setObjectName("emptyDeck")
            theme_mod.bind(empty, lambda: inst.deck_qss("emptyDeck"))
            el = QVBoxLayout(empty)
            el.setContentsMargins(28, 28, 28, 28)
            el.setSpacing(8)
            title = QLabel("No folders yet")
            title.setFont(theme_mod.display_font(15))
            theme_mod.bind(title, lambda: f"color: {theme_mod.hexc('fg')};")
            el.addWidget(title)
            body = QLabel("Point PICker at a folder of photos and it opens instantly — "
                          "nothing is imported, copied or changed.")
            body.setWordWrap(True)
            theme_mod.bind(body, lambda: f"color: {theme_mod.hexc('muted')}; font-size: 13px;")
            el.addWidget(body)
            hint = inst.legend("Use Add folder, top right", size=8, color="faint")
            el.addWidget(hint)
            sec_l.addWidget(empty)
        else:
            self._grid = QGridLayout()
            self._grid.setSpacing(14)
            self._grid.setContentsMargins(0, 0, 0, 0)
            for r in roots:
                card = _RootCard(r)
                card.clicked.connect(self.open_folder.emit)
                self._cards.append(card)
                self._cards_by_path[r["path"]] = card
                _card_pool().start(_CardTask(r, self._card_signals))
            self._place_cards()
            grid_host = QWidget()
            grid_host.setLayout(self._grid)
            sec_l.addWidget(grid_host)

        self._host_layout.addWidget(section)
        self._host_layout.addStretch()
        self._build_recents_bar()
        self._update_screen()

    def _build_recents_bar(self):
        while self._recents_layout.count():
            item = self._recents_layout.takeAt(0)
            w = item.widget()
            if w:
                w.deleteLater()

        recents = library_mod.recents()
        self._recents_bar.setVisible(bool(recents))
        if not recents:
            return
        self._recents_layout.addWidget(inst.legend("RECENT", size=8))
        for path in recents[:8]:
            key = _RecentKey(path)
            key.clicked.connect(lambda _=False, p=path: self.open_folder.emit(p))
            self._recents_layout.addWidget(key)
        self._recents_layout.addStretch(1)

    def _place_cards(self):
        if not hasattr(self, "_grid") or not self._cards:
            return
        while self._grid.count():
            self._grid.takeAt(0)
        width = max(0, self._scroll.viewport().width() - 44)
        cols = max(MIN_COLS, min(MAX_COLS, width // (CARD_W + 14) or 1))
        for i, card in enumerate(self._cards):
            self._grid.addWidget(card, i // cols, i % cols)
        self._grid.setColumnStretch(cols, 1)

    def theme_changed(self):
        """Mode switch (theme.restyle calls this): the palette ground shows
        behind the transparent top rail and the recents rail."""
        pal = self.palette()
        pal.setColor(self.backgroundRole(), _c("chassis"))
        self.setPalette(pal)
        self.update()

    refresh_theme = theme_changed

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._place_cards()
