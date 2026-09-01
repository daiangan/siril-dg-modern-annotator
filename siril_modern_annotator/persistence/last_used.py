"""Last-used *general* settings: the catalog selection, global style, and per-catalog
marker/connector colors the user had active when they last touched them -- restored as
the starting point the next time the script is launched, even on a completely
different image.

This is deliberately separate from both `presets.py` (named, user-created style presets
the user picks explicitly) and `project.py` (per-image `.annotations.json` layout files).
Per user request: switching to a new/different image should not reset catalog choices
and styling back to hardcoded defaults if the user has already set them up once.
"""

from __future__ import annotations

import json
import logging
from dataclasses import asdict

from PyQt6.QtCore import QSettings

from ..annotation.models import DecLabelPosition, InfoBoxCorner, OverlaySettings, RaLabelPosition, StylePreset
from .presets import APP_NAME, ORG_NAME
from .project import ExportSettings, style_preset_from_dict, to_jsonable

logger = logging.getLogger(__name__)

_STYLE_KEY = "last_used/global_style"
_CATALOGS_KEY = "last_used/active_catalogs"
_CATALOG_COLORS_KEY = "last_used/catalog_colors"
_OVERLAY_KEY = "last_used/overlay_settings"
_EXPORT_KEY = "last_used/export_settings"


def _settings() -> QSettings:
    return QSettings(ORG_NAME, APP_NAME)


def save_last_used_style(style: StylePreset) -> None:
    _settings().setValue(_STYLE_KEY, json.dumps(to_jsonable(asdict(style))))


def load_last_used_style() -> StylePreset | None:
    raw = _settings().value(_STYLE_KEY, None)
    if not raw:
        return None
    try:
        return style_preset_from_dict(json.loads(raw))
    except Exception:
        logger.exception("Failed to parse saved last-used style; ignoring it")
        return None


def save_last_used_catalogs(catalogs: set[str]) -> None:
    _settings().setValue(_CATALOGS_KEY, json.dumps(sorted(catalogs)))


def load_last_used_catalogs() -> set[str] | None:
    raw = _settings().value(_CATALOGS_KEY, None)
    if not raw:
        return None
    try:
        return set(json.loads(raw))
    except Exception:
        logger.exception("Failed to parse saved last-used catalogs; ignoring it")
        return None


def save_last_used_catalog_colors(colors: dict[str, str]) -> None:
    _settings().setValue(_CATALOG_COLORS_KEY, json.dumps(colors))


def load_last_used_catalog_colors() -> dict[str, str] | None:
    raw = _settings().value(_CATALOG_COLORS_KEY, None)
    if not raw:
        return None
    try:
        data = json.loads(raw)
        return {str(k): str(v) for k, v in data.items()}
    except Exception:
        logger.exception("Failed to parse saved last-used catalog colors; ignoring it")
        return None


def save_last_used_export_settings(settings: ExportSettings) -> None:
    _settings().setValue(_EXPORT_KEY, json.dumps(asdict(settings)))


def load_last_used_export_settings() -> ExportSettings | None:
    raw = _settings().value(_EXPORT_KEY, None)
    if not raw:
        return None
    try:
        return ExportSettings(**json.loads(raw))
    except Exception:
        logger.exception("Failed to parse saved last-used export settings; ignoring them")
        return None


# Only the fields below are saved for overlays -- per user request, "settings that do
# not depend on image size" (on/off, color, opacity, label positions, corner). Left out
# on purpose: line_width/label_font_size/padding/border_radius/margin (resolution-scaled
# fresh for each image by presets.default_overlay_settings_for_image -- restoring a flat
# saved value would defeat that scaling and look wrong on a different-sized image next
# time), info_box.text (per-image FITS-header content, not a style choice), and
# anchor_x/anchor_y (a raw native-pixel-space drag position, meaningless on a
# different-sized image).


def save_last_used_overlay_settings(settings: OverlaySettings) -> None:
    data = {
        "grid": {
            "enabled": settings.grid.enabled,
            "color": settings.grid.color,
            "opacity": settings.grid.opacity,
            "show_labels": settings.grid.show_labels,
            "ra_label_position": settings.grid.ra_label_position.value,
            "dec_label_position": settings.grid.dec_label_position.value,
        },
        "compass": {
            "enabled": settings.compass.enabled,
            "color": settings.compass.color,
            "arrow_length_fraction": settings.compass.arrow_length_fraction,
        },
        "info_box": {
            "enabled": settings.info_box.enabled,
            "corner": settings.info_box.corner.value,
            "background_color": settings.info_box.background_color,
            "background_opacity": settings.info_box.background_opacity,
            "text_color": settings.info_box.text_color,
        },
    }
    _settings().setValue(_OVERLAY_KEY, json.dumps(data))


def apply_last_used_overlay_settings(defaults: OverlaySettings) -> OverlaySettings:
    """Overwrites every size-independent field of `defaults` (already resolution-scaled
    for the image being loaded, via presets.default_overlay_settings_for_image) in
    place with whatever was last saved, and returns it. A missing/corrupt/older-format
    saved value for any individual field is simply skipped, leaving that one field at
    its resolution-scaled default rather than losing the whole overlay setup."""
    raw = _settings().value(_OVERLAY_KEY, None)
    if not raw:
        return defaults
    try:
        data = json.loads(raw)
    except Exception:
        logger.exception("Failed to parse saved last-used overlay settings; ignoring them")
        return defaults
    if not isinstance(data, dict):
        return defaults

    grid = data.get("grid") or {}
    try:
        defaults.grid.enabled = bool(grid.get("enabled", defaults.grid.enabled))
        defaults.grid.color = str(grid.get("color", defaults.grid.color))
        defaults.grid.opacity = float(grid.get("opacity", defaults.grid.opacity))
        defaults.grid.show_labels = bool(grid.get("show_labels", defaults.grid.show_labels))
        if "ra_label_position" in grid:
            defaults.grid.ra_label_position = RaLabelPosition(grid["ra_label_position"])
        if "dec_label_position" in grid:
            defaults.grid.dec_label_position = DecLabelPosition(grid["dec_label_position"])
    except Exception:
        logger.exception("Failed to apply saved grid overlay settings; using resolution-scaled defaults")

    compass = data.get("compass") or {}
    try:
        defaults.compass.enabled = bool(compass.get("enabled", defaults.compass.enabled))
        defaults.compass.color = str(compass.get("color", defaults.compass.color))
        defaults.compass.arrow_length_fraction = float(
            compass.get("arrow_length_fraction", defaults.compass.arrow_length_fraction)
        )
    except Exception:
        logger.exception("Failed to apply saved compass overlay settings; using resolution-scaled defaults")

    info_box = data.get("info_box") or {}
    try:
        defaults.info_box.enabled = bool(info_box.get("enabled", defaults.info_box.enabled))
        defaults.info_box.background_color = str(
            info_box.get("background_color", defaults.info_box.background_color)
        )
        defaults.info_box.background_opacity = float(
            info_box.get("background_opacity", defaults.info_box.background_opacity)
        )
        defaults.info_box.text_color = str(info_box.get("text_color", defaults.info_box.text_color))
        if "corner" in info_box:
            defaults.info_box.corner = InfoBoxCorner(info_box["corner"])
    except Exception:
        logger.exception("Failed to apply saved info box overlay settings; using resolution-scaled defaults")

    return defaults
