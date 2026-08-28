"""Draggable marker position + "Reset Position" (Selected Object tab). Markers were
previously fixed at image_x/image_y (the WCS-derived position) with no way to nudge one
that's a bit off for a particular object -- marker_x/marker_y is a manual override,
mirroring the existing label_x/label_y pattern, with None meaning "use the WCS
position" (see Annotation.effective_marker_position)."""

from __future__ import annotations

from siril_modern_annotator.annotation.layout import auto_arrange
from siril_modern_annotator.annotation.models import Annotation, MarkerStyle, StylePreset
from siril_modern_annotator.annotation.renderer import compute_label_geometry, compute_marker_geometry
from siril_modern_annotator.gui.commands import MoveMarkerCommand


def _ann(**overrides) -> Annotation:
    return Annotation(
        catalog="messier", catalog_name="M31", ra=10.0, dec=20.0,
        image_x=100.0, image_y=200.0, **overrides,
    )


def _style() -> StylePreset:
    return StylePreset(name="test", marker_style=MarkerStyle(radius=10.0))


def test_effective_marker_position_defaults_to_wcs_position():
    ann = _ann()
    assert ann.effective_marker_position() == (100.0, 200.0)


def test_effective_marker_position_uses_override_when_set():
    ann = _ann(marker_x=150.0, marker_y=250.0)
    assert ann.effective_marker_position() == (150.0, 250.0)


def test_compute_marker_geometry_uses_override_position():
    ann = _ann(marker_x=150.0, marker_y=250.0)
    geo = compute_marker_geometry(ann, _style())
    assert (geo.x, geo.y) == (150.0, 250.0)


def test_compute_marker_geometry_uses_wcs_position_when_no_override():
    ann = _ann()
    geo = compute_marker_geometry(ann, _style())
    assert (geo.x, geo.y) == (100.0, 200.0)


def test_move_marker_command_redo_sets_override():
    ann = _ann()
    calls = []
    cmd = MoveMarkerCommand(ann, (None, None), (150.0, 250.0), refresh=lambda: calls.append(1))
    cmd.redo()
    assert (ann.marker_x, ann.marker_y) == (150.0, 250.0)
    assert ann.effective_marker_position() == (150.0, 250.0)
    assert calls == [1]


def test_move_marker_command_undo_restores_previous_override():
    ann = _ann(marker_x=120.0, marker_y=220.0)
    cmd = MoveMarkerCommand(ann, (120.0, 220.0), (150.0, 250.0), refresh=lambda: None)
    cmd.redo()
    cmd.undo()
    assert (ann.marker_x, ann.marker_y) == (120.0, 220.0)


def test_reset_position_is_move_marker_command_to_none():
    """"Reset Position" is implemented as a MoveMarkerCommand targeting (None, None) --
    no separate command class needed since assigning None is exactly what un-overrides
    it (see effective_marker_position)."""
    ann = _ann(marker_x=150.0, marker_y=250.0)
    cmd = MoveMarkerCommand(ann, (150.0, 250.0), (None, None), refresh=lambda: None)
    cmd.redo()
    assert (ann.marker_x, ann.marker_y) == (None, None)
    assert ann.effective_marker_position() == (100.0, 200.0)  # back to the WCS position
    cmd.undo()
    assert (ann.marker_x, ann.marker_y) == (150.0, 250.0)


def test_compute_label_geometry_default_position_follows_dragged_marker():
    """Regression test for a real report ("Auto Arrange looks like it's not working"):
    both auto_arrange and this "not yet arranged" default fallback anchored off
    image_x/image_y directly, ignoring a marker_x/marker_y drag override -- so a
    marker dragged before Auto Arrange (or Reset Layout) had ever run would default
    its label right next to the marker's *original* WCS position, nowhere near where
    the marker actually is now."""
    ann = _ann(marker_x=900.0, marker_y=950.0)  # label_x/label_y still None
    style = StylePreset(name="test")
    label = compute_label_geometry(ann, style)
    assert abs(label.bbox.x0 - 900.0) < 50
    assert abs(label.bbox.y0 - 950.0) < 50


def test_auto_arrange_anchors_to_dragged_marker_position():
    ann = _ann(marker_x=1500.0, marker_y=1500.0)
    style = StylePreset(name="test")
    auto_arrange([ann], style, image_width=2000, image_height=2000)
    dx = ann.label_x - 1500.0
    dy = ann.label_y - 1500.0
    assert (dx**2 + dy**2) ** 0.5 < 200, "label should hug the actual (dragged) marker, not the original WCS position"
