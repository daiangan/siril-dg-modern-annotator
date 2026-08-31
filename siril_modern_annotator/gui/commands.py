"""QUndoCommand subclasses for every mutating GUI operation (brief #25).

Each command stores plain before/after values and calls a refresh callback after
mutating the model — it never talks to Siril or does I/O, keeping undo/redo entirely
local to the in-memory annotation state.
"""

from __future__ import annotations

from typing import Callable

from PyQt6.QtGui import QUndoCommand

from ..annotation.models import Annotation, StylePreset


class MoveLabelCommand(QUndoCommand):
    def __init__(
        self,
        annotation: Annotation,
        old_pos: tuple[float, float],
        new_pos: tuple[float, float],
        old_manual: bool,
        refresh: Callable[[], None],
    ):
        super().__init__(f"Move label: {annotation.catalog_name}")
        self.annotation = annotation
        self.old_pos = old_pos
        self.new_pos = new_pos
        self.old_manual = old_manual
        self.refresh = refresh

    def redo(self) -> None:
        self.annotation.label_x, self.annotation.label_y = self.new_pos
        self.annotation.manually_positioned = True
        self.refresh()

    def undo(self) -> None:
        self.annotation.label_x, self.annotation.label_y = self.old_pos
        self.annotation.manually_positioned = self.old_manual
        self.refresh()


class MoveMarkerCommand(QUndoCommand):
    """Unlike MoveLabelCommand, no manual/auto flag is needed: nothing but the user's
    own drag (or a "Reset Position" click, which passes new_pos=(None, None)) ever
    changes marker_x/marker_y, so the value itself already says whether the marker is
    overridden -- see Annotation.effective_marker_position."""

    def __init__(
        self,
        annotation: Annotation,
        old_pos: tuple[float | None, float | None],
        new_pos: tuple[float | None, float | None],
        refresh: Callable[[], None],
    ):
        super().__init__(f"Move marker: {annotation.catalog_name}")
        self.annotation = annotation
        self.old_pos = old_pos
        self.new_pos = new_pos
        self.refresh = refresh

    def redo(self) -> None:
        self.annotation.marker_x, self.annotation.marker_y = self.new_pos
        self.refresh()

    def undo(self) -> None:
        self.annotation.marker_x, self.annotation.marker_y = self.old_pos
        self.refresh()


class MoveCompassCommand(QUndoCommand):
    """Mirrors MoveMarkerCommand exactly (same None-means-default-anchor convention,
    same reasoning for needing no separate "manual" flag)."""

    def __init__(
        self,
        style: object,  # CompassStyle -- kept loosely typed to avoid a models.py import here
        old_anchor: tuple[float | None, float | None],
        new_anchor: tuple[float | None, float | None],
        refresh: Callable[[], None],
    ):
        super().__init__("Move compass")
        self.style = style
        self.old_anchor = old_anchor
        self.new_anchor = new_anchor
        self.refresh = refresh

    def redo(self) -> None:
        self.style.anchor_x, self.style.anchor_y = self.new_anchor
        self.refresh()

    def undo(self) -> None:
        self.style.anchor_x, self.style.anchor_y = self.old_anchor
        self.refresh()


class MoveInfoBoxCommand(QUndoCommand):
    """Mirrors MoveCompassCommand exactly -- same reasoning, different overlay."""

    def __init__(
        self,
        style: object,  # InfoBoxStyle -- kept loosely typed to avoid a models.py import here
        old_anchor: tuple[float | None, float | None],
        new_anchor: tuple[float | None, float | None],
        refresh: Callable[[], None],
    ):
        super().__init__("Move info box")
        self.style = style
        self.old_anchor = old_anchor
        self.new_anchor = new_anchor
        self.refresh = refresh

    def redo(self) -> None:
        self.style.anchor_x, self.style.anchor_y = self.new_anchor
        self.refresh()

    def undo(self) -> None:
        self.style.anchor_x, self.style.anchor_y = self.old_anchor
        self.refresh()


class ToggleVisibilityCommand(QUndoCommand):
    def __init__(self, annotation: Annotation, enabled: bool, refresh: Callable[[], None]):
        super().__init__(f"{'Show' if enabled else 'Hide'}: {annotation.catalog_name}")
        self.annotation = annotation
        self.enabled = enabled
        self.previous = annotation.enabled
        self.refresh = refresh

    def redo(self) -> None:
        self.annotation.enabled = self.enabled
        self.refresh()

    def undo(self) -> None:
        self.annotation.enabled = self.previous
        self.refresh()


