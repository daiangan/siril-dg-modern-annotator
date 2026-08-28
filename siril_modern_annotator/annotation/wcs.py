"""WCS handling.

Per RESEARCH.md #4: sirilpy has no direct get_wcs()/CRVAL/CRPIX accessor. The sanctioned
path is to read the raw FITS header text via SirilInterface.get_image_fits_header(),
parse it with sirilpy.utility.parse_fits_header() (which produces an astropy.wcs.WCS
-compatible dict), and build astropy.wcs.WCS ourselves. This module owns that logic and
is the *only* place in the codebase allowed to do sky<->native-pixel math (ARCHITECTURE.md #4).
"""

from __future__ import annotations

import warnings
from dataclasses import dataclass

import numpy as np
from astropy.io.fits.verify import VerifyWarning
from astropy.wcs import WCS


class NotPlateSolvedError(RuntimeError):
    """Raised when a WCS operation is attempted on an image with no astrometric solution."""


@dataclass(frozen=True)
class FieldOfView:
    width_deg: float
    height_deg: float
    center_ra: float
    center_dec: float


class SirilWcs:
    """Wraps an astropy.wcs.WCS built from a Siril FITS header, plus the native image
    dimensions needed to bound coordinate transforms and compute field of view."""

    def __init__(self, wcs: WCS, native_width: int, native_height: int):
        self._wcs = wcs
        self.native_width = native_width
        self.native_height = native_height

    @classmethod
    def from_header_dict(
        cls, header: dict, native_width: int, native_height: int
    ) -> "SirilWcs":
        if not header:
            raise NotPlateSolvedError("Image has no FITS header data.")
        # naxis=2 restricts WCS construction to the two celestial (RA/Dec) axes. Siril's
        # FITS headers for color images carry NAXIS=3 (the third axis being R/G/B
        # channels, not a spatial/WCS axis at all); letting astropy infer the axis count
        # from the header directly makes WCSLIB try to build a 3-axis WCS, which then
        # rejects any SIP distortion terms from plate solving ("FITS WCS distortion...
        # lookup tables... only work in 2 dimensions") -- confirmed by a real ValueError
        # against an actual plate-solved color Siril image during testing.
        # Siril's own custom FITS keywords (SMOOTHING, CORR-TYPE, SAMPLE-SIZE, etc. --
        # background-extraction/plate-solve metadata, not WCS-relevant) are longer than
        # the classic FITS 8-character limit. astropy correctly promotes them to HIERARCH
        # cards and warns about it every time; the warning is expected and harmless here
        # since we only care about the WCS keywords, so it's suppressed rather than left
        # to spam Siril's log on every load (confirmed noisy against a real Siril header).
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", category=VerifyWarning)
            wcs = WCS(header, naxis=2)
        if wcs.naxis == 0 or not wcs.has_celestial:
            raise NotPlateSolvedError(
                "FITS header does not contain a celestial WCS solution."
            )
        return cls(wcs, native_width, native_height)

    def world_to_pixel(self, ra: np.ndarray | float, dec: np.ndarray | float):
        """Vectorized sky -> native pixel. Returns (x, y) arrays/scalars, 0-indexed, in
        *displayed* pixel space (see _flip_y for why)."""
        x, y_raw = self._wcs.wcs_world2pix(np.atleast_1d(ra), np.atleast_1d(dec), 0)
        y = self._flip_y(y_raw)
        if np.isscalar(ra) or (hasattr(ra, "ndim") and getattr(ra, "ndim", 1) == 0):
            return float(x[0]), float(y[0])
        return x, y

    def pixel_to_world(self, x: np.ndarray | float, y: np.ndarray | float):
        """Vectorized native pixel -> sky. x/y are in *displayed* pixel space (see
        _flip_y). Returns (ra, dec) arrays/scalars."""
        y_raw = self._flip_y(np.atleast_1d(y))
        ra, dec = self._wcs.wcs_pix2world(np.atleast_1d(x), y_raw, 0)
        if np.isscalar(x) or (hasattr(x, "ndim") and getattr(x, "ndim", 1) == 0):
            return float(ra[0]), float(dec[0])
        return ra, dec

    def _flip_y(self, y):
        """astropy.wcs.WCS describes the FITS header's own pixel convention (row 0 =
        bottom of the stored data, per FITS's bottom-up ROWORDER). The pixel data we
        actually display has been flipped vertically to appear correctly on screen
        (annotation.pixel_utils.correct_fits_row_order -- confirmed necessary by testing
        against a real Siril image, and matching a real siril-scripts precedent). Every
        annotation coordinate in this codebase is defined in *displayed* pixel space
        (ARCHITECTURE.md #4), so this flip must happen here, once, rather than being
        applied ad hoc by every caller -- confirmed necessary by a real bug where marker
        positions no longer matched the (correctly reoriented) image because only the
        pixel data had been flipped, not the coordinate math."""
        return (self.native_height - 1) - y

    def in_bounds(self, x: np.ndarray, y: np.ndarray, margin_px: float = 0.0) -> np.ndarray:
        """Boolean mask of which native-pixel coordinates fall within the image, plus
        an optional margin (useful for including labels that peek slightly outside the
        frame vs. objects genuinely outside the field)."""
        return (
            (x >= -margin_px)
            & (x < self.native_width + margin_px)
            & (y >= -margin_px)
            & (y < self.native_height + margin_px)
        )

    def field_of_view(self) -> FieldOfView:
        """Compute an approximate FOV (deg) and field center from the four image corners."""
        corners_x = np.array([0, self.native_width, self.native_width, 0])
        corners_y = np.array([0, 0, self.native_height, self.native_height])
        ra, dec = self.pixel_to_world(corners_x, corners_y)
        center_ra, center_dec = self.pixel_to_world(
            self.native_width / 2.0, self.native_height / 2.0
        )
        dec_rad = np.deg2rad(center_dec)
        # Angular separation approximations, adequate for FOV framing (not for precision astrometry).
        ra_span = float(np.max(ra) - np.min(ra)) * np.cos(dec_rad)
        # RA wraps at 0/360; if the naive span looks implausibly large, use the pixel scale instead.
        pixel_scale_deg = self.pixel_scale_deg_per_px()
        if ra_span <= 0 or ra_span > 180:
            ra_span = abs(pixel_scale_deg * self.native_width)
        dec_span = float(np.max(dec) - np.min(dec))
        if dec_span <= 0 or dec_span > 180:
            dec_span = abs(pixel_scale_deg * self.native_height)
        return FieldOfView(
            width_deg=abs(ra_span),
            height_deg=abs(dec_span),
            center_ra=float(center_ra),
            center_dec=float(center_dec),
        )

    def pixel_scale_deg_per_px(self) -> float:
        """Approximate pixel scale in degrees/pixel from the WCS CD/PC matrix."""
        scales = self._wcs.proj_plane_pixel_scales()
        return float(np.mean([s.to("deg").value for s in scales]))

    def pixel_scale_arcsec_per_px(self) -> float:
        return self.pixel_scale_deg_per_px() * 3600.0

    @property
    def astropy_wcs(self) -> WCS:
        return self._wcs
