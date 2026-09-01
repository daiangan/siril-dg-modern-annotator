"""ObjectPanel's "Loading objects..." placeholder (brief: show something in the
Objects tab while the initial catalog fetch is running, per user report/screenshot --
the table just sat empty with no explanation)."""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication

from siril_modern_annotator.annotation.models import Annotation
from siril_modern_annotator.gui.object_panel import ObjectPanel

_app = QApplication.instance() or QApplication([])


def _annotation() -> Annotation:
    return Annotation(
        catalog="messier", catalog_name="M31", ra=10.0, dec=41.0, image_x=100.0, image_y=200.0,
    )


def test_table_shown_by_default():
    panel = ObjectPanel()
    assert panel.table_stack.currentWidget() is panel.table


def test_set_loading_true_shows_the_placeholder():
    panel = ObjectPanel()
    panel.set_loading(True)
    assert panel.table_stack.currentWidget() is panel.loading_label


def test_set_loading_false_shows_the_table_again():
    panel = ObjectPanel()
    panel.set_loading(True)
    panel.set_loading(False)
    assert panel.table_stack.currentWidget() is panel.table


def test_set_annotations_always_clears_loading_even_with_zero_results():
    # A real fetch that legitimately finds nothing must still land back on the (empty)
    # table, not leave "Loading objects..." showing forever.
    panel = ObjectPanel()
    panel.set_loading(True)
    panel.set_annotations([])
    assert panel.table_stack.currentWidget() is panel.table


def test_set_annotations_with_results_clears_loading():
    panel = ObjectPanel()
    panel.set_loading(True)
    panel.set_annotations([_annotation()])
    assert panel.table_stack.currentWidget() is panel.table
    assert panel.model.rowCount() == 1
