"""to_hwc_uint8 must normalize every pixel-array shape/dtype combination sirilpy could
plausibly hand back, since RESEARCH.md #3 could not pin down get_image_pixeldata()'s
exact channel-axis convention and dtype range from docs alone -- this was confirmed to
matter by a real broken preview against an actual Siril image."""

from __future__ import annotations

import numpy as np

from siril_modern_annotator.annotation.pixel_utils import correct_fits_row_order, to_hwc_uint8


def test_mono_2d_uint8_expanded_to_rgb():
    arr = np.full((10, 20), 128, dtype=np.uint8)
    out = to_hwc_uint8(arr)
    assert out.shape == (10, 20, 3)
    assert out.dtype == np.uint8
    assert np.all(out == 128)


def test_channels_last_rgb_uint8_passthrough():
    arr = np.zeros((10, 20, 3), dtype=np.uint8)
    arr[:, :, 0] = 200
    out = to_hwc_uint8(arr)
    assert out.shape == (10, 20, 3)
    assert np.all(out[:, :, 0] == 200)


def test_channels_first_rgb_is_transposed_to_channels_last():
    arr = np.zeros((3, 10, 20), dtype=np.uint8)
    arr[0, :, :] = 200  # red channel, channels-first layout
    out = to_hwc_uint8(arr)
    assert out.shape == (10, 20, 3)
    assert np.all(out[:, :, 0] == 200)
    assert np.all(out[:, :, 1] == 0)


def test_uint16_scaled_down_to_uint8_range():
    arr = np.full((5, 5, 3), 65535, dtype=np.uint16)
    out = to_hwc_uint8(arr)
    assert out.dtype == np.uint8
    assert out.max() == 255


def test_float_0_to_1_scaled_to_uint8_range():
    arr = np.full((5, 5, 3), 0.5, dtype=np.float32)
    out = to_hwc_uint8(arr)
    assert out.dtype == np.uint8
    assert 126 <= int(out[0, 0, 0]) <= 129


def test_uint16_scales_against_literal_dtype_max_not_percentile_clip():
    """Regression test for a real user report: this app's "Linear" preview did not
    visually match Siril's own linear (non-autostretched) display of the same image.
    Root cause -- a previous version of this function used a 0.25th/99.75th-percentile
    clip to pick black/white points, which is itself a crude auto-stretch and does not
    match Siril's own linear view, which is anchored to the data's literal fixed range
    (0..65535 for uint16). Real unstretched linear astro data occupying only a small
    slice of the 16-bit range (background a few thousand ADU out of 65535) is *supposed*
    to look dim under a literal scale -- that dimness matches Siril, it isn't a bug.
    Auto Stretch mode (Siril's own get_image_pixeldata(preview=True) output) is the
    correct place to reach for a brighter, stretched look."""
    rng = np.random.default_rng(0)
    background = rng.normal(2000, 100, size=(200, 200, 3)).clip(0, 65535)
    arr = background.astype(np.uint16)
    arr[50:55, 50:55, :] = 20000  # a handful of "star" pixels
    out = to_hwc_uint8(arr)
    expected_bg = round(2000 / 65535 * 255)
    assert abs(int(out[0, 0, 0]) - expected_bg) <= 2, (
        f"background should track a literal 65535->255 scale (~{expected_bg}), got {out[0, 0, 0]}"
    )
    assert out[52, 52, 0] > out[0, 0, 0]  # the bright "star" pixels still stand out


def test_uint16_single_outlier_pixel_does_not_affect_other_pixels():
    """A literal fixed-range scale must be entirely per-pixel -- one saturated/hot pixel
    must not change how any *other* pixel renders, unlike an adaptive percentile/min-max
    stretch (which this function deliberately no longer does; see the test above)."""
    rng = np.random.default_rng(1)
    background = rng.normal(3000, 50, size=(50, 50, 3)).clip(0, 65535)
    arr = background.astype(np.uint16)
    without_outlier = to_hwc_uint8(arr.copy())
    arr[0, 0, :] = 65535  # one extreme outlier pixel
    with_outlier = to_hwc_uint8(arr)
    assert with_outlier[0, 0, 0] == 255
    assert np.array_equal(with_outlier[1:, 1:, :], without_outlier[1:, 1:, :])


def test_mono_channels_first_single_leading_axis():
    arr = np.full((1, 10, 20), 90, dtype=np.uint8)
    out = to_hwc_uint8(arr)
    assert out.shape == (10, 20, 3)
    assert np.all(out == 90)


def test_output_is_contiguous():
    arr = np.zeros((3, 10, 20), dtype=np.uint8)
    out = to_hwc_uint8(arr)
    assert out.flags["C_CONTIGUOUS"]


def test_correct_fits_row_order_flips_vertically_only():
    """FITS stores rows bottom-up; a real Siril PyQt6 script (ImageWindow.py) applies
    exactly this vertical-only correction after pulling pixel data from Siril."""
    arr = np.zeros((4, 6, 3), dtype=np.uint8)
    arr[0, :, :] = 10   # top row (as stored)
    arr[-1, :, :] = 200  # bottom row (as stored)
    out = correct_fits_row_order(arr)
    assert np.all(out[0, :, :] == 200)  # was bottom, now displayed first (top)
    assert np.all(out[-1, :, :] == 10)
    # Column order must be untouched -- this is a vertical-only correction.
    assert out.shape == arr.shape
    assert out.flags["C_CONTIGUOUS"]


def test_correct_fits_row_order_does_not_touch_columns():
    arr = np.zeros((4, 6, 3), dtype=np.uint8)
    arr[:, 0, :] = 10   # leftmost column
    arr[:, -1, :] = 200  # rightmost column
    out = correct_fits_row_order(arr)
    assert np.all(out[:, 0, :] == 10)
    assert np.all(out[:, -1, :] == 200)
