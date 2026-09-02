"""MainWindow._rebuild_scene -- replaces every marker/label/connector scene item
(called when the initial catalog query completes, or a project is loaded over an
already-populated scene).

Regression test for a real crash report: a SIGSEGV inside QGraphicsView::paintEvent
with no toolbar-click frame anywhere on the stack -- i.e. a plain queued repaint, not
a user action, touching an already-destroyed item. Root cause was _rebuild_scene
freeing every old item immediately (item.scene().removeItem(item) then dict.clear(),
dropping the last Python reference on the spot) instead of routing them through
_defer_item_cleanup like every other removal path in this file already does (see that
method's own docstring for the two real native crash reports it exists to prevent) --
a later repaint queued before the old items were actually gone could still dereference
one Python had already let a freed C++ object out from under."""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication, QGraphicsScene

from siril_modern_annotator.annotation.models import Annotation, StylePreset
from siril_modern_annotator.gui.main_window import MainWindow

_app = QApplication.instance() or QApplication([])


def _ann(catalog: str, catalog_name: str, ann_id: str) -> Annotation:
    return Annotation(
        catalog=catalog, catalog_name=catalog_name, ra=10.0, dec=20.0,
        image_x=100.0, image_y=100.0, id=ann_id,
    )


def _bare_window(annotations: list[Annotation]) -> MainWindow:
    # See test_deferred_item_cleanup.py's own comment on why MainWindow.__new__ (not
    # object.__new__) is needed to bypass __init__ (which needs a live sirilpy bridge).
    window = MainWindow.__new__(MainWindow)
    window.annotations = annotations
    window.global_style_holder = [StylePreset(name="test")]
    window.arcsec_per_px = 1.0
    window.catalog_colors = {}
    window.image_info = type("ImageInfo", (), {"width": 4000, "height": 3000})()
    window.image_view = type("ImageView", (), {"scene_": QGraphicsScene()})()
    window.marker_items = {}
    window.label_items = {}
    window.connector_items = {}
    window._pending_item_cleanup = []
    return window


def test_rebuild_scene_defers_cleanup_of_the_old_items_instead_of_dropping_them():
    old_ann = _ann("ngc", "NGC 6888", "old-1")
    window = _bare_window([old_ann])
    window._add_scene_items_for(old_ann)
    old_marker = window.marker_items["old-1"]
    old_label = window.label_items["old-1"]
    old_connector = window.connector_items["old-1"]

    new_ann = _ann("wr", "WR 136", "new-1")
    window.annotations = [new_ann]
    window._rebuild_scene()

    # The old items must not be dropped on the spot -- they should be sitting in the
    # same deferred-cleanup mechanism every other removal path already uses.
    assert window._pending_item_cleanup, "old items were dropped immediately, not deferred"
    deferred = {id(item) for batch in window._pending_item_cleanup for item in batch}
    assert id(old_marker) in deferred
    assert id(old_label) in deferred
    assert id(old_connector) in deferred

    # The new annotation's items must be the only ones left live in the tracking dicts.
    assert set(window.marker_items) == {"new-1"}
    assert set(window.label_items) == {"new-1"}
    assert set(window.connector_items) == {"new-1"}


def test_rebuild_scene_with_no_prior_items_queues_nothing():
    """The very first call (initial catalog load into an empty scene) has nothing to
    clean up -- must not queue an empty batch."""
    ann = _ann("messier", "M31", "m31")
    window = _bare_window([ann])
    window._rebuild_scene()
    assert window._pending_item_cleanup == []
    assert set(window.marker_items) == {"m31"}
