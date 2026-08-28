"""Collision avoidance: placed label bounding boxes should not overlap when an
avoidable, in-bounds candidate exists (brief #35)."""

from __future__ import annotations

from siril_modern_annotator.annotation.layout import BBox, _default_text_measurer, auto_arrange
from siril_modern_annotator.annotation.models import Annotation, StylePreset


def _bbox_for(ann: Annotation, style: StylePreset) -> BBox:
    label_style = ann.effective_label_style(style)
    text = ann.display_name(label_style.name_display)
    w, h = _default_text_measurer(text, label_style)
    return BBox(ann.label_x, ann.label_y, ann.label_x + w, ann.label_y + h)


def _make_annotation(name: str, x: float, y: float, priority: int = 50) -> Annotation:
    return Annotation(
        catalog="ngc", catalog_name=name, ra=0.0, dec=0.0,
        image_x=x, image_y=y, priority=priority,
    )


def test_two_close_objects_get_non_overlapping_labels():
    style = StylePreset(name="test")
    annotations = [
        _make_annotation("NGC 1", 500, 500),
        _make_annotation("NGC 2", 520, 505),
    ]
    auto_arrange(annotations, style, image_width=2000, image_height=1500)

    for ann in annotations:
        assert ann.label_x is not None and ann.label_y is not None

    b1 = _bbox_for(annotations[0], style)
    b2 = _bbox_for(annotations[1], style)
    assert b1.overlap_area(b2) == 0.0


def test_isolated_object_gets_a_label_near_its_marker():
    style = StylePreset(name="test")
    ann = _make_annotation("Lone Object", 1000, 750)
    auto_arrange([ann], style, image_width=2000, image_height=1500)
    dx = ann.label_x - ann.image_x
    dy = ann.label_y - ann.image_y
    distance = (dx**2 + dy**2) ** 0.5
    assert distance < 200  # should hug the marker, not fly off to a far corner


def test_dragged_marker_position_is_used_not_the_original_wcs_position():
    """Regression test for a real report ("Auto Arrange looks like it's not working"):
    auto_arrange anchored label placement to image_x/image_y directly, ignoring
    marker_x/marker_y (the manual marker-position override -- see
    Annotation.effective_marker_position). For any object whose marker had been
    dragged, the label landed near the marker's original position instead of where it
    actually is now, making the label look completely disconnected from its marker."""
    style = StylePreset(name="test")
    ann = _make_annotation("Dragged", 500, 500)
    ann.marker_x, ann.marker_y = 1500.0, 1500.0
    auto_arrange([ann], style, image_width=2000, image_height=1500)
    dx = ann.label_x - 1500.0
    dy = ann.label_y - 1500.0
    distance_from_dragged = (dx**2 + dy**2) ** 0.5
    assert distance_from_dragged < 200, "label should hug the actual (dragged) marker position"


def test_locked_annotation_is_not_moved():
    style = StylePreset(name="test")
    ann = _make_annotation("Locked", 800, 600)
    ann.label_x, ann.label_y = 900.0, 550.0
    ann.locked = True
    other = _make_annotation("Neighbor", 810, 605)
    auto_arrange([ann, other], style, image_width=2000, image_height=1500)
    assert ann.label_x == 900.0
    assert ann.label_y == 550.0


def test_disabled_annotation_is_skipped():
    style = StylePreset(name="test")
    ann = _make_annotation("Hidden", 800, 600)
    ann.enabled = False
    auto_arrange([ann], style, image_width=2000, image_height=1500)
    assert ann.label_x is None and ann.label_y is None


def test_marker_radius_fn_places_label_outside_the_real_marker_radius():
    """Regression test: auto_arrange used to size its placement distance off the flat
    MarkerStyle.radius even when the real rendered marker (angular-size-scaled, see
    annotation.renderer.compute_marker_geometry) was far larger -- confirmed against a
    real M31 image, where the label landed deep inside a ~770px-radius circle sized off
    an ~18px style radius. marker_radius_fn lets the caller supply the real radius."""
    style = StylePreset(name="test")
    ann = _make_annotation("M31", 1000, 800)
    real_radius = 400.0  # stand-in for an angular-size-scaled marker much bigger than style.radius
    auto_arrange(
        [ann], style, image_width=3000, image_height=2000,
        marker_radius_fn=lambda a: real_radius,
    )
    dx = ann.label_x - ann.image_x
    dy = ann.label_y - ann.image_y
    distance = (dx**2 + dy**2) ** 0.5
    assert distance > real_radius


def test_higher_priority_object_placed_first_keeps_preferred_side():
    """With no competing neighbors, both important and minor objects should still land
    on a low-collision candidate; this mainly guards that priority ordering doesn't
    crash and that all enabled objects get placed."""
    style = StylePreset(name="test")
    important = _make_annotation("M1", 500, 500, priority=10)
    minor = _make_annotation("Faint", 1500, 1000, priority=90)
    auto_arrange([minor, important], style, image_width=2000, image_height=1500)
    assert important.label_x is not None
    assert minor.label_x is not None
