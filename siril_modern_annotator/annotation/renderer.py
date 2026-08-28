"""Shared, backend-independent geometry for drawing an annotation.

This module computes *where* things go (label bounding box, connector line points) but
never draws anything itself — no Qt, no Pillow. gui/annotation_item.py (QPainter) and
export/exporter.py (Pillow ImageDraw) both call these same functions before drawing, so
interactive preview and full-resolution export are guaranteed to place markers, labels,
and connectors identically (ARCHITECTURE.md #8) — only the pixel-drawing backend and the
scale factor differ.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, replace
from typing import Callable

from .layout import BBox
from .models import Annotation, ConnectorStyle, LabelStyle, MarkerShape, MarkerStyle, StylePreset

TextMeasurer = Callable[[str, LabelStyle], tuple[float, float]]

# Below this marker-to-label-center distance, the label is considered "attached" and no
# connector line is drawn (brief #10's connector is only for labels moved away).
_ATTACHED_THRESHOLD_PX = 4.0

# Default fraction of the smaller image dimension used as a sane cap on angular-size-
# derived marker radii (see compute_marker_geometry's max_radius_px). Shared by the
# interactive canvas (gui/main_window.py) and the exporter so both clamp identically.
DEFAULT_MAX_MARKER_RADIUS_FRACTION = 0.4


def default_max_marker_radius_px(width: float, height: float) -> float:
    return min(width, height) * DEFAULT_MAX_MARKER_RADIUS_FRACTION


def default_text_measurer(text: str, style: LabelStyle) -> tuple[float, float]:
    """Supports multi-line text (custom display names/notes can span several lines,
    brief request: "a bigger tooltip with some description") -- width is the widest
    line, height is per-line-height times the number of lines."""
    lines = text.split("\n") or [""]
    line_height = style.font_size * 1.35
    width = max((len(line) * style.font_size * 0.62 for line in lines), default=0.0)
    height = line_height * len(lines)
    return width + 2 * style.padding, height + 2 * style.padding


@dataclass(frozen=True)
class LabelGeometry:
    text: str
    bbox: BBox
    style: LabelStyle


@dataclass(frozen=True)
class MarkerGeometry:
    x: float
    y: float
    # Circular-equivalent radius: max(radius_x, radius_y) for ELLIPSE, otherwise equal
    # to both. Used by the "attached, skip the connector" distance check and as a
    # bounding-box radius, both of which stay correct for ELLIPSE too since that max is
    # always >= the true bounding box at any rotation (see _ellipse_anchor_point).
    radius: float
    style: MarkerStyle
    radius_x: float
    radius_y: float
    rotation_deg: float


def resolve_marker_color(
    ann: Annotation,
    global_style: StylePreset,
    catalog_colors: dict[str, str] | None = None,
) -> str:
    """Precedence: an explicit per-object marker style override is the most specific
    choice and always wins; next a per-catalog color; otherwise the global default.
    Shared by compute_marker_geometry (what actually renders) and the Style panel's
    "Selected Object" editor (what color to show/commit when a user starts a per-
    object override), so unchecking "Use global style for this object" starts from
    whatever color is already on screen instead of silently jumping to the flat
    global default -- a real report confirmed the marker/connector visibly changed
    color the instant that box was unchecked, before any actual edit."""
    if ann.marker_style is not None:
        return ann.marker_style.color
    if catalog_colors:
        color = catalog_colors.get(ann.catalog)
        if color:
            return color
    return global_style.marker_style.color


def compute_marker_geometry(
    ann: Annotation,
    global_style: StylePreset,
    arcsec_per_px: float | None = None,
    max_radius_px: float | None = None,
    catalog_colors: dict[str, str] | None = None,
) -> MarkerGeometry:
    """max_radius_px caps the angular-size-derived radius so a catalog entry with a
    genuinely huge apparent size (e.g. M31's ~178' full extent) can't produce a marker
    bigger than the frame itself when the field of view is much tighter than the
    object's full catalog size -- callers pass a fraction of the image dimensions."""
    style = ann.effective_marker_style(global_style)
    style = replace(style, color=resolve_marker_color(ann, global_style, catalog_colors))
    radius = style.radius
    # ELLIPSE is manual-only (see MarkerStyle.radius_x's docstring) -- skip the
    # angular-size auto-scaling entirely rather than applying it to a field
    # (radius_x/radius_y) style.size_from_angular_size doesn't even describe.
    if style.shape is not MarkerShape.ELLIPSE and style.size_from_angular_size and ann.angular_size and arcsec_per_px:
        # angular_size is in arcmin (RESEARCH.md local-catalog schema); convert to px.
        angular_radius_arcsec = (ann.angular_size * 60.0) / 2.0
        radius = max(radius, angular_radius_arcsec / arcsec_per_px)
        if max_radius_px is not None:
            radius = min(radius, max_radius_px)
    x, y = ann.effective_marker_position()
    if style.shape is MarkerShape.ELLIPSE:
        radius_x, radius_y = style.radius_x, style.radius_y
        radius = max(radius_x, radius_y)
    else:
        radius_x = radius_y = radius
    return MarkerGeometry(
        x=x, y=y, radius=radius, style=style,
        radius_x=radius_x, radius_y=radius_y, rotation_deg=style.rotation_deg if style.shape is MarkerShape.ELLIPSE else 0.0,
    )


