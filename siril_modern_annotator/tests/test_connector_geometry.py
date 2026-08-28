"""Connector line routing (brief #10): the line must start at the marker's edge facing
the label, not at its center. A real screenshot showed the line running from dead-center
out through the circle, which reads as visually wrong and doesn't match how Siril's own
annotator (or any typical annotation tool) draws it."""

from __future__ import annotations

import math

import pytest

from siril_modern_annotator.annotation.layout import BBox
from siril_modern_annotator.annotation.models import Annotation, ConnectorStyle, StylePreset
from siril_modern_annotator.annotation.renderer import (
    _line_box_entry_point,
    compute_connector_points,
    compute_label_geometry,
    compute_marker_geometry,
)


def _ann_with_label(label_x, label_y, radius=20.0):
    style = StylePreset(name="test")
    style.marker_style.radius = radius
    ann = Annotation(
        catalog="messier", catalog_name="M31", ra=0.0, dec=0.0,
        image_x=1000.0, image_y=1000.0, label_x=label_x, label_y=label_y,
    )
    return ann, style


def test_connector_start_point_sits_on_marker_boundary_not_center():
    ann, style = _ann_with_label(label_x=1200.0, label_y=1000.0, radius=50.0)
    marker = compute_marker_geometry(ann, style)
    label = compute_label_geometry(ann, style)
    points = compute_connector_points(ann, marker, label, ConnectorStyle.STRAIGHT)
    assert points is not None
    start = points[0]
    distance_from_center = math.hypot(start[0] - marker.x, start[1] - marker.y)
    assert distance_from_center == pytest.approx(marker.radius, abs=0.5)


def test_connector_start_point_faces_the_label_direction():
    # Label is directly to the right of the marker -> the start point should be on the
    # right edge of the circle (x = marker.x + radius, y == marker.y), not the top/left.
    ann, style = _ann_with_label(label_x=1300.0, label_y=990.0, radius=40.0)
    marker = compute_marker_geometry(ann, style)
    label = compute_label_geometry(ann, style)
    points = compute_connector_points(ann, marker, label, ConnectorStyle.STRAIGHT)
    start = points[0]
    assert start[0] > marker.x  # biased toward the label's side, not centered


def test_no_connector_when_label_still_inside_marker_radius():
    ann, style = _ann_with_label(label_x=1005.0, label_y=1000.0, radius=50.0)
    marker = compute_marker_geometry(ann, style)
    label = compute_label_geometry(ann, style)
    assert compute_connector_points(ann, marker, label, ConnectorStyle.STRAIGHT) is None


def test_line_box_entry_point_lands_on_an_edge_not_a_corner_for_diagonal_offset():
    """Regression test: the old nearest-point clamp treated x and y independently, so a
    marker diagonally offset from its label snapped to a box *corner* instead of aiming
    at the label's middle -- confirmed by a real screenshot where the connector visibly
    pointed at a corner of the "NGC7000" label. The entry point must lie exactly on one
    edge (not both x and y pinned to a corner simultaneously) and must be collinear with
    the marker and the box's true center."""
    box = BBox(x0=1100.0, y0=950.0, x1=1220.0, y1=990.0)  # center = (1160, 970)
    px, py = 1000.0, 1000.0  # diagonally below-left of the box
    point = _line_box_entry_point(px, py, 1160.0, 970.0, box)

    on_vertical_edge = point[0] in (box.x0, box.x1)
    on_horizontal_edge = point[1] in (box.y0, box.y1)
    assert on_vertical_edge or on_horizontal_edge
    assert not (
        point[0] in (box.x0, box.x1) and point[1] in (box.y0, box.y1)
    ), "entry point landed exactly on a corner"

    # Collinearity with (px, py) -> (1160, 970): cross product of the two direction
    # vectors should be ~0.
    dx1, dy1 = 1160.0 - px, 970.0 - py
    dx2, dy2 = point[0] - px, point[1] - py
    cross = dx1 * dy2 - dy1 * dx2
    assert abs(cross) < 1e-6


def test_connector_edge_point_visually_aims_at_label_center_for_diagonal_offset():
    """Same scenario end-to-end through compute_connector_points: a marker diagonally
    offset from its label must produce a line aimed at the label's center, not a corner."""
    style = StylePreset(name="test")
    style.marker_style.radius = 20.0
    ann = Annotation(
        catalog="ngc", catalog_name="NGC7000", ra=0.0, dec=0.0,
        image_x=1000.0, image_y=1000.0, label_x=1100.0, label_y=950.0,
    )
    marker = compute_marker_geometry(ann, style)
    label = compute_label_geometry(ann, style)
    points = compute_connector_points(ann, marker, label, ConnectorStyle.STRAIGHT)
    end = points[-1]
    bbox = label.bbox
    is_corner = end[0] in (bbox.x0, bbox.x1) and end[1] in (bbox.y0, bbox.y1)
    assert not is_corner
