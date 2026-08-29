"""Thin wrapper around sirilpy.SirilInterface.

Per RESEARCH.md #5 and ARCHITECTURE.md #10, no method on this class is guaranteed
thread-safe by Siril's own documentation, so by policy every method here must only ever
be called from the Qt main thread. Worker threads receive plain data (numpy arrays,
dataclasses) produced by these calls — never a reference to this object or to
sirilpy itself.

This module assumes `import sirilpy` has already succeeded (see modern_annotator.py,
which must ensure_installed() our own dependencies via sirilpy *before* importing
PyQt6/astropy/etc., since sirilpy is what provides ensure_installed in the first place).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

import numpy as np

logger = logging.getLogger(__name__)

MINIMUM_SIRIL_VERSION = "1.4.0"
# No sirilpy module-version floor is enforced here. RESEARCH.md's ">=1.1.14" figure came
# from the ReadTheDocs "latest" branch, which tracks Siril 1.5-dev -- not what actually
# ships with 1.4.x stable (real-world prior art, e.g. siril-scripts/utility/
# Galaxy_Annotations.py, gates on sirilpy >=0.6.37 for Siril 1.4.0-beta2, nowhere near
# 1.1.x). Enforcing a guessed floor here caused a false rejection on real Siril 1.4.4.
# The siril.cmd("requires", ...) check below is the one documented, reliable gate; any
# individual sirilpy method this bridge depends on that turns out to be missing on an
# older install will raise its own AttributeError/exception at the call site, which is
# surfaced to the user as an error dialog rather than guessed at up front.


class SirilBridgeError(RuntimeError):
    """Raised for any failure connecting to or querying Siril."""


class NoImageLoadedError(SirilBridgeError):
    pass


class NotPlateSolvedError(SirilBridgeError):
    pass


@dataclass(frozen=True)
class ImageInfo:
    width: int
    height: int
    channels: int
    plate_solved: bool
    object_name: str | None
    instrument: str | None
    telescope: str | None


class SirilBridge:
    """Main-thread-only facade over sirilpy.SirilInterface, exposing exactly what
    Siril Modern Annotator needs (image access, header/WCS-source data, STF/preview,
    ICC, and the system catalogue directory) — nothing invented beyond RESEARCH.md."""

    def __init__(self):
        self._siril = None

    def connect(self) -> None:
        import sirilpy as s
        from sirilpy import SirilConnectionError

        self._siril = s.SirilInterface()
        try:
            self._siril.connect()
        except SirilConnectionError as exc:
            raise SirilBridgeError(f"Could not connect to Siril: {exc}") from exc

        try:
            self._siril.cmd("requires", MINIMUM_SIRIL_VERSION)
        except Exception as exc:  # CommandError if the running Siril is too old
            raise SirilBridgeError(
                f"This script requires Siril >= {MINIMUM_SIRIL_VERSION}: {exc}"
            ) from exc

    @property
    def connected(self) -> bool:
        return self._siril is not None

    def _require_connection(self):
        if self._siril is None:
            raise SirilBridgeError("Not connected to Siril. Call connect() first.")
        return self._siril

    def is_image_loaded(self) -> bool:
        return bool(self._require_connection().is_image_loaded())

    def get_image_info(self) -> ImageInfo:
        siril = self._require_connection()
        if not siril.is_image_loaded():
            raise NoImageLoadedError("No image is currently loaded in Siril.")
        # get_image_shape() returns (channels, height, width) -- deliberately not reused
        # for width/height below to avoid relying on two different axis conventions in
        # one code path; get_image_keywords()/get_image() are the sources of truth we
        # standardize on everywhere else in this codebase (native = height, width, channels).
        shape = siril.get_image_shape()
        if shape is None:
            raise NoImageLoadedError("Siril reports no image shape.")
        channels, height, width = shape
        keywords = siril.get_image_keywords()
        plate_solved = bool(keywords.pltsolvd) if keywords is not None else False
        return ImageInfo(
            width=width,
            height=height,
            channels=channels,
            plate_solved=plate_solved,
            object_name=getattr(keywords, "object", None) if keywords else None,
            instrument=getattr(keywords, "instrume", None) if keywords else None,
            telescope=getattr(keywords, "telescop", None) if keywords else None,
        )

    def get_preview_pixeldata(self, linked: bool = True) -> np.ndarray:
        """8-bit array autostretched by Siril itself (RESEARCH.md #6) — used for the
        GUI preview so it matches what the user currently sees on screen."""
        siril = self._require_connection()
        return siril.get_image_pixeldata(preview=True, linked=linked)

    def get_full_pixeldata(self) -> np.ndarray:
        """Raw (linear) pixel data at native resolution, uint16 or float32, used for
        full-resolution export compositing (ARCHITECTURE.md #9)."""
        siril = self._require_connection()
        return siril.get_image_pixeldata(preview=False)

    def get_wcs_header_dict(self) -> dict:
        """Returns a dict suitable for astropy.wcs.WCS(...), per RESEARCH.md #4."""
        import sirilpy.utility as sutil

        siril = self._require_connection()
        header_text = siril.get_image_fits_header(return_as="str")
        if not header_text:
            raise NotPlateSolvedError("Image has no FITS header.")
        return sutil.parse_fits_header(header_text)

    def get_system_catalogue_dir(self) -> Path:
        siril = self._require_connection()
        return Path(siril.get_siril_systemdatadir()) / "catalogue"

    def get_image_icc_profile(self) -> bytes | None:
        return self._require_connection().get_image_iccprofile()

    def get_display_icc_profile(self) -> bytes | None:
        return self._require_connection().get_siril_display_iccprofile()

    def get_loaded_image_filename(self) -> str | None:
        """Best-effort: the actual on-disk filename of the currently loaded image, for
        naming exports after it (per user request: "original image name" rather than
        the FITS OBJECT header keyword, which is often blank or generic and doesn't
        reflect the real filename). Unlike this class's other methods, this
        deliberately swallows *any* failure and returns None rather than raising --
        RESEARCH.md has no confirmed sirilpy accessor for this, so callers must be able
        to fall back gracefully if this sirilpy version doesn't expose it under the
        name this guesses, rather than break image loading entirely over a filename."""
        siril = self._require_connection()
        try:
            filename = siril.get_image_filename()
        except Exception:
            return None
        return filename or None

    def get_technical_metadata(self) -> dict[str, str]:
        """Best-effort capture-session details (camera, telescope, filter, exposure,
        etc.) for pre-populating the Info Box overlay's text (gui/main_window.py's
        _default_info_box_text). RESEARCH.md confirms get_image_keywords() exposes
        instrume/telescop/focal_length/pixel_size_x/pixel_size_y/date_obs as real
        FKeywords attributes; filter/exptime/gain are plausible but UNCONFIRMED
        attribute names on that dataclass, so every read here is individually guarded
        via getattr and simply omitted if the attribute doesn't exist under this name
        on this sirilpy version -- same reasoning as get_loaded_image_filename above.
        Returns an empty dict on any failure rather than raising, since this is purely
        for pre-filling editable text the user can always type themselves."""
        siril = self._require_connection()
        try:
            keywords = siril.get_image_keywords()
        except Exception:
            return {}
        if keywords is None:
            return {}

        def _first(*names: str):
            for name in names:
                value = getattr(keywords, name, None)
                if value not in (None, "", 0):
                    return value
            return None

        focal_length = _first("focal_length")
        pixel_size = _first("pixel_size_x")
        exposure = _first("exptime", "exposure")

        fields = {
            "Camera": _first("instrume"),
            "Telescope": _first("telescop"),
            "Focal Length": f"{focal_length} mm" if focal_length else None,
            "Filter": _first("filter"),
            "Exposure": f"{exposure} s" if exposure else None,
            "Gain": _first("gain"),
            "Pixel Size": f"{pixel_size} µm" if pixel_size else None,
            "Date": _first("date_obs"),
        }
        return {k: str(v) for k, v in fields.items() if v is not None}

    def log(self, message: str) -> None:
        """Writes a line to Siril's own log/console panel -- distinct from this
        script's own Python logger, which the user won't see unless Siril was
        launched from a terminal. This is sirilpy's documented way for a script to
        surface status text directly inside Siril's UI."""
        self._require_connection().log(message)

    def show_marker(self, ra: float, dec: float, name: str) -> None:
        """Fire-and-forget: also draw a marker in Siril's own GUI overlay
        (RESEARCH.md #8, Option B). Never used as a data source."""
        siril = self._require_connection()
        try:
            siril.cmd("show", str(ra), str(dec), name)
        except Exception:
            logger.exception("siril.cmd('show', ...) failed for %s", name)
