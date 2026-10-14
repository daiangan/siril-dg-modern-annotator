"""Facade wrapping sirilpy.SirilInterface.

Methods on this class are called on the Qt main thread. Worker threads receive plain data
(NumPy arrays, dataclasses) produced by these calls, keeping background tasks isolated from
the live IPC connection.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

import numpy as np

logger = logging.getLogger(__name__)

MINIMUM_SIRIL_VERSION = "1.4.4"
# Gate on Siril version via siril.cmd("requires", ...). Individual sirilpy method
# availability is handled gracefully at call sites.


class SirilBridgeError(RuntimeError):
    """Raised for any failure connecting to or querying Siril."""


class NoImageLoadedError(SirilBridgeError):
    pass


from ..annotation.wcs import NotPlateSolvedError


@dataclass(frozen=True)
class ImageInfo:
    width: int
    height: int
    channels: int
    plate_solved: bool
    object_name: str | None
    instrument: str | None
    telescope: str | None


def _format_focal_length(value) -> str:
    return f"{round(float(value))} mm"


def _format_pixel_size(value) -> str:
    return f"{float(value):.1f} µm"


def _format_exposure(value) -> str:
    """Seconds -> the smallest unit that reads naturally, per user request: under a
    minute stays in seconds, under an hour becomes minutes, an hour or more becomes
    hours -- seconds/minutes are whole numbers, hours keep one decimal place."""
    seconds = float(value)
    if seconds < 60:
        return f"{round(seconds)} s"
    minutes = seconds / 60
    if minutes < 60:
        return f"{round(minutes)} m"
    return f"{seconds / 3600:.1f} h"


class SirilBridge:
    """Main-thread facade over sirilpy.SirilInterface, exposing image access,
    FITS header and WCS metadata, preview/STF stretch, and catalogue paths."""

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

    def disconnect(self) -> None:
        """Closes the socket/pipe connection to Siril if open."""
        if self._siril is not None:
            try:
                if getattr(self._siril, "connected", False):
                    self._siril.disconnect()
            except Exception:
                logger.debug("Failed to disconnect from Siril cleanly", exc_info=True)
            finally:
                self._siril = None

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
        """8-bit array autostretched by Siril for on-screen preview and matched export."""
        siril = self._require_connection()
        return siril.get_image_pixeldata(preview=True, linked=linked)

    def get_full_pixeldata(self) -> np.ndarray:
        """Raw pixel data at native resolution, uint16 or float32."""
        siril = self._require_connection()
        return siril.get_image_pixeldata(preview=False)

    def get_wcs_header_dict(self) -> dict:
        """Returns a dict suitable for constructing an astropy.wcs.WCS instance."""
        import sirilpy.utility as sutil

        siril = self._require_connection()
        header_text = siril.get_image_fits_header(return_as="str")
        if not header_text:
            raise NotPlateSolvedError("Image has no FITS header.")
        return sutil.parse_fits_header(header_text)

    def get_system_catalogue_dir(self) -> Path:
        siril = self._require_connection()
        return Path(siril.get_siril_systemdatadir()) / "catalogue"

    def get_user_catalogue_dir(self) -> Path:
        """Writable per-user catalogue directory -- distinct from
        get_system_catalogue_dir()'s read-only, app-bundled one (messier.csv etc). This
        is where Siril's own Astrometry > Annotate > Search Object > Apply persists a
        manually searched/confirmed object into user-DSO-catalogue.csv, confirmed by
        inspecting a real installation's Application Support folder: nothing gets
        written into the image's own FITS header for this."""
        siril = self._require_connection()
        return Path(siril.get_siril_userdatadir()) / "catalogue"

    def get_image_icc_profile(self) -> bytes | None:
        return self._require_connection().get_image_iccprofile()

    def get_loaded_image_filename(self) -> str | None:
        """Returns the filename of the loaded image if available."""
        siril = self._require_connection()
        try:
            filename = siril.get_image_filename()
        except Exception:
            return None
        return filename or None

    def get_technical_metadata(self) -> dict[str, str]:
        """Reads capture-session metadata for the Info Box overlay."""
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

        # Prioritize total integration time (livetime / totalexp) over single sub exposure
        livetime = _first("livetime", "totalexp")
        if not livetime:
            try:
                hdr = self.get_wcs_header_dict()
                for key in ("LIVETIME", "TOTALEXP", "TELAPSE"):
                    if key in hdr and hdr[key]:
                        try:
                            livetime = float(hdr[key])
                            break
                        except (ValueError, TypeError):
                            pass
            except Exception:
                pass
        exposure = livetime if livetime else _first("exptime", "exposure")

        fields = {
            "Camera": _first("instrume"),
            "Telescope": _first("telescop"),
            "Focal Length": _format_focal_length(focal_length) if focal_length else None,
            "Filter": _first("filter"),
            "Exposure": _format_exposure(exposure) if exposure else None,
            "Gain": _first("gain"),
            "Pixel Size": _format_pixel_size(pixel_size) if pixel_size else None,
            "Date": _first("date_obs"),
        }
        return {k: str(v) for k, v in fields.items() if v is not None}

    def log(self, message: str) -> None:
        """Writes a line to Siril's log panel."""
        self._require_connection().log(message)
