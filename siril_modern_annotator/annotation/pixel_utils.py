"""Normalizes raw pixel arrays from sirilpy into a predictable HxWx3 uint8 RGB array.

Why this exists: RESEARCH.md #3 flagged a real, confirmed axis-order discrepancy between
two different sirilpy accessors -- SirilInterface.get_image_shape() returns
(channels, height, width), while FFit.data.shape uses (height, width, channels) -- and
we could not find documentation pinning down which convention
get_image_pixeldata()'s *return array* itself follows in every case, nor its exact dtype
range for `preview=True` versus raw data. A real test against an actual plate-solved
Siril image showed the preview rendering incorrectly, consistent with this ambiguity.
Rather than hard-code an assumption, this module detects channel position from the
array's own shape (the channel axis is whichever axis has size <= 4, since real image
dimensions are essentially never that small) and normalizes dtype defensively, so both
the GUI preview and the full-resolution exporter go through one tested code path instead
of two independent, unverified assumptions.
"""

from __future__ import annotations

import numpy as np

_MAX_CHANNELS = 4


def to_hwc_uint8(data) -> np.ndarray:
    """Returns a contiguous (H, W, 3) uint8 array from arbitrary sirilpy pixel data:
    mono or RGB, channels-first or channels-last, uint8/uint16/float in any range."""
    arr = np.asarray(data)

    if arr.ndim == 2:
        arr = arr[:, :, None]
    elif arr.ndim == 3:
        if arr.shape[-1] > _MAX_CHANNELS and arr.shape[0] <= _MAX_CHANNELS:
            arr = np.moveaxis(arr, 0, -1)
        # else: already channels-last, or ambiguous (leave as-is -- a square-ish tiny
        # image is not a realistic input here).
    else:
        raise ValueError(f"Unsupported pixel data shape: {arr.shape}")

    arr = _to_uint8(arr)

    channels = arr.shape[-1]
    if channels == 1:
        arr = np.repeat(arr, 3, axis=2)
    elif channels >= 3:
        arr = arr[:, :, :3]

    return np.ascontiguousarray(arr)


def correct_fits_row_order(hwc_array: np.ndarray) -> np.ndarray:
    """Flips an already-HWC array vertically to correct for FITS's bottom-up row
    storage convention. Real evidence for this, not a guess: (1) `sirilpy`'s own docs
    describe an `FFit.top_down` field distinguishing FITS's native bottom-up ROWORDER
    from top-down sensors, meaning orientation is left for the caller to handle, not
    silently pre-corrected; (2) a real, working Siril PyQt6 script in the official
    siril-scripts repo (`utility/ImageWindow.py`, `ZoomableImageLabel.set_image_data`)
    applies exactly this same correction -- `qimage.mirrored(False, True)`, vertical
    only -- right after pulling pixel data from Siril, with the comment "Flip vertically
    to match FITS ROWORDER convention (like Siril does)". We were applying no such
    correction at all before this fix."""
    return np.ascontiguousarray(np.flipud(hwc_array))


def _to_uint8(arr: np.ndarray) -> np.ndarray:
    if arr.dtype == np.uint8:
        return arr
    if arr.dtype == np.uint16:
        return _linear_scale(arr, scale_max=65535.0)
    if np.issubdtype(arr.dtype, np.floating):
        finite = arr[np.isfinite(arr)]
        # Siril's float pixel data is typically normalized to [0, 1], but treat it
        # generically: only assume the [0,1] convention when the data actually stays
        # within it; otherwise fall back to the observed range. This is only picking
        # *which* fixed physical convention applies, not deriving an adaptive black/
        # white point from the histogram -- see _linear_scale's docstring.
        scale_max = 1.0 if (finite.size == 0 or float(np.max(finite)) <= 1.0 + 1e-6) else float(np.max(finite))
        return _linear_scale(arr, scale_max=scale_max)
    # Any other integer type: scale from its dtype's max representable value.
    info = np.iinfo(arr.dtype)
    return _linear_scale(arr, scale_max=float(info.max))


def _linear_scale(arr: np.ndarray, scale_max: float) -> np.ndarray:
    """Literal fixed-range linear mapping [0, scale_max] -> [0, 255] -- no histogram-
    derived black/white point, no percentile clipping. This module previously (briefly)
    used a 0.25th/99.75th-percentile clip here to work around exports that looked all-
    black, but that clip is itself a crude form of auto-stretch: it picks black/white
    points from the data's own distribution, exactly like an autostretch algorithm
    does. Confirmed by a real user report comparing this app's "Linear" preview against
    Siril's own linear (non-autostretched) display of the same image: with the
    percentile clip in place, our "Linear" mode visibly did NOT match Siril's -- it
    looked stretched/brightened, because it was, secretly, doing its own auto-stretch.
    Siril's own linear view is anchored to the data's fixed numeric range (e.g. literal
    0..65535 for uint16), not an adaptive percentile window, and genuinely does look
    dim for raw/unstretched linear data -- that dimness is correct, expected behavior
    for "Linear" mode, not a bug. Auto Stretch mode remains available (and unaffected by
    this function -- it renders Siril's own get_image_pixeldata(preview=True) output
    directly) for anyone who wants the brighter, stretched look."""
    data = np.where(np.isfinite(arr), arr, 0).astype(np.float32)
    if scale_max <= 0:
        scale_max = 1.0
    scaled = data / scale_max * 255.0
    return np.clip(scaled, 0, 255).astype(np.uint8)