class GlobalStyleChangeCommand(QUndoCommand):
    def __init__(
        self,
        target_holder: list,  # single-element list acting as a mutable box for the active StylePreset
        old_style: StylePreset,
        new_style: StylePreset,
        refresh: Callable[[], None],
    ):
        super().__init__("Change global style")
        self.target_holder = target_holder
        self.old_style = old_style
        self.new_style = new_style
        self.refresh = refresh

    def redo(self) -> None:
        self.target_holder[0] = self.new_style
        self.refresh()

    def undo(self) -> None:
        self.target_holder[0] = self.old_style
        self.refresh()


class AnnotationFieldsCommand(QUndoCommand):
    """Generic undo command that restores an arbitrary set of Annotation attributes.
    Used for both per-object style overrides (marker_style/label_style/
    connector_enabled) and metadata edits (custom_display_name/priority/locked).

    Repeated edits to the same target within one "session" (see MainWindow's pending-
    command merge logic) update `new_values` and re-apply in place rather than pushing a
    fresh undo entry per keystroke/slider tick, while still leaving exactly one entry on
    the stack per logical edit session.
    """

    def __init__(
        self,
        annotation: Annotation,
        old_values: dict,
        new_values: dict,
        text: str,
        refresh: Callable[[], None],
    ):
        super().__init__(text)
        self.annotation = annotation
        self.old_values = old_values
        self.new_values = new_values
        self.refresh = refresh

    def redo(self) -> None:
        for key, value in self.new_values.items():
            setattr(self.annotation, key, value)
        self.refresh()

    def undo(self) -> None:
        for key, value in self.old_values.items():
            setattr(self.annotation, key, value)
        self.refresh()


class AddAnnotationCommand(QUndoCommand):
    """Adds a brand-new Annotation (currently only ever a user-placed custom object,
    catalog == "user") to the live annotation list and its scene items. Delegates
    scene-item creation/removal to callbacks (MainWindow._add_scene_items_for /
    _remove_scene_items_for) so this module stays free of QGraphicsScene/MarkerItem
    knowledge, same as every other command here."""

    def __init__(
        self,
        annotation: Annotation,
        annotations: list[Annotation],
        add_to_scene: Callable[[Annotation], None],
        remove_from_scene: Callable[[Annotation], None],
        refresh: Callable[[], None],
    ):
        super().__init__(f"Add {annotation.catalog_name}")
        self.annotation = annotation
        self.annotations = annotations
        self.add_to_scene = add_to_scene
        self.remove_from_scene = remove_from_scene
        self.refresh = refresh

    def redo(self) -> None:
        self.annotations.append(self.annotation)
        self.add_to_scene(self.annotation)
        self.refresh()

    def undo(self) -> None:
        self.remove_from_scene(self.annotation)
        self.annotations.remove(self.annotation)
        self.refresh()


class DeleteAnnotationCommand(QUndoCommand):
    """Mirrors AddAnnotationCommand in reverse. Only ever offered for catalog == "user"
    objects (see MainWindow._show_object_context_menu) -- a catalog object should be
    Hidden, not deleted, to keep the app's catalog-vs-manual data model honest; a
    custom object has no catalog backing it, so Hide-forever would just be permanent
    clutter with no real value."""

    def __init__(
        self,
        annotation: Annotation,
        annotations: list[Annotation],
        add_to_scene: Callable[[Annotation], None],
        remove_from_scene: Callable[[Annotation], None],
        refresh: Callable[[], None],
    ):
        super().__init__(f"Delete {annotation.catalog_name}")
        self.annotation = annotation
        self.annotations = annotations
        self.add_to_scene = add_to_scene
        self.remove_from_scene = remove_from_scene
        self.refresh = refresh

    def redo(self) -> None:
        self.remove_from_scene(self.annotation)
        self.annotations.remove(self.annotation)
        self.refresh()

    def undo(self) -> None:
        self.annotations.append(self.annotation)
        self.add_to_scene(self.annotation)
        self.refresh()


class AutoArrangeCommand(QUndoCommand):
    """Snapshots every movable annotation's label position/manual flag before Auto
    Arrange runs, so the whole batch undoes in one step."""

    def __init__(
        self,
        annotations: list[Annotation],
        before: dict[str, tuple[float | None, float | None, bool]],
        refresh: Callable[[], None],
    ):
        super().__init__("Auto Arrange Labels")
        self.annotations = annotations
        self.before = before
        self.after: dict[str, tuple[float | None, float | None, bool]] = {
            a.id: (a.label_x, a.label_y, a.manually_positioned) for a in annotations
        }
        self.refresh = refresh

    def redo(self) -> None:
        for ann in self.annotations:
            if ann.id in self.after:
                ann.label_x, ann.label_y, ann.manually_positioned = self.after[ann.id]
        self.refresh()

    def undo(self) -> None:
        for ann in self.annotations:
            if ann.id in self.before:
                ann.label_x, ann.label_y, ann.manually_positioned = self.before[ann.id]
        self.refresh()
