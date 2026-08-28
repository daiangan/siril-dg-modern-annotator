"""Known RA/Dec -> expected native pixel position, and the inverse (brief #35)."""

from __future__ import annotations

import pytest

from siril_modern_annotator.annotation.wcs import NotPlateSolvedError, SirilWcs

_WIDTH, _HEIGHT = 4000, 3000
_PIXEL_SCALE_DEG = 1.5 / 3600.0  # 1.5"/px


def _synthetic_header(center_ra=310.0, center_dec=41.0):
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


def _wcs() -> SirilWcs:
    return SirilWcs.from_header_dict(_synthetic_header(), _WIDTH, _HEIGHT)


def test_crval_round_trips_through_world_to_pixel_and_back():
    # world_to_pixel/pixel_to_world work in *displayed* pixel space (vertically flipped
    # from the FITS header's own raw convention, to match the displayed image -- see
    # SirilWcs._flip_y). Rather than hardcode the flipped index arithmetic here, this
    # just confirms self-consistency: converting CRVAL to display pixels and back
    # recovers CRVAL exactly.
    wcs = _wcs()
    x, y = wcs.world_to_pixel(310.0, 41.0)
    ra, dec = wcs.pixel_to_world(x, y)
    assert ra == pytest.approx(310.0, abs=1e-6)
    assert dec == pytest.approx(41.0, abs=1e-6)


def test_display_y_is_vertically_flipped_from_raw_fits_y():
    """Regression test: marker positions must be computed in the same vertically-
    flipped pixel space as the displayed image (annotation.pixel_utils.
    correct_fits_row_order) -- confirmed necessary by a real bug where fixing the
    image's own orientation, without also flipping the coordinate math, left markers
    misaligned from the real stars/objects they were supposed to point at."""
    wcs = _wcs()
    x, y = wcs.world_to_pixel(310.0, 41.0)
    raw_x, raw_y = wcs.astropy_wcs.wcs_world2pix([310.0], [41.0], 0)
    assert x == pytest.approx(raw_x[0], abs=1e-3)  # x is untouched
    assert y == pytest.approx((_HEIGHT - 1) - raw_y[0], abs=1e-3)  # y is flipped


def test_round_trip_world_to_pixel_and_back():
    wcs = _wcs()
    x0, y0 = 1234.0, 987.0
    ra, dec = wcs.pixel_to_world(x0, y0)
    x1, y1 = wcs.world_to_pixel(ra, dec)
    assert x1 == pytest.approx(x0, abs=1e-3)
    assert y1 == pytest.approx(y0, abs=1e-3)


def test_pixel_scale_matches_input():
    wcs = _wcs()
    arcsec_per_px = wcs.pixel_scale_arcsec_per_px()
    assert arcsec_per_px == pytest.approx(1.5, abs=1e-3)


def test_in_bounds_true_for_center_false_far_outside():
    wcs = _wcs()
    import numpy as np

    x = np.array([_WIDTH / 2.0, -10000.0])
    y = np.array([_HEIGHT / 2.0, -10000.0])
    mask = wcs.in_bounds(x, y)
    assert mask[0] and not mask[1]


def test_field_of_view_is_plausible():
    wcs = _wcs()
    fov = wcs.field_of_view()
    expected_width_deg = _WIDTH * _PIXEL_SCALE_DEG
    expected_height_deg = _HEIGHT * _PIXEL_SCALE_DEG
    assert fov.width_deg == pytest.approx(expected_width_deg, rel=0.05)
    assert fov.height_deg == pytest.approx(expected_height_deg, rel=0.05)
    assert fov.center_ra == pytest.approx(310.0, abs=1e-3)
    assert fov.center_dec == pytest.approx(41.0, abs=1e-3)


def test_missing_wcs_raises_not_plate_solved():
    with pytest.raises(NotPlateSolvedError):
        SirilWcs.from_header_dict({}, _WIDTH, _HEIGHT)


def test_header_without_celestial_axes_raises_not_plate_solved():
    header = {"NAXIS": 2, "NAXIS1": _WIDTH, "NAXIS2": _HEIGHT}
    with pytest.raises(NotPlateSolvedError):
        SirilWcs.from_header_dict(header, _WIDTH, _HEIGHT)


def test_color_image_header_with_third_axis_and_sip_distortion():
    """Regression test for a real failure seen against an actual plate-solved Siril
    color image: NAXIS=3 (the 3rd axis being R/G/B channels, not spatial) combined with
    SIP distortion terms made astropy try to build a 3-axis WCS and reject the SIP terms
    ("only work in 2 dimensions"). from_header_dict must restrict to the 2 celestial axes."""
    header = _synthetic_header()
    header.update(
        {
            "NAXIS": 3,
            "NAXIS3": 3,
            # Minimal SIP distortion terms, same shape as what a real plate-solved
            # Siril header carries -- these are what triggered WCSLIB's 2D-only check.
            "CTYPE1": "RA---TAN-SIP",
            "CTYPE2": "DEC--TAN-SIP",
            "A_ORDER": 2,
            "A_0_2": 1e-6,
            "B_ORDER": 2,
            "B_0_2": 1e-6,
        }
    )
    wcs = SirilWcs.from_header_dict(header, _WIDTH, _HEIGHT)
    assert wcs.astropy_wcs.naxis == 2
    x, y = wcs.world_to_pixel(310.0, 41.0)
    ra, dec = wcs.pixel_to_world(x, y)
    assert ra == pytest.approx(310.0, abs=1e-3)
    assert dec == pytest.approx(41.0, abs=1e-3)
