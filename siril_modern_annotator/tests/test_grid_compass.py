"""RA/Dec grid and compass geometry (brief: "grid lines with RA and DEC, the
compass"). Both are pure functions of an already-built SirilWcs + style, following the
same "geometry only, no Qt/Pillow" convention as every other function in renderer.py --
see its module docstring."""

from __future__ import annotations

import math

import pytest

from siril_modern_annotator.annotation.models import CompassStyle, DecLabelPosition, GridStyle, RaLabelPosition
from siril_modern_annotator.annotation.renderer import (
    _GRID_LABEL_MARGIN_PX,
    _choose_grid_step_deg,
    _format_dec_sexagesimal,
    _format_ra_sexagesimal,
    compute_compass_geometry,
    compute_grid_geometry,
)
from siril_modern_annotator.annotation.wcs import SirilWcs

_WIDTH, _HEIGHT = 2000, 1500
_PIXEL_SCALE_DEG = 1.5 / 3600.0


def _synthetic_header(center_ra=180.0, center_dec=30.0):
    return {
        "NAXIS": 2,
        "NAXIS1": _WIDTH,
        "NAXIS2": _HEIGHT,
        "CTYPE1": "RA---TAN",
        "CTYPE2": "DEC--TAN",
        "CRPIX1": _WIDTH / 2.0,
        "CRPIX2": _HEIGHT / 2.0,
        "CRVAL1": center_ra,
        "CRVAL2": center_dec,
        "CDELT1": -_PIXEL_SCALE_DEG,
        "CDELT2": _PIXEL_SCALE_DEG,
        "CUNIT1": "deg",
        "CUNIT2": "deg",
    }


def _wcs(center_ra=180.0, center_dec=30.0) -> SirilWcs:
    return SirilWcs.from_header_dict(_synthetic_header(center_ra, center_dec), _WIDTH, _HEIGHT)


# --------------------------------------------------------------- step selection ----


def test_choose_grid_step_picks_smallest_sufficient_step():
    # Regression test: _GRID_STEP_CHOICES_DEG must be in ascending order (it's built
    # via sorted(set(...)) specifically because a hand-written literal list interleaved
    # out of order -- e.g. 30/60 == 0.5 appearing before 0.05/0.1/0.25 -- made this
    # "first entry >= ideal" scan pick a much coarser step than intended.
    step = _choose_grid_step_deg(0.8359459812940603)
    assert step == pytest.approx(0.25)


def test_choose_grid_step_is_monotonically_non_decreasing_with_fov():
    prev = 0.0
    for fov in (0.001, 0.01, 0.1, 0.5, 1.0, 5.0, 20.0, 60.0):
        step = _choose_grid_step_deg(fov)
        assert step >= prev
        prev = step


def test_choose_grid_step_handles_degenerate_fov():
    assert _choose_grid_step_deg(0.0) > 0
    assert _choose_grid_step_deg(-1.0) > 0


# ---------------------------------------------------------------- sexagesimal ----


def test_format_ra_sexagesimal_known_values():
    assert _format_ra_sexagesimal(0.0) == "00h00m00.0s"
    assert _format_ra_sexagesimal(180.0) == "12h00m00.0s"
    assert _format_ra_sexagesimal(360.0) == "00h00m00.0s"  # wraps


def test_format_dec_sexagesimal_known_values():
    assert _format_dec_sexagesimal(0.0) == "+00°00′00.0″"
    assert _format_dec_sexagesimal(30.5) == "+30°30′00.0″"
    assert _format_dec_sexagesimal(-5.25) == "-05°15′00.0″"


def test_format_sexagesimal_does_not_display_60_seconds():
    """A naive %.1f on the seconds component can round up to "60.0s" for a value like
    59.96 -- must carry into minutes instead."""
    text = _format_ra_sexagesimal(0.0041666)  # ~59.96 seconds of RA time
    assert "60.0s" not in text


# --------------------------------------------------------------------- grid ----


def test_grid_geometry_empty_when_disabled():
    wcs = _wcs()
    geo = compute_grid_geometry(wcs, GridStyle(enabled=False))
    assert geo.lines == []
    assert geo.labels == []


def test_grid_geometry_produces_lines_within_frame_when_enabled():
    wcs = _wcs()
    geo = compute_grid_geometry(wcs, GridStyle(enabled=True))
    assert len(geo.lines) > 0
    for line in geo.lines:
        assert len(line) >= 2
        for x, y in line:
            assert -1.0 <= x <= _WIDTH + 1.0
            assert -1.0 <= y <= _HEIGHT + 1.0


def test_grid_geometry_finds_lines_at_a_real_declination():
    """Regression test for a real bug: the RA sampling range was computed from
    field_of_view()'s cos(dec)-corrected angular width without converting it back to
    raw RA-degrees, silently under-covering the true RA span and dropping meridians
    near a non-zero declination (30 degrees here)."""
    wcs = _wcs(center_dec=30.0)
    geo = compute_grid_geometry(wcs, GridStyle(enabled=True))
    ra_lines = [l for l in geo.labels if "h" in l.text]
    dec_lines = [l for l in geo.labels if "°" in l.text]
    assert len(ra_lines) >= 2, "expected multiple RA meridians across the frame"
    assert len(dec_lines) >= 2, "expected multiple Dec parallels across the frame"


