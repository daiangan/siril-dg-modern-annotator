"""ImageView.wheelEvent's guards against spurious zoom (brief: right-click on a
trackpad -- typically a two-finger tap -- could fire a phantom scroll/wheel event
alongside the click, silently zooming the view out until "Fit" was needed to recover).
Two independent bugs made this possible: a zero-delta event fell into the zoom-*out*
branch rather than being a no-op, and nothing checked whether a mouse button was
physically held (a genuine scroll gesture never coincides with a held button)."""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QApplication

from siril_modern_annotator.gui.image_view import ImageView

_app = QApplication.instance() or QApplication([])


class _FakeAngleDelta:
    def __init__(self, y: float):
        self._y = y

    def y(self) -> float:
        return self._y


class _FakeWheelEvent:
    def __init__(self, delta_y: float, buttons: Qt.MouseButton = Qt.MouseButton.NoButton):
        self._delta_y = delta_y
        self._buttons = buttons

    def angleDelta(self) -> _FakeAngleDelta:
        return _FakeAngleDelta(self._delta_y)

    def buttons(self) -> Qt.MouseButton:
        return self._buttons


def test_zero_delta_wheel_event_does_not_zoom():
    view = ImageView()
    before = view.transform().m11()
    view.wheelEvent(_FakeWheelEvent(0))
    assert view.transform().m11() == before


def test_wheel_event_while_a_button_is_held_is_ignored():
    view = ImageView()
    before = view.transform().m11()
    view.wheelEvent(_FakeWheelEvent(-120, buttons=Qt.MouseButton.RightButton))
    assert view.transform().m11() == before


def test_genuine_scroll_with_no_button_held_still_zooms():
    view = ImageView()
    before = view.transform().m11()
    view.wheelEvent(_FakeWheelEvent(120))
    assert view.transform().m11() > before
    after_zoom_in = view.transform().m11()
    view.wheelEvent(_FakeWheelEvent(-120))
    assert view.transform().m11() < after_zoom_in
