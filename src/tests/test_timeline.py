"""Timeline scrollbar: labels, dragging, smooth wheel scrolling."""
import time
from datetime import datetime

import pytest


def _ts(*a):
    return datetime(*a).timestamp()


def test_mode_follows_the_span():
    from picker.timeline import pick_mode
    assert pick_mode([_ts(2026, 6, 14, 9, 5), _ts(2026, 6, 14, 18, 2)]) == "time"
    assert pick_mode([_ts(2026, 6, 14), _ts(2026, 9, 1)]) == "day"
    assert pick_mode([_ts(2025, 11, 2), _ts(2026, 9, 1)]) == "month"
    assert pick_mode([_ts(2019, 1, 1), _ts(2026, 9, 1)]) == "year"


def test_single_day_labels_are_clock_times():
    from picker.timeline import label_for, bubble_for
    t = _ts(2026, 6, 14, 0, 45)
    assert label_for(t, "time") == "12:45 AM"
    assert label_for(_ts(2026, 6, 14, 13, 39), "time") == "1:39 PM"
    assert bubble_for(t, "time") == "Jun 14, 2026  ·  12:45 AM"
    assert label_for(t, "day") == "Jun 14"
    assert label_for(t, "month") == "Jun 2026"
    assert label_for(t, "year") == "2026"


def test_exif_dates_parse_to_local_time():
    from picker.capture_time import _parse_exif_dt
    assert _parse_exif_dt("2026:06:14 09:05:00") == _ts(2026, 6, 14, 9, 5)
    assert _parse_exif_dt("2026-06-14T09:05:00") == _ts(2026, 6, 14, 9, 5)
    assert _parse_exif_dt("garbage") is None


@pytest.fixture
def area(qapp):
    from PyQt6.QtWidgets import QScrollArea, QWidget
    a = QScrollArea()
    inner = QWidget()
    inner.setFixedSize(400, 20000)
    a.setWidget(inner)
    a.resize(420, 600)
    a.show()
    qapp.processEvents()
    yield a
    a.close()


def _pump(app, secs):
    t = time.time()
    while time.time() - t < secs:
        app.processEvents()
        time.sleep(0.004)


def _wheel(target, notches=1):
    from PyQt6.QtCore import QPoint, QPointF, Qt
    from PyQt6.QtGui import QWheelEvent
    return QWheelEvent(QPointF(10, 10), QPointF(10, 10), QPoint(0, 0), QPoint(0, -120 * notches),
                       Qt.MouseButton.NoButton, Qt.KeyboardModifier.NoModifier,
                       Qt.ScrollPhase.NoScrollPhase, False)


def test_wheel_glides_and_accumulates(qapp, area, settings_dir):
    from picker import motion
    from picker.timeline import SmoothScroll
    motion.set_enabled(True, persist=False)
    sm = SmoothScroll(area)
    sb = area.verticalScrollBar()
    qapp.sendEvent(area.viewport(), _wheel(area.viewport()))
    qapp.sendEvent(area.viewport(), _wheel(area.viewport()))   # mid-glide: extends
    _pump(qapp, 0.07)
    assert 0 < sb.value() < 2 * SmoothScroll.STEP               # gliding, not jumped
    _pump(qapp, 0.4)
    assert sb.value() == 2 * SmoothScroll.STEP                  # both notches landed
    sm.stop()


def test_wheel_is_immediate_with_animation_off(qapp, area, settings_dir):
    from picker import motion
    from picker.timeline import SmoothScroll
    motion.set_enabled(False, persist=False)
    sm = SmoothScroll(area)
    sb = area.verticalScrollBar()
    sm.scroll_to(900)
    assert sb.value() == 900                                    # no animation at all
    motion.set_enabled(True, persist=False)


def test_rail_drag_follows_pointer_instantly(qapp, area, settings_dir):
    from PyQt6.QtCore import QEvent, QPointF, Qt
    from PyQt6.QtGui import QMouseEvent
    from PyQt6.QtWidgets import QWidget
    from picker.timeline import SmoothScroll, TimelineRail
    host = QWidget()
    sm = SmoothScroll(area)
    rail = TimelineRail(area, host, sm)
    rail.resize(TimelineRail.WIDTH, 500)
    rail.set_marks([(i * 1000, _ts(2026, 6, 14, 9) + i * 600) for i in range(20)])
    sb = area.verticalScrollBar()
    t = rail._thumb()

    def ev(kind, y, buttons):
        return QMouseEvent(kind, QPointF(60, y), QPointF(60, y), Qt.MouseButton.LeftButton,
                           buttons, Qt.KeyboardModifier.NoModifier)
    qapp.sendEvent(rail, ev(QEvent.Type.MouseButtonPress, t.center().y(), Qt.MouseButton.LeftButton))
    qapp.sendEvent(rail, ev(QEvent.Type.MouseMove, 400, Qt.MouseButton.LeftButton))
    assert sb.value() > sb.maximum() * 0.6                      # same event, no easing lag
    qapp.sendEvent(rail, ev(QEvent.Type.MouseButtonRelease, 400, Qt.MouseButton.NoButton))
    assert rail._labels and rail._labels[0][1] == "9:00 AM"
    host.deleteLater()
