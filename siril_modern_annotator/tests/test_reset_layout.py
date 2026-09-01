"""MainWindow._reset_layout's marker-reset behavior (brief: the Objects panel's Reset
button only reset label positions via run_auto_arrange, not marker positions -- an
object dragged off its catalog/WCS position stayed exactly where it was dragged to,
per a real user report).

_reset_layout itself needs a full MainWindow (no test harness exists for that), so
this tests the same mechanic it's built from directly: pushing one MoveMarkerCommand
per moved marker inside a single QUndoStack macro resets every marker_x/marker_y to
None (catalog/WCS position, per Annotation.effective_marker_position) and undoes as
one combined step, matching commands.py's own real MoveMarkerCommand exactly."""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtGui import QUndoStack
from PyQt6.QtWidgets import QApplication

from siril_modern_annotator.annotation.models import Annotation
from siril_modern_annotator.gui.commands import MoveMarkerCommand

_app = QApplication.instance() or QApplication([])


def _dragged_annotation(name: str, marker_x: float, marker_y: float) -> Annotation:
    return Annotation(
        catalog="messier", catalog_name=name, ra=10.0, dec=20.0,
        image_x=100.0, image_y=100.0, marker_x=marker_x, marker_y=marker_y,
    )


def _reset_markers_as_one_macro(stack: QUndoStack, annotations: list[Annotation], refreshed: list) -> None:
    """Mirrors _reset_layout's own loop exactly."""
    stack.beginMacro("Reset Layout")
    for ann in annotations:
        if ann.marker_x is not None:
            old_pos = (ann.marker_x, ann.marker_y)
            stack.push(MoveMarkerCommand(ann, old_pos, (None, None), lambda a=ann: refreshed.append(a.id)))
    stack.endMacro()


def test_reset_clears_every_dragged_marker():
    a = _dragged_annotation("M31", 111.0, 222.0)
    b = _dragged_annotation("M42", 333.0, 444.0)
    stack = QUndoStack()
    refreshed: list = []
    _reset_markers_as_one_macro(stack, [a, b], refreshed)
    assert (a.marker_x, a.marker_y) == (None, None)
    assert (b.marker_x, b.marker_y) == (None, None)


def test_reset_leaves_an_already_catalog_positioned_marker_untouched():
    # marker_x is already None (never dragged) -- must not push a no-op MoveMarkerCommand
    # or otherwise touch it (refreshed stays empty -- no MoveMarkerCommand ever ran).
    # Qt's QUndoStack still pushes a macro command even when nothing was added inside
    # it between beginMacro/endMacro, so count() is 1 here, not 0 -- harmless in the
    # real _reset_layout, whose macro always also contains run_auto_arrange's own
    # AutoArrangeCommand regardless of whether any marker needed resetting.
    untouched = Annotation(
        catalog="messier", catalog_name="M13", ra=10.0, dec=20.0, image_x=50.0, image_y=50.0,
    )
    stack = QUndoStack()
    refreshed: list = []
    _reset_markers_as_one_macro(stack, [untouched], refreshed)
    assert untouched.marker_x is None
    assert refreshed == []
    assert stack.count() == 1


def test_reset_of_multiple_markers_undoes_as_a_single_combined_step():
    a = _dragged_annotation("M31", 111.0, 222.0)
    b = _dragged_annotation("M42", 333.0, 444.0)
    stack = QUndoStack()
    refreshed: list = []
    _reset_markers_as_one_macro(stack, [a, b], refreshed)

    assert stack.count() == 1  # one macro, not two separate undo steps
    stack.undo()
    assert (a.marker_x, a.marker_y) == (111.0, 222.0)
    assert (b.marker_x, b.marker_y) == (333.0, 444.0)

    stack.redo()
    assert (a.marker_x, a.marker_y) == (None, None)
    assert (b.marker_x, b.marker_y) == (None, None)
