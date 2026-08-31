"""Manually-added custom objects (right-click empty canvas -> "Add Custom Object",
catalog == "user"). AddAnnotationCommand/DeleteAnnotationCommand are plain QUndoCommand
subclasses operating on a list + two callbacks (mirroring every other command in
commands.py -- see that module's docstring), so they're tested directly here with fake
add/remove/refresh callbacks rather than a real QGraphicsScene."""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication

from siril_modern_annotator.annotation.catalogs import DEFAULT_CATALOG_COLORS, SUPPORTED_CATALOGS
from siril_modern_annotator.annotation.models import Annotation, CATALOG_PRIORITY, default_priority_for_catalog
from siril_modern_annotator.gui.commands import AddAnnotationCommand, DeleteAnnotationCommand
from siril_modern_annotator.gui.object_panel import AnnotationTableModel

_app = QApplication.instance() or QApplication([])


def _custom_annotation() -> Annotation:
    return Annotation(
        catalog="user", catalog_name="Custom Object", ra=10.0, dec=20.0,
        image_x=100.0, image_y=200.0, object_type="custom",
        priority=default_priority_for_catalog("user"),
    )


def _fakes():
    """Stand-ins for MainWindow._add_scene_items_for / _remove_scene_items_for /
    _refresh_after_annotation_count_change -- records calls instead of touching Qt."""
    added: list[Annotation] = []
    removed: list[Annotation] = []
    refreshed = [0]

    def add_to_scene(ann):
        added.append(ann)

    def remove_from_scene(ann):
        removed.append(ann)

    def refresh():
        refreshed[0] += 1

    return added, removed, refreshed, add_to_scene, remove_from_scene, refresh


def test_add_annotation_command_redo_appends_and_adds_to_scene():
    ann = _custom_annotation()
    annotations: list[Annotation] = []
    added, removed, refreshed, add_to_scene, remove_from_scene, refresh = _fakes()
    cmd = AddAnnotationCommand(ann, annotations, add_to_scene, remove_from_scene, refresh)
    cmd.redo()
    assert annotations == [ann]
    assert added == [ann]
    assert refreshed[0] == 1


def test_add_annotation_command_undo_removes_from_list_and_scene():
    ann = _custom_annotation()
    annotations: list[Annotation] = []
    added, removed, refreshed, add_to_scene, remove_from_scene, refresh = _fakes()
    cmd = AddAnnotationCommand(ann, annotations, add_to_scene, remove_from_scene, refresh)
    cmd.redo()
    cmd.undo()
    assert annotations == []
    assert removed == [ann]
    assert refreshed[0] == 2


def test_delete_annotation_command_redo_removes_from_list_and_scene():
    ann = _custom_annotation()
    annotations: list[Annotation] = [ann]
    added, removed, refreshed, add_to_scene, remove_from_scene, refresh = _fakes()
    cmd = DeleteAnnotationCommand(ann, annotations, add_to_scene, remove_from_scene, refresh)
    cmd.redo()
    assert annotations == []
    assert removed == [ann]
    assert refreshed[0] == 1


def test_delete_annotation_command_undo_restores_list_and_scene():
    ann = _custom_annotation()
    annotations: list[Annotation] = [ann]
    added, removed, refreshed, add_to_scene, remove_from_scene, refresh = _fakes()
    cmd = DeleteAnnotationCommand(ann, annotations, add_to_scene, remove_from_scene, refresh)
    cmd.redo()
    cmd.undo()
    assert annotations == [ann]
    assert added == [ann]
    assert refreshed[0] == 2


def test_annotation_table_model_data_survives_a_stale_out_of_range_row():
    """Regression test for a real crash (SIGABRT / "Pure virtual function called!")
    deleting a custom object: AnnotationTableModel._annotations is the same list
    object MainWindow.annotations aliases (see ObjectPanel.set_annotations), so an
    in-place list.remove() (as DeleteAnnotationCommand does) shrinks the model's data
    immediately -- before the QTableView is ever told the row count changed (only
    set_annotations()'s beginResetModel()/endResetModel() does that). A QModelIndex
    obtained *before* that shrink stays isValid() == True afterward regardless (Qt's
    isValid() only checks row/col >= 0, it never re-queries the model -- confirmed
    against the Qt docs), so a view holding one across the mutation can still hand it
    to data() -- which must not raise for a now-out-of-range row."""
    ann1, ann2 = _custom_annotation(), _custom_annotation()
    shared_list = [ann1, ann2]
    model = AnnotationTableModel()
    model.set_annotations(shared_list)
    assert model.rowCount() == 2

    stale_index = model.index(1, 1)  # last row, obtained *before* the mutation below
    assert stale_index.isValid()

    shared_list.remove(ann2)  # in-place, mirrors DeleteAnnotationCommand.redo()
    assert model.rowCount() == 1  # aliased -- the model "sees" the shrink immediately
    assert stale_index.isValid()  # still True -- isValid() never re-queries the model
    assert model.data(stale_index) is None


def test_user_catalog_has_the_highest_default_priority():
    # Lower number wins Auto Arrange conflicts (see CATALOG_PRIORITY's own comment) --
    # a manually-placed object should win over every catalog object, including
    # Messier (the previous lowest/most-important entry).
    assert CATALOG_PRIORITY["user"] == 5
    assert CATALOG_PRIORITY["user"] < min(v for k, v in CATALOG_PRIORITY.items() if k != "user")


def test_user_catalog_has_a_default_color_but_is_not_a_queryable_catalog():
    assert DEFAULT_CATALOG_COLORS["user"] == "#FFFFFF"
    assert "user" not in SUPPORTED_CATALOGS