def test_grid_geometry_no_labels_when_show_labels_false():
    wcs = _wcs()
    geo = compute_grid_geometry(wcs, GridStyle(enabled=True, show_labels=False))
    assert len(geo.lines) > 0
    assert geo.labels == []


def test_grid_labels_stay_within_frame_margin():
    """Regression test for a real report: Dec labels landed right at (or past) the
    image edge, so the exported text was partly or fully cut off. Every label's anchor
    point must be inset from the frame edge by at least the configured margin."""
    wcs = _wcs()
    geo = compute_grid_geometry(wcs, GridStyle(enabled=True))
    assert len(geo.labels) > 0
    for label in geo.labels:
        assert _GRID_LABEL_MARGIN_PX - 1e-6 <= label.x <= _WIDTH - _GRID_LABEL_MARGIN_PX + 1e-6
        assert _GRID_LABEL_MARGIN_PX - 1e-6 <= label.y <= _HEIGHT - _GRID_LABEL_MARGIN_PX + 1e-6


def test_ra_labels_prefer_top_edge_by_default():
    wcs = _wcs()
    geo = compute_grid_geometry(wcs, GridStyle(enabled=True))
    ra_labels = [l for l in geo.labels if l.axis == "ra"]
    assert ra_labels
    for label in ra_labels:
        assert label.y < _HEIGHT / 2.0, "RA labels should default to the top edge"


def test_ra_labels_move_to_bottom_when_configured():
    wcs = _wcs()
    geo = compute_grid_geometry(wcs, GridStyle(enabled=True, ra_label_position=RaLabelPosition.BOTTOM))
    ra_labels = [l for l in geo.labels if l.axis == "ra"]
    assert ra_labels
    for label in ra_labels:
        assert label.y > _HEIGHT / 2.0


def test_dec_labels_prefer_right_edge_by_default():
    wcs = _wcs()
    geo = compute_grid_geometry(wcs, GridStyle(enabled=True))
    dec_labels = [l for l in geo.labels if l.axis == "dec"]
    assert dec_labels
    for label in dec_labels:
        assert label.x > _WIDTH / 2.0, "Dec labels should default to the right edge"


def test_dec_labels_move_to_left_when_configured():
    wcs = _wcs()
    geo = compute_grid_geometry(wcs, GridStyle(enabled=True, dec_label_position=DecLabelPosition.LEFT))
    dec_labels = [l for l in geo.labels if l.axis == "dec"]
    assert dec_labels
    for label in dec_labels:
        assert label.x < _WIDTH / 2.0


# ------------------------------------------------------------------ compass ----


def test_compass_geometry_none_when_disabled():
    wcs = _wcs()
    assert compute_compass_geometry(wcs, CompassStyle(enabled=False)) is None


def test_compass_geometry_defaults_to_bottom_right():
    wcs = _wcs()
    geo = compute_compass_geometry(wcs, CompassStyle(enabled=True))
    assert geo is not None
    assert geo.anchor[0] > _WIDTH / 2.0
    assert geo.anchor[1] > _HEIGHT / 2.0


def test_compass_geometry_respects_anchor_override():
    wcs = _wcs()
    geo = compute_compass_geometry(wcs, CompassStyle(enabled=True, anchor_x=200.0, anchor_y=300.0))
    assert geo is not None
    assert geo.anchor == (200.0, 300.0)


def test_compass_arrow_lengths_match_configured_fraction():
    wcs = _wcs()
    style = CompassStyle(enabled=True, arrow_length_fraction=0.1)
    geo = compute_compass_geometry(wcs, style)
    expected_len = min(_WIDTH, _HEIGHT) * 0.1
    north_len = math.hypot(geo.north_end[0] - geo.anchor[0], geo.north_end[1] - geo.anchor[1])
    east_len = math.hypot(geo.east_end[0] - geo.anchor[0], geo.east_end[1] - geo.anchor[1])
    assert north_len == pytest.approx(expected_len, rel=1e-3)
    assert east_len == pytest.approx(expected_len, rel=1e-3)


def test_compass_north_and_east_are_roughly_perpendicular_for_an_unrotated_wcs():
    """This synthetic header has no CROTA/PC rotation, so North should point in a
    (near) constant screen direction and East should be (near) perpendicular to it."""
    wcs = _wcs()
    geo = compute_compass_geometry(wcs, CompassStyle(enabled=True))
    nx, ny = geo.north_end[0] - geo.anchor[0], geo.north_end[1] - geo.anchor[1]
    ex, ey = geo.east_end[0] - geo.anchor[0], geo.east_end[1] - geo.anchor[1]
    dot = nx * ex + ny * ey
    n_len = math.hypot(nx, ny)
    e_len = math.hypot(ex, ey)
    cos_angle = dot / (n_len * e_len)
    assert abs(cos_angle) < 0.05  # close to perpendicular (cos ~ 0)
