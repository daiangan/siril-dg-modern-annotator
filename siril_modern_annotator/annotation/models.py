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


class RaLabelPosition(str, Enum):
    TOP = "top"
    BOTTOM = "bottom"


class DecLabelPosition(str, Enum):
    RIGHT = "right"
    LEFT = "left"


@dataclass
class GridStyle:
    """RA/Dec coordinate grid overlay -- an image-level setting, not per-object, so
    unlike MarkerStyle/LabelStyle this has no per-object override concept and lives
    directly on OverlaySettings rather than StylePreset."""

    enabled: bool = False  # off by default -- per user request, shown only if asked for
    color: str = "#66AADD"
    opacity: float = 0.6
    line_width: float = 1.0
    show_labels: bool = True
    # A flat default here is overridden with a resolution-scaled value at image load
    # time (persistence/presets.py's default_overlay_settings_for_image) -- per user
    # report, a small flat size was unreadable on a large image. This field only
    # matters as the fallback for a brand new StylePreset built without going through
    # that path (e.g. directly in a test).
    label_font_size: float = 11.0
    ra_label_position: RaLabelPosition = RaLabelPosition.TOP
    dec_label_position: DecLabelPosition = DecLabelPosition.RIGHT


@dataclass
class CompassStyle:
    """N/E direction indicator -- also image-level, not per-object."""

    enabled: bool = False  # off by default, same reasoning as GridStyle
    color: str = "#88CCFF"
    line_width: float = 1.6
    arrow_length_fraction: float = 0.06  # of min(image_width, image_height)
    label_font_size: float = 13.0
    # None = default bottom-right corner anchor; an explicit override means the user
    # dragged it. Native image pixel space, same convention as Annotation.marker_x/y.
    anchor_x: float | None = None
    anchor_y: float | None = None


class InfoBoxCorner(str, Enum):
    TOP_LEFT = "top_left"
    TOP_RIGHT = "top_right"
    BOTTOM_LEFT = "bottom_left"
    BOTTOM_RIGHT = "bottom_right"


@dataclass
class InfoBoxStyle:
    """Technical-details text overlay (camera/telescope/filter/etc.) -- image-level,
    not per-object. text is pre-populated from FITS header metadata at image load
    time (see MainWindow._default_info_box_text) and then freely editable, the same
    "real text, not a placeholder" convention as an object's custom display name."""

    enabled: bool = False  # off by default, same reasoning as GridStyle/CompassStyle
    text: str = ""
    corner: InfoBoxCorner = InfoBoxCorner.BOTTOM_LEFT
    background_color: str = "#000000"
    background_opacity: float = 0.6
    border_radius: float = 6.0
    padding: float = 10.0
    text_color: str = "#f2f2f2"
    font_size: float = 14.0
    margin: float = 24.0  # gap from the image edge, native px
    # None = default corner position (per `corner` above); an explicit override means
    # the user dragged it -- same convention as CompassStyle.anchor_x/y.
    anchor_x: float | None = None
    anchor_y: float | None = None


@dataclass
class ConstellationStyle:
    """Constellation stick-figure lines + name labels -- image-level, not per-object,
    same category as GridStyle/CompassStyle. Unlike those two, its geometry doesn't
    come from WCS math alone: it's Siril's own bundled constellations.csv/
    constellationsnames.csv (see annotation/constellations.py), filtered to the
    current frame by annotation/renderer.py's compute_constellation_geometry."""

    enabled: bool = False  # off by default, same reasoning as GridStyle/CompassStyle
    color: str = "#A9B4C2"
    opacity: float = 0.7
    line_width: float = 1.0
    show_labels: bool = True
    label_font_size: float = 11.0


