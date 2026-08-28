"""Core annotation data model.

Deliberately free of any Qt import (enforced by tests/test_no_qt_in_model.py) so the
model can be unit tested, serialized, and reasoned about independently of the GUI.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field, replace
from enum import Enum


class MarkerShape(str, Enum):
    CIRCLE = "circle"
    BRACKETS = "brackets"
    CROSSHAIR = "crosshair"
    RETICLE = "reticle"
    DOT = "dot"
    NONE = "none"
    ELLIPSE = "ellipse"


class BackgroundMode(str, Enum):
    NONE = "none"
    TRANSLUCENT = "translucent"
    SOLID = "solid"


class ConnectorStyle(str, Enum):
    STRAIGHT = "straight"
    ELBOW = "elbow"
    CURVED = "curved"


class NameDisplayMode(str, Enum):
    CATALOG_ONLY = "catalog_only"
    COMMON_ONLY = "common_only"
    COMMON_THEN_CATALOG = "common_then_catalog"
    CATALOG_THEN_COMMON = "catalog_then_common"


@dataclass
class MarkerStyle:
    shape: MarkerShape = MarkerShape.CIRCLE
    color: str = "#e8e8e8"
    stroke_width: float = 1.4
    radius: float = 14.0
    opacity: float = 0.9
    size_from_angular_size: bool = False
    # Only meaningful when shape is ELLIPSE (per-object only -- brief: fit an elongated
    # galaxy inside a customized oval). Deliberately manual-only, unlike radius above:
    # size_from_angular_size never applies to these, since the catalogs this app reads
    # from carry no position angle to auto-orient an ellipse with anyway.
    radius_x: float = 20.0
    radius_y: float = 12.0
    rotation_deg: float = 0.0


@dataclass
class LabelStyle:
    # "Verdana" ships with both macOS and Windows by default -- "Inter" doesn't, and
    # silently falls back on both platforms (a Qt console warning on macOS, no warning
    # at all on Windows, where a real report confirmed it lands on Tahoma instead).
    font_family: str = "Verdana"
    font_size: float = 12.0
    bold: bool = False
    italic: bool = False
    text_color: str = "#f2f2f2"
    background_mode: BackgroundMode = BackgroundMode.TRANSLUCENT
    # None = "match this object's catalog color" (the new default, per user request) --
    # an explicit hex string is a deliberate override and always wins. Resolved to a
    # concrete color in annotation.renderer.compute_label_geometry, the single place
    # both the interactive canvas and the exporter read it from.
    background_color: str | None = None
    background_opacity: float = 0.55
    padding: float = 4.0
    corner_radius: float = 3.0
    outline: bool = False
    outline_color: str = "#000000"
    shadow: bool = True
    glow: bool = False
    name_display: NameDisplayMode = NameDisplayMode.CATALOG_ONLY


@dataclass
class StylePreset:
    """A named, reusable pairing of marker + label style, used both as the global
    default style and as the basis for per-object overrides."""

    name: str
    marker_style: MarkerStyle = field(default_factory=MarkerStyle)
    label_style: LabelStyle = field(default_factory=LabelStyle)
    connector_style: ConnectorStyle = ConnectorStyle.ELBOW
    connector_color: str = "#8a8a8a"
    connector_width: float = 1.0


@dataclass
class Annotation:
    """A single astronomical annotation.

    Coordinate contract (see ARCHITECTURE.md #4):
      - ra/dec are the permanent source of truth (sky space).
      - image_x/image_y are the object's position in *native* image pixel space,
        derived once from ra/dec via the image's WCS and never overwritten -- always
        the value "Reset Position" restores.
      - marker_x/marker_y are also native image pixel space, and represent a manual
        override of where the *marker* is drawn; None means "use image_x/image_y" (see
        effective_marker_position). Distinct from label_x/label_y below the same way
        marker_style/label_style are distinct -- moving the marker off the object's
        true position and moving its label off to the side are independent edits.
      - label_x/label_y are also native image pixel space, but represent where the
        *label* is drawn; None means "not yet placed / auto-placement owns it".
      - Preview-space coordinates are never stored here; gui/image_view.py derives
        them on the fly for display only.
    """

    catalog: str
    catalog_name: str
    ra: float
    dec: float
    image_x: float
    image_y: float
    object_type: str = "unknown"
    common_name: str | None = None
    angular_size: float | None = None  # arcmin
    magnitude: float | None = None
    id: str = field(default_factory=lambda: uuid.uuid4().hex)
    enabled: bool = True
    marker_x: float | None = None
    marker_y: float | None = None
    label_x: float | None = None
    label_y: float | None = None
    manually_positioned: bool = False
    locked: bool = False
    priority: int = 50
    marker_style: MarkerStyle | None = None
    label_style: LabelStyle | None = None
    connector_enabled: bool = True
    # Per-object connector overrides -- unlike marker_style/label_style (a full struct
    # swap), these are independent scalar fields since ConnectorStyle/color/width have
    # no bundling struct of their own on StylePreset either. None means "use the global
    # default (or catalog color, for connector_color -- see
    # renderer.resolve_connector_color)".
    connector_style: ConnectorStyle | None = None
    connector_color: str | None = None
    connector_width: float | None = None
    custom_display_name: str | None = None

    def display_name(self, mode: NameDisplayMode) -> str:
        if self.custom_display_name:
            return self.custom_display_name
        catalog = self.catalog_name
        common = self.common_name
        if mode is NameDisplayMode.COMMON_ONLY and common:
            return common
        if mode is NameDisplayMode.COMMON_THEN_CATALOG and common:
            return f"{common} ({catalog})"
        if mode is NameDisplayMode.CATALOG_THEN_COMMON and common:
            return f"{catalog} ({common})"
        return catalog

    def effective_marker_style(self, global_style: StylePreset) -> MarkerStyle:
        return self.marker_style or global_style.marker_style

    def effective_marker_position(self) -> tuple[float, float]:
        x = self.marker_x if self.marker_x is not None else self.image_x
        y = self.marker_y if self.marker_y is not None else self.image_y
        return x, y

    def effective_label_style(self, global_style: StylePreset) -> LabelStyle:
        return self.label_style or global_style.label_style

    def effective_connector_style(self, global_style: StylePreset) -> ConnectorStyle:
        return self.connector_style if self.connector_style is not None else global_style.connector_style

    def effective_connector_color(self, global_style: StylePreset) -> str:
        """Per-object override only -- does NOT factor in catalog color, which needs
        the catalog_colors map this model layer doesn't have; see
        renderer.resolve_connector_color for the full precedence chain used at render
        time (per-object override > catalog color > global default)."""
        return self.connector_color if self.connector_color is not None else global_style.connector_color

    def effective_connector_width(self, global_style: StylePreset) -> float:
        return self.connector_width if self.connector_width is not None else global_style.connector_width

    def clone(self, **overrides) -> "Annotation":
        return replace(self, **overrides)


# Default priority ordering, per brief #11 step 5. Lower number = placed/kept first.
CATALOG_PRIORITY: dict[str, int] = {
    "messier": 10,
    "sh2": 15,
    "ic": 20,
    "ngc": 20,
    "ldn": 25,
    "barnard": 25,
    "bright_star": 30,
    "simbad": 40,
    "user": 5,
}


def default_priority_for_catalog(catalog: str) -> int:
    return CATALOG_PRIORITY.get(catalog.lower(), 50)
