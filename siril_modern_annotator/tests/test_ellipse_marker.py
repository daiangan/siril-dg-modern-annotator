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


# --- Galaxy-shape auto-ellipse (see annotation/catalogs.py's galaxy-shape-enrichment ----
# --- section) -- per explicit user request/GitHub issue #9, a galaxy with real SGA2020/-
# --- HyperLeda isophote data should render as an oriented ellipse automatically, not -----
# --- the flat circle every other object defaults to, without requiring the user to --------
# --- manually switch its shape to ELLIPSE first. -------------------------------------------


def _galaxy_ann(**overrides) -> Annotation:
    fields = dict(
        object_type="galaxy",
        galaxy_major_axis_arcmin=13.5, galaxy_minor_axis_arcmin=11.6, galaxy_position_angle_screen_deg=28.0,
    )
    fields.update(overrides)
    return _ann(**fields)


def test_galaxy_shape_data_auto_renders_as_an_oriented_ellipse():
    ann = _galaxy_ann()
    style = StylePreset(name="test")  # default marker shape is CIRCLE
    geo = compute_marker_geometry(ann, style, arcsec_per_px=1.5)
    assert geo.style.shape is MarkerShape.ELLIPSE
    assert geo.rotation_deg == 28.0
    # major_arcmin(13.5) * 60 / 2 / arcsec_per_px(1.5) = 270.0
    assert geo.radius_x == pytest.approx(270.0)
    # minor_arcmin(11.6) * 60 / 2 / 1.5 = 232.0
    assert geo.radius_y == pytest.approx(232.0)
    assert geo.radius == geo.radius_x  # circular-equivalent uses the major axis


def test_galaxy_shape_auto_ellipse_still_resolves_catalog_color():
    """Regression guard: an earlier version of this feature stored the auto-shape as a
    real marker_style override, which silently broke per-catalog marker coloring (see
    resolve_marker_color: once ann.marker_style is set, catalog_colors is never
    consulted at all). galaxy_major_axis_arcmin/etc. must stay plain catalog *data*
    (ann.marker_style itself untouched) so color keeps resolving normally."""
    ann = _galaxy_ann()
    style = StylePreset(name="test")
    geo = compute_marker_geometry(ann, style, arcsec_per_px=1.5, catalog_colors={"messier": "#F2C572"})
    assert geo.style.color == "#F2C572"


def test_galaxy_shape_data_ignored_without_arcsec_per_px():
    ann = _galaxy_ann()
    style = StylePreset(name="test")
    geo = compute_marker_geometry(ann, style)  # arcsec_per_px defaults to None
    assert geo.style.shape is MarkerShape.CIRCLE


def test_galaxy_shape_data_ignored_when_incomplete():
    # Only two of the three fields present -- must not half-apply an ellipse.
    ann = _galaxy_ann(galaxy_position_angle_screen_deg=None)
    style = StylePreset(name="test")
    geo = compute_marker_geometry(ann, style, arcsec_per_px=1.5)
    assert geo.style.shape is MarkerShape.CIRCLE


def test_manual_marker_style_override_beats_galaxy_shape_auto_ellipse():
    """Per explicit user decision: the auto-ellipse must stay fully editable/override-
    able -- a real per-object marker_style (the user manually customizing this specific
    object, e.g. via the Style panel's "Selected Object" tab) always wins, same
    precedence as every other per-object override in this app."""
    ann = _galaxy_ann(marker_style=MarkerStyle(shape=MarkerShape.CIRCLE, radius=9.0))
    style = StylePreset(name="test")
    geo = compute_marker_geometry(ann, style, arcsec_per_px=1.5)
    assert geo.style.shape is MarkerShape.CIRCLE
    assert geo.radius == 9.0


def test_galaxy_shape_auto_ellipse_respects_max_radius_px_cap():
    ann = _galaxy_ann(galaxy_major_axis_arcmin=178.0, galaxy_minor_axis_arcmin=63.0)  # M31's real full extent
    style = StylePreset(name="test")
    geo = compute_marker_geometry(ann, style, arcsec_per_px=1.5, max_radius_px=100.0)
    assert geo.radius_x == pytest.approx(100.0)
    # Both axes scaled by the same factor -- the ellipse's aspect ratio must be
    # preserved, not just the major axis clamped independently.
    assert geo.radius_y == pytest.approx(100.0 * (63.0 / 178.0))


def test_connector_appears_for_a_large_eccentric_ellipse_label_near_the_minor_axis():
    """Regression test for a real report: dragging a galaxy's label produced no
    connector at all. Root cause -- the "attached, skip the connector" check compared
    the label's distance to marker.radius, which for ELLIPSE is max(radius_x,
    radius_y) (the *longest* axis). For a large, eccentric marker like a galaxy's
    fitted ellipse (radius_x/radius_y can differ by 3x or more), a label dragged well
    clear of the drawn oval along the *minor* axis direction could still fall well
    inside that longest-axis radius and be wrongly treated as still attached. The
    check must use the true ellipse boundary distance in the label's own direction."""
    rx, ry, rotation = 2000.0, 700.0, 0.0  # M31-scale: a highly eccentric marker
    style = _ellipse_style(radius_x=rx, radius_y=ry, rotation_deg=rotation)
    # Label placed straight above the marker (along the *minor* axis at rotation=0,
    # where the drawn boundary is only ~ry away) at a distance clearly outside the
    # drawn oval (ry=700) but still well inside the longest-axis radius (rx=2000) --
    # exactly the gap the old flat marker.radius check got wrong.
    ann = _ann(label_x=1000.0, label_y=1000.0 - 900.0)  # marker at (1000, 1000)
    marker = compute_marker_geometry(ann, style)
    label = compute_label_geometry(ann, style)
    points = compute_connector_points(ann, marker, label, ConnectorStyle.STRAIGHT)
    assert points is not None, "a label clearly outside the drawn ellipse must get a connector"


def test_connector_still_hidden_when_label_is_truly_inside_a_large_ellipse():
    """The fix above must not make every large-ellipse label always get a connector --
    a label that's still genuinely inside (or right at) the drawn boundary in its own
    direction must still count as attached, same as any other shape."""
    rx, ry, rotation = 2000.0, 700.0, 0.0
    style = _ellipse_style(radius_x=rx, radius_y=ry, rotation_deg=rotation)
    ann = _ann(label_x=1000.0, label_y=1000.0 - 100.0)  # well inside ry=700
    marker = compute_marker_geometry(ann, style)
    label = compute_label_geometry(ann, style)
    points = compute_connector_points(ann, marker, label, ConnectorStyle.STRAIGHT)
    assert points is None


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