@dataclass
class OverlaySettings:
    """Bundles every image-level (as opposed to per-object) overlay -- the RA/Dec
    grid, compass, technical-details info box, and constellation lines; a scale bar is
    a planned future addition here."""

    grid: GridStyle = field(default_factory=GridStyle)
    compass: CompassStyle = field(default_factory=CompassStyle)
    info_box: InfoBoxStyle = field(default_factory=InfoBoxStyle)
    constellations: ConstellationStyle = field(default_factory=ConstellationStyle)


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
    # A catalog-provided identifier known to resolve reliably on SIMBAD (e.g. "HD
    # 186675"), independent of catalog_name -- some catalogs' display names (Bayer/
    # Flamsteed strings like "b01 Cyg", Siril's "LdN-1712") are ambiguous or malformed
    # as SIMBAD queries even though the source data has a better identifier available.
    # None means "fall back to catalog_name-based lookup" (see object_panel.simbad_url_for).
    simbad_id: str | None = None
    # Real isophote shape data for a galaxy (see annotation/catalogs.py's galaxy-shape-
    # enrichment section), all three present together or not at all. Deliberately plain
    # catalog *data*, not a marker_style override -- renderer.compute_marker_geometry
    # auto-renders an oriented ellipse from these when present and marker_style is still
    # None, the same way size_from_angular_size auto-scales a circle from angular_size,
    # so per-catalog marker color/stroke_width/opacity keep resolving normally instead
    # of being pinned to whatever a real marker_style object would carry. A user's own
    # manual style edit sets marker_style and takes over from there, same as any other
    # per-object override. major/minor are arcmin (same convention as angular_size,
    # converted to native px at render time via the image's own arcsec_per_px);
    # position_angle_screen_deg is pre-converted to on-screen rotation at enrichment
    # time (needs the image's actual WCS orientation, which compute_marker_geometry
    # itself deliberately never touches -- see ARCHITECTURE.md #4).
    galaxy_major_axis_arcmin: float | None = None
    galaxy_minor_axis_arcmin: float | None = None
    galaxy_position_angle_screen_deg: float | None = None

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
    # One worse than sh2 (not tied) for a deterministic winner on the rare object
    # cataloged in both -- RCW is the southern-hemisphere counterpart to Sh2 (which is
    # mostly northern-sky), so the two rarely compete for the same object at all, but
    # still need a fixed order for CompositeProvider's dedup.
    "rcw": 16,
    # One worse than rcw, same reasoning: the Messier/NGC/Sh2/RCW name for a shared
    # object (e.g. the Veil Nebula as NGC 6960, the Crab as M1) is the one
    # astrophotographers actually use for it, even when it's also a cataloged SNR.
    "snr": 17,
    "ic": 20,
    "ngc": 20,
    # One worse than ic/ngc (not tied) -- every Arp entry already carries a real NGC/
    # UGC/MCG cross-reference (see _vii192_row_to_annotation), so when the same galaxy
    # also comes back from VII/118/NGC/IC, that more commonly cited designation wins;
    # Arp only wins the display name when nothing else covers the same galaxy.
    "arp": 21,
    # One worse than arp (not tied) -- most of Abell's 86 planetary nebulae were
    # previously uncatalogued (that's why Abell found them), so this rarely competes
    # with anything, but still needs a fixed order for the rare object that does.
    "abell": 22,
    "ldn": 25,
    # One better than ldn (not tied) so Auto Arrange/dedup have a deterministic winner
    # when the same dark nebula is cataloged in both -- B-numbers (Barnard) are the
    # more commonly cited name for these in astrophotography circles (e.g. the
    # Horsehead's dark patch as "B33"), so Barnard wins the on-image label.
    "barnard": 24,
    # Least commonly cited name among the deep-sky catalogs above for a shared object
    # (e.g. an LBN nebula that's also cataloged as Sh2/IC/NGC) -- those catalogs' names
    # are the ones astrophotographers actually use, so LBN only wins the display name
    # when nothing else covers the same object.
    "lbn": 26,
    # Never competes with any other catalog for the same on-image label (deliberately
    # excluded from _DEEP_SKY_CATALOGS' cross-catalog dedup -- see
    # _vii21_row_to_annotation's docstring), so this tier only matters for vdB's own
    # rare same-designation ties.
    "vdb": 27,
    # Confirmed live: most Gum objects already carry an RCW cross-reference for the
    # same physical nebula, and RCW is the more commonly cited name for southern HII
    # regions in modern astrophotography -- Gum only wins the display name when
    # nothing else (RCW/NGC/Sh2/etc.) covers the same object.
    "gum": 28,
    # Never competes with any other catalog for the same on-image label (deliberately
    # excluded from _DEEP_SKY_CATALOGS' cross-catalog dedup, same reasoning as vdB --
    # see _vii213_row_to_annotation's docstring), so this tier only matters for
    # Hickson's own rare same-designation ties.
    "hickson": 29,
    "bright_star": 30,
    # A deliberately, individually searched-and-confirmed object (Siril's own
    # Annotate > Search Object), so it outranks the generic catalogs -- but not "user"
    # below, this app's own manually-placed custom objects, which stay the highest.
    "user_dso": 32,
    "simbad": 40,
    "user": 5,
}


def default_priority_for_catalog(catalog: str) -> int:
    return CATALOG_PRIORITY.get(catalog.lower(), 50)
