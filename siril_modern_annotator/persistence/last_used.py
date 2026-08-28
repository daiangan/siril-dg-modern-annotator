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

from ..annotation.models import StylePreset
from .presets import APP_NAME, ORG_NAME
from .project import style_preset_from_dict, to_jsonable

logger = logging.getLogger(__name__)

_STYLE_KEY = "last_used/global_style"
_CATALOGS_KEY = "last_used/active_catalogs"
_CATALOG_COLORS_KEY = "last_used/catalog_colors"


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