_FALLBACK_LABEL_BACKGROUND_COLOR = "#101015"


def compute_label_geometry(
    ann: Annotation,
    global_style: StylePreset,
    measurer: TextMeasurer | None = None,
    catalog_colors: dict[str, str] | None = None,
) -> LabelGeometry:
    style = ann.effective_label_style(global_style)
    if style.background_color is None:
        # None = "match this object's catalog color" (see LabelStyle's docstring
        # comment). Falls back to the old flat dark default if no catalog color is
        # available (e.g. an unrecognized/future catalog key).
        color = catalog_colors.get(ann.catalog) if catalog_colors else None
        style = replace(style, background_color=color or _FALLBACK_LABEL_BACKGROUND_COLOR)
    measurer = measurer or default_text_measurer
    text = ann.display_name(style.name_display)
    w, h = measurer(text, style)
    if ann.label_x is not None and ann.label_y is not None:
        x, y = ann.label_x, ann.label_y
    else:
        # Not yet auto-arranged: default to just right of the marker.
        x, y = ann.image_x + 14, ann.image_y - h / 2
    return LabelGeometry(text=text, bbox=BBox(x, y, x + w, y + h), style=style)


def resolve_connector_color(
    ann: Annotation,
    global_style: StylePreset,
    catalog_colors: dict[str, str] | None = None,
) -> str:
    """Precedence: an explicit per-object override (Selected Object tab, "Use global
    style for this object" unchecked) is the most specific choice and always wins;
    next a per-catalog color; otherwise the global default."""
    if ann.connector_color is not None:
        return ann.connector_color
    if catalog_colors:
        color = catalog_colors.get(ann.catalog)
        if color:
            return color
    return global_style.connector_color


def compute_connector_points(
    ann: Annotation,
    marker: MarkerGeometry,
    label: LabelGeometry,
    connector_style: ConnectorStyle,
) -> list[tuple[float, float]] | None:
    """Returns a polyline (list of points) from marker to label edge, or None if no
    connector should be drawn. A 3-point result with connector_style CURVED is meant to
    be interpreted as a quadratic Bezier (start, control, end) by the drawing backend."""
    if not ann.connector_enabled:
        return None

    bbox = label.bbox
    cx = (bbox.x0 + bbox.x1) / 2.0
    cy = (bbox.y0 + bbox.y1) / 2.0
    dx, dy = cx - marker.x, cy - marker.y
    distance = (dx**2 + dy**2) ** 0.5
    if distance <= _ATTACHED_THRESHOLD_PX + marker.radius:
        return None

    # Aim at the box's true center, stopping right at the boundary -- a real
    # screenshot showed the line landing on a corner instead (the old nearest-point
    # clamp treats x and y independently, so a diagonally-offset marker snaps to
    # whichever corner is closest rather than pointing at the label's middle, which
    # reads as visually wrong). This uses the point where the marker->center segment
    # first crosses the box edge, so the line always visually aims at the center.
    edge_point = _line_box_entry_point(marker.x, marker.y, cx, cy, bbox)
    # Start at the marker's own boundary facing the label, not its center -- a
    # real screenshot showed the line running from dead-center out through the
    # circle, which reads as visually wrong (Siril's own annotator, like most
    # annotation tools, starts the connector at the marker's edge).
    ux, uy = (dx / distance, dy / distance) if distance > 0 else (0.0, -1.0)
    if marker.style.shape is MarkerShape.BRACKETS:
        off_x, off_y = _bracket_anchor_point(marker.radius, ux, uy)
        start = (marker.x + off_x, marker.y + off_y)
    elif marker.style.shape is MarkerShape.ELLIPSE:
        off_x, off_y = _ellipse_anchor_point(marker.radius_x, marker.radius_y, marker.rotation_deg, ux, uy)
        start = (marker.x + off_x, marker.y + off_y)
    else:
        start = (marker.x + marker.radius * ux, marker.y + marker.radius * uy)

    if connector_style is ConnectorStyle.STRAIGHT:
        return [start, edge_point]
    if connector_style is ConnectorStyle.CURVED:
        mid = ((start[0] + edge_point[0]) / 2.0, min(start[1], edge_point[1]) - 12.0)
        return [start, mid, edge_point]
    # ELBOW: bend once, horizontally then vertically toward whichever axis has the
    # larger separation, producing a clean right-angle dogleg.
    if abs(dx) > abs(dy):
        bend = (edge_point[0], start[1])
    else:
        bend = (start[0], edge_point[1])
    return [start, bend, edge_point]


