"""ImageView's "Loading image..." overlay (brief: the plain black canvas during the
initial image load was confusing, and the small toolbar loading text was too easy to
miss -- per user report/screenshot). Visible from construction (before any real image
exists) until the first set_base_image() call, with no processEvents()/repaint()
trickery needed since it's just the widget's own normal initial paint state."""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtGui import QPixmap
from PyQt6.QtWidgets import QApplication

from siril_modern_annotator.gui.image_view import ImageView

_app = QApplication.instance() or QApplication([])


def test_overlay_visible_before_any_image_is_set():
    view = ImageView()
    view.show()
    assert view.loading_overlay.isVisible()


def test_overlay_hidden_after_set_base_image():
    view = ImageView()
    pixmap = QPixmap(10, 10)
    pixmap.fill()
    view.set_base_image(pixmap, 4000, 3000)
    assert not view.loading_overlay.isVisible()


def test_overlay_stays_hidden_across_subsequent_set_base_image_calls():
    # Switching Auto Stretch <-> Linear preview mode calls set_base_image again on an
    # already-loaded image -- the overlay must not reappear for that.
    view = ImageView()
    pixmap = QPixmap(10, 10)
    pixmap.fill()
    view.set_base_image(pixmap, 4000, 3000)
    view.set_base_image(pixmap, 4000, 3000)
    assert not view.loading_overlay.isVisible()


def test_overlay_geometry_tracks_view_resize():
    view = ImageView()
    view.show()
    view.resize(500, 400)
    assert view.loading_overlay.geometry() == view.rect()
    view.resize(800, 200)
    assert view.loading_overlay.geometry() == view.rect()
