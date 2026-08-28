"""BRACKETS marker connector anchor point: BRACKETS draws four short L-shaped corner
marks (not a continuous circle/square outline -- see gui/annotation_item.py's
MarkerItem.paint / export/exporter.py's _draw_marker), so the plain circular-radius
offset every other marker shape uses can land in the gap between two corners with no
visible line there at all -- confirmed by a real user report of the connector line
"floating," pointing at empty space instead of touching the bracket."""

from __future__ import annotations

import math

import pytest

from siril_modern_annotator.annotation.models import Annotation, ConnectorStyle, MarkerShape, StylePreset
from siril_modern_annotator.annotation.renderer import (
    _bracket_anchor_point,
    compute_connector_points,
    compute_label_geometry,
    compute_marker_geometry,
)


def _on_a_drawn_bracket_segment(radius: float, x: float, y: float) -> bool:
    """True if (x, y), relative to the marker's own center, lies on one of the eight
    line segments BRACKETS actually draws."""
    arm = radius * 0.5
    eps = 1e-6

    def _near_end(v: float) -> bool:
        return -radius <= v <= -radius + arm + eps or radius - arm - eps <= v <= radius

    on_left = abs(x - (-radius)) < eps and _near_end(y)
    on_right = abs(x - radius) < eps and _near_end(y)
    on_top = abs(y - (-radius)) < eps and _near_end(x)
    on_bottom = abs(y - radius) < eps and _near_end(x)
    return on_left or on_right or on_top or on_bottom


def test_bracket_anchor_point_directly_above_lands_on_a_drawn_segment():
    """Direction straight up (0, -1): the naive circular-radius offset would put the
    point at local (0, -radius) -- exactly the middle of the top edge's gap, since
    BRACKETS only draws the outer arm-length quarter of each edge from each corner."""
    radius = 40.0
    x, y = _bracket_anchor_point(radius, 0.0, -1.0)
    assert _on_a_drawn_bracket_segment(radius, x, y)


def test_bracket_anchor_point_naive_circle_offset_would_have_missed():
    """Confirms this scenario really does expose the bug: the plain circular offset
    (what every other marker shape still uses) is NOT on a drawn segment here, so the
    fix above is meaningfully doing something rather than being a no-op."""
    radius = 40.0
    naive_x, naive_y = 0.0, -radius
    assert not _on_a_drawn_bracket_segment(radius, naive_x, naive_y)


def test_bracket_anchor_point_various_directions_all_land_on_drawn_segments():
    radius = 30.0
    for angle_deg in range(0, 360, 15):
        angle = math.radians(angle_deg)
        ux, uy = math.cos(angle), math.sin(angle)
        x, y = _bracket_anchor_point(radius, ux, uy)
        assert _on_a_drawn_bracket_segment(radius, x, y), f"angle={angle_deg} landed at ({x}, {y})"


def test_bracket_anchor_point_at_corner_direction_is_the_corner_itself():
    radius = 25.0
    x, y = _bracket_anchor_point(radius, 1.0, -1.0)  # toward the top-right corner
    assert x == radius
    assert y == -radius


def test_bracket_anchor_point_zero_radius_or_zero_direction_is_safe():
    assert _bracket_anchor_point(0.0, 1.0, 0.0) == (0.0, 0.0)
    assert _bracket_anchor_point(10.0, 0.0, 0.0) == (0.0, 0.0)


def test_connector_start_point_for_brackets_marker_lands_on_a_drawn_segment_end_to_end():
    """Full pipeline: a BRACKETS-shaped marker's connector must start on an actual
    drawn bracket line, not float toward empty space."""
    style = StylePreset(name="test")
    style.marker_style.shape = MarkerShape.BRACKETS
    style.marker_style.radius = 35.0
    ann = Annotation(
        catalog="messier", catalog_name="M31", ra=0.0, dec=0.0,
        image_x=1000.0, image_y=1000.0, label_x=1000.0, label_y=900.0,  # directly above
    )
    marker = compute_marker_geometry(ann, style)
    label = compute_label_geometry(ann, style)
    points = compute_connector_points(ann, marker, label, ConnectorStyle.STRAIGHT)
    assert points is not None
    start = points[0]
    local_x, local_y = start[0] - marker.x, start[1] - marker.y
    assert _on_a_drawn_bracket_segment(marker.radius, local_x, local_y)


def test_connector_start_point_for_circle_marker_is_unaffected():
    """Circle (and every other shape) must keep using the plain circular offset --
    this fix is scoped to BRACKETS only."""
    style = StylePreset(name="test")
    style.marker_style.shape = MarkerShape.CIRCLE
    style.marker_style.radius = 35.0
    ann = Annotation(
        catalog="messier", catalog_name="M31", ra=0.0, dec=0.0,
        image_x=1000.0, image_y=1000.0, label_x=1000.0, label_y=900.0,
    )
    marker = compute_marker_geometry(ann, style)
    label = compute_label_geometry(ann, style)
    points = compute_connector_points(ann, marker, label, ConnectorStyle.STRAIGHT)
    start = points[0]
    distance_from_center = math.hypot(start[0] - marker.x, start[1] - marker.y)
    assert distance_from_center == pytest.approx(marker.radius, abs=1e-6)
