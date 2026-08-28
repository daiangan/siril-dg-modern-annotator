"""ELLIPSE marker shape: an elongated/rotatable oval for fitting an irregular galaxy,
rather than the fixed circular radius every other shape uses. Two things need to stay
consistent with each other: what's actually drawn (gui/annotation_item.py's
MarkerItem.paint, export/exporter.py's _draw_marker -- both draw an ellipse of
radius_x/radius_y rotated by rotation_deg) and where the connector line is allowed to
touch it (_ellipse_anchor_point below) -- see _bracket_anchor_point's docstring for the
same kind of bug this guards against for a different non-circular shape."""

from __future__ import annotations

import math

import pytest

from siril_modern_annotator.annotation.models import Annotation, ConnectorStyle, MarkerShape, MarkerStyle, StylePreset
from siril_modern_annotator.annotation.renderer import (
    _ellipse_anchor_point,
    compute_connector_points,
    compute_label_geometry,
    compute_marker_geometry,
)


def test_ellipse_anchor_point_unrotated_along_major_axis():
    x, y = _ellipse_anchor_point(radius_x=30.0, radius_y=10.0, rotation_deg=0.0, ux=1.0, uy=0.0)
    assert x == pytest.approx(30.0)
    assert y == pytest.approx(0.0)


def test_ellipse_anchor_point_unrotated_along_minor_axis():
    x, y = _ellipse_anchor_point(radius_x=30.0, radius_y=10.0, rotation_deg=0.0, ux=0.0, uy=1.0)
    assert x == pytest.approx(0.0)
    assert y == pytest.approx(10.0)


def test_ellipse_anchor_point_lands_on_the_ellipse_boundary_at_various_angles():
    """For any direction, the returned point (x, y) must satisfy the rotated ellipse's
    boundary equation -- i.e. actually be on the drawn oval, not some other distance."""
    rx, ry, rotation = 40.0, 15.0, 25.0
    theta = math.radians(rotation)
    cos_t, sin_t = math.cos(theta), math.sin(theta)
    for angle_deg in range(0, 360, 10):
        angle = math.radians(angle_deg)
        ux, uy = math.cos(angle), math.sin(angle)
        x, y = _ellipse_anchor_point(rx, ry, rotation, ux, uy)
        # Un-rotate the point back into the ellipse's own local frame and check it
        # satisfies (x/rx)^2 + (y/ry)^2 == 1.
        local_x = x * cos_t + y * sin_t
        local_y = -x * sin_t + y * cos_t
        assert (local_x / rx) ** 2 + (local_y / ry) ** 2 == pytest.approx(1.0, abs=1e-6), (
            f"angle={angle_deg} landed off the ellipse boundary"
        )


def test_ellipse_anchor_point_90_degree_rotation_swaps_effective_axes():
    """Rotating the ellipse 90 degrees means "straight up" now hits what was the major
    axis -- confirms rotation_deg is actually applied, not silently ignored."""
    x, y = _ellipse_anchor_point(radius_x=30.0, radius_y=10.0, rotation_deg=90.0, ux=0.0, uy=1.0)
    assert math.hypot(x, y) == pytest.approx(30.0, abs=1e-6)


def test_ellipse_anchor_point_zero_radius_or_zero_direction_is_safe():
    assert _ellipse_anchor_point(0.0, 10.0, 0.0, 1.0, 0.0) == (0.0, 0.0)
    assert _ellipse_anchor_point(10.0, 10.0, 0.0, 0.0, 0.0) == (0.0, 0.0)


def _ellipse_style(radius_x=30.0, radius_y=10.0, rotation_deg=0.0, size_from_angular_size=False) -> StylePreset:
    style = StylePreset(name="test")
    style.marker_style = MarkerStyle(
        shape=MarkerShape.ELLIPSE, radius_x=radius_x, radius_y=radius_y, rotation_deg=rotation_deg,
        size_from_angular_size=size_from_angular_size,
    )
    return style


def _ann(**overrides) -> Annotation:
    return Annotation(
        catalog="messier", catalog_name="M31", ra=0.0, dec=0.0,
        image_x=1000.0, image_y=1000.0, **overrides,
    )


def test_compute_marker_geometry_ellipse_uses_radius_x_radius_y_and_rotation():
    ann = _ann()
    geo = compute_marker_geometry(ann, _ellipse_style(radius_x=30.0, radius_y=10.0, rotation_deg=25.0))
    assert (geo.radius_x, geo.radius_y, geo.rotation_deg) == (30.0, 10.0, 25.0)
    assert geo.radius == 30.0  # circular-equivalent: max(radius_x, radius_y)


def test_compute_marker_geometry_non_ellipse_radius_x_y_mirror_radius():
    ann = _ann()
    style = StylePreset(name="test")
    style.marker_style.shape = MarkerShape.CIRCLE
    style.marker_style.radius = 22.0
    geo = compute_marker_geometry(ann, style)
    assert (geo.radius_x, geo.radius_y, geo.rotation_deg) == (22.0, 22.0, 0.0)


def test_compute_marker_geometry_ellipse_ignores_angular_size_auto_scaling():
    """Ellipse is manual-only (see MarkerStyle.radius_x's docstring) -- unlike every
    other shape, a huge angular_size must never inflate radius_x/radius_y."""
    ann = _ann(angular_size=178.0)  # M31's real ~178' extent
    style = _ellipse_style(radius_x=30.0, radius_y=10.0, size_from_angular_size=True)
    geo = compute_marker_geometry(ann, style, arcsec_per_px=1.5)
    assert (geo.radius_x, geo.radius_y) == (30.0, 10.0)


def test_connector_start_point_for_ellipse_marker_lands_on_the_boundary():
    """Full pipeline: an ELLIPSE-shaped marker's connector must start on the actual
    drawn (rotated) oval, not assume a circular radius."""
    rx, ry, rotation = 40.0, 15.0, 20.0
    style = _ellipse_style(radius_x=rx, radius_y=ry, rotation_deg=rotation)
    ann = _ann(label_x=1000.0, label_y=800.0)  # roughly above
    marker = compute_marker_geometry(ann, style)
    label = compute_label_geometry(ann, style)
    points = compute_connector_points(ann, marker, label, ConnectorStyle.STRAIGHT)
    assert points is not None
    start = points[0]
    off_x, off_y = start[0] - marker.x, start[1] - marker.y

    theta = math.radians(rotation)
    cos_t, sin_t = math.cos(theta), math.sin(theta)
    local_x = off_x * cos_t + off_y * sin_t
    local_y = -off_x * sin_t + off_y * cos_t
    assert (local_x / rx) ** 2 + (local_y / ry) ** 2 == pytest.approx(1.0, abs=1e-6)