def _bracket_anchor_point(radius: float, ux: float, uy: float) -> tuple[float, float]:
    """Where a BRACKETS marker's connector should actually start, relative to the
    marker's own center. BRACKETS draws four short L-shaped corner marks (see
    gui/annotation_item.py's MarkerItem.paint and export/exporter.py's _draw_marker),
    not a continuous circle or square outline -- the middle half of each side of the
    bounding square has no line at all. The plain circular radius offset every other
    marker shape uses can land in exactly that gap, which is a real, confirmed report:
    the connector line appeared to float, pointing at empty space instead of touching
    the marker. This finds where the marker->label ray crosses the bracket's bounding
    square (same ray-vs-square math as a circle's radius offset, just square instead
    of round), then clamps that point onto whichever of that edge's two drawn corner
    segments is nearest -- so the result always lands on an actual visible line, while
    still pointing in roughly the label's true direction rather than jumping to a
    fixed corner regardless of angle."""
    if radius <= 0 or (ux == 0 and uy == 0):
        return (0.0, 0.0)
    arm = radius * 0.5
    tx = radius / abs(ux) if ux != 0 else float("inf")
    ty = radius / abs(uy) if uy != 0 else float("inf")
    t = min(tx, ty)
    x, y = t * ux, t * uy

    def _clamp_to_drawn(value: float) -> float:
        gap_lo, gap_hi = -radius + arm, radius - arm
        if gap_lo <= value <= gap_hi:
            return gap_lo if (value - gap_lo) < (gap_hi - value) else gap_hi
        return value

    if t == tx:  # ray crossed a vertical (left/right) edge first -- y is the free axis
        y = _clamp_to_drawn(y)
    else:  # crossed a horizontal (top/bottom) edge -- x is the free axis
        x = _clamp_to_drawn(x)
    return (x, y)


def _ellipse_anchor_point(
    radius_x: float, radius_y: float, rotation_deg: float, ux: float, uy: float
) -> tuple[float, float]:
    """Where an ELLIPSE marker's connector should actually start, relative to the
    marker's own center -- the point where the marker->label ray exits the (possibly
    rotated) ellipse boundary, so the connector always touches the drawn oval exactly
    rather than assuming a circular radius (a real, confirmed bug for a differently
    non-circular shape: see _bracket_anchor_point above).

    Rotates the ray direction into the ellipse's own unrotated frame (inverse of the
    rotation gui/annotation_item.py's paint() and export/exporter.py's _draw_marker
    apply when actually drawing it -- both must agree with this for the connector to
    land on the visible edge), solves for the standard axis-aligned ellipse boundary
    there, then rotates the resulting point back."""
    if radius_x <= 0 or radius_y <= 0 or (ux == 0 and uy == 0):
        return (0.0, 0.0)
    theta = math.radians(rotation_deg)
    cos_t, sin_t = math.cos(theta), math.sin(theta)
    local_ux = ux * cos_t + uy * sin_t
    local_uy = -ux * sin_t + uy * cos_t
    denom = (local_ux / radius_x) ** 2 + (local_uy / radius_y) ** 2
    if denom <= 0:
        return (0.0, 0.0)
    t = 1.0 / math.sqrt(denom)
    local_x, local_y = t * local_ux, t * local_uy
    x = local_x * cos_t - local_y * sin_t
    y = local_x * sin_t + local_y * cos_t
    return (x, y)


def _line_box_entry_point(px: float, py: float, qx: float, qy: float, bbox: BBox) -> tuple[float, float]:
    """Where the segment from (px, py) to (qx, qy) first crosses bbox's boundary.
    (qx, qy) is always the box's own center here, so this is exactly the point on the
    edge that a line from the marker straight at the label's middle would touch --
    unlike a nearest-point clamp (which treats x/y independently and can land on a
    corner for a diagonally-offset marker), this always aims visually at the center."""
    dx, dy = qx - px, qy - py
    t0, t1 = 0.0, 1.0
    # Liang-Barsky line clipping: (p, q) pairs test each of the 4 box boundaries.
    for p, q in (
        (-dx, px - bbox.x0),
        (dx, bbox.x1 - px),
        (-dy, py - bbox.y0),
        (dy, bbox.y1 - py),
    ):
        if p == 0:
            if q < 0:
                return qx, qy  # parallel to this edge and outside it; fall back to center
            continue
        r = q / p
        if p < 0:
            t0 = max(t0, r)
        else:
            t1 = min(t1, r)
    if t0 > t1:
        return qx, qy
    return px + t0 * dx, py + t0 * dy
