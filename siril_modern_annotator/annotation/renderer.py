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

import numpy as np

from .layout import BBox
from .models import (
    Annotation,
    CompassStyle,
    ConnectorStyle,
    DecLabelPosition,
    GridStyle,
    LabelStyle,
    MarkerShape,
    MarkerStyle,
    RaLabelPosition,
    StylePreset,
)
from .wcs import SirilWcs

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


# ---------------------------------------------------------------- RA/Dec grid ----

Point = tuple[float, float]

# "Nice" grid spacings in degrees -- mirrors the approach in siril-scripts'
# Svenesis-AnnotateImage.py (confirmed via direct source read), which this project
# treats as its closest prior art for this exact feature.
# sorted()/set() rather than a hand-ordered literal list: the arcsec/arcmin/degree
# groups don't interleave in ascending order when just concatenated (e.g. 30/60 == 0.5
# lands *before* 0.05/0.1/0.25 in source order) -- a real bug caught by testing, where
# _choose_grid_step_deg's "first entry >= ideal" scan could pick a much coarser step
# than intended because a larger-but-earlier-in-the-list value shadowed the correct one.
_GRID_STEP_CHOICES_DEG = sorted({
    1 / 3600, 2 / 3600, 5 / 3600, 10 / 3600, 30 / 3600,  # arcsec range: tight FOVs
    1 / 60, 2 / 60, 5 / 60, 10 / 60, 30 / 60,  # arcmin range
    0.05, 0.1, 0.25, 0.5, 1.0, 2.0, 5.0, 10.0, 15.0, 30.0, 45.0,  # degree range
})
_GRID_TARGET_LINES = 5
_GRID_SAMPLES_PER_LINE = 200


def _choose_grid_step_deg(fov_deg: float, target_lines: int = _GRID_TARGET_LINES) -> float:
    """Smallest predefined "nice" step that gives no more than target_lines lines
    across the field of view."""
    if fov_deg <= 0:
        return _GRID_STEP_CHOICES_DEG[0]
    ideal = fov_deg / target_lines
    for step in _GRID_STEP_CHOICES_DEG:
        if step >= ideal:
            return step
    return _GRID_STEP_CHOICES_DEG[-1]


def _format_ra_sexagesimal(ra_deg: float) -> str:
    total_hours = (ra_deg % 360.0) / 15.0
    h = int(total_hours)
    minutes = (total_hours - h) * 60.0
    m = int(minutes)
    s = (minutes - m) * 60.0
    # Guard the classic "59.96s rounds to 60" display bug from naive %.1f formatting.
    if round(s, 1) >= 60.0:
        s = 0.0
        m += 1
        if m >= 60:
            m = 0
            h = (h + 1) % 24
    return f"{h:02d}h{m:02d}m{s:04.1f}s"


def _format_dec_sexagesimal(dec_deg: float) -> str:
    sign = "-" if dec_deg < 0 else "+"
    dec_deg = abs(dec_deg)
    d = int(dec_deg)
    minutes = (dec_deg - d) * 60.0
    m = int(minutes)
    s = (minutes - m) * 60.0
    if round(s, 1) >= 60.0:
        s = 0.0
        m += 1
        if m >= 60:
            m = 0
            d += 1
    return f"{sign}{d:02d}°{m:02d}′{s:04.1f}″"


@dataclass(frozen=True)
class GridLabel:
    x: float
    y: float
    text: str
    # Which edge this label sits near -- "ra" (a meridian, near the top or bottom edge
    # per style.ra_label_position) or "dec" (a parallel, near the right or left edge
    # per style.dec_label_position). The drawing backends (gui/overlay_item.py,
    # export/exporter.py) use this to anchor the text so it grows *inward* from (x, y)
    # rather than being centered on it -- centering would let text overhang the frame
    # edge for a label point deliberately placed close to that edge (see
    # _pick_grid_label_point's margin).
    axis: str


@dataclass(frozen=True)
class GridGeometry:
    lines: list[list[Point]]  # each entry is a polyline, already clipped to the frame
    labels: list[GridLabel]
    style: GridStyle


def _clip_polyline_to_frame(
    xs: np.ndarray, ys: np.ndarray, width: float, height: float
) -> list[list[Point]]:
    """Splits a (possibly partly off-frame, possibly discontinuous near a pole or the
    0/360 RA wrap) sampled line into contiguous segments that are actually visible."""
    jump_threshold = max(width, height)
    segments: list[list[Point]] = []
    current: list[Point] = []
    prev: Point | None = None
    for x, y in zip(xs, ys):
        in_bounds = 0.0 <= x <= width and 0.0 <= y <= height
        discontinuous = prev is not None and (
            abs(x - prev[0]) > jump_threshold or abs(y - prev[1]) > jump_threshold
        )
        if not in_bounds or discontinuous:
            if len(current) >= 2:
                segments.append(current)
            current = []
            prev = None
            if in_bounds:
                current = [(float(x), float(y))]
                prev = current[0]
            continue
        current.append((float(x), float(y)))
        prev = current[-1]
    if len(current) >= 2:
        segments.append(current)
    return segments


_GRID_LABEL_MARGIN_PX = 6.0


def _pick_grid_label_point(
    segment: list[Point], axis: str, style: GridStyle, width: float, height: float
) -> Point:
    """Where a grid line's label anchors, on a real point of the (possibly curved,
    possibly not-quite-vertical/horizontal) line itself, biased toward the requested
    edge and inset by a small margin so the text (anchored to grow inward from this
    point -- see GridLabel) never overhangs the frame. Previously this was always just
    segment[0] -- wherever the sampling loop happened to enter the frame -- which for
    many lines landed hard against the very edge, with the export's Pillow anchor
    defaulting to centered-on-the-point and no margin at all: a real, confirmed report
    of Dec labels rendering partly or fully outside the exported image."""
    if axis == "ra":
        # Roughly vertical: bias toward the top or bottom edge.
        point = min(segment, key=lambda p: p[1]) if style.ra_label_position is RaLabelPosition.TOP else max(
            segment, key=lambda p: p[1]
        )
    else:
        # Roughly horizontal: bias toward the right or left edge.
        point = max(segment, key=lambda p: p[0]) if style.dec_label_position is DecLabelPosition.RIGHT else min(
            segment, key=lambda p: p[0]
        )
    x = min(max(point[0], _GRID_LABEL_MARGIN_PX), width - _GRID_LABEL_MARGIN_PX)
    y = min(max(point[1], _GRID_LABEL_MARGIN_PX), height - _GRID_LABEL_MARGIN_PX)
    return x, y


def compute_grid_geometry(wcs: SirilWcs, style: GridStyle) -> GridGeometry:
    """RA/Dec coordinate grid, clipped to the image frame. Native image pixel space,
    same as every other geometry function here. wcs is the only thing this needs from
    the caller -- all the actual sky<->pixel math is SirilWcs's (ARCHITECTURE.md #4;
    this just calls its existing public methods, the same way CatalogFetchWorker does)."""
    if not style.enabled:
        return GridGeometry(lines=[], labels=[], style=style)

    width, height = float(wcs.native_width), float(wcs.native_height)
    fov = wcs.field_of_view()
    ra_step = _choose_grid_step_deg(fov.width_deg)
    dec_step = _choose_grid_step_deg(fov.height_deg)

    # fov.width_deg is already the true angular width (cos(dec)-corrected, see
    # field_of_view's own math) -- converting it back to a *raw RA-degree* sampling
    # range (what ra_step actually steps through) needs dividing back out by
    # cos(dec), or this range comes out too narrow near any non-zero declination and
    # silently drops real grid lines near the edges of the frame.
    cos_center_dec = max(math.cos(math.radians(fov.center_dec)), 1e-6)
    # Pad the sampled range beyond the nominal FOV so a grid line whose *label* point
    # falls just outside a corner still has the rest of its length correctly clipped
    # in, rather than the sampled range itself stopping short of the visible frame.
    dec_lo = fov.center_dec - fov.height_deg / 2.0 - dec_step
    dec_hi = fov.center_dec + fov.height_deg / 2.0 + dec_step
    ra_lo = fov.center_ra - (fov.width_deg / 2.0) / cos_center_dec - ra_step
    ra_hi = fov.center_ra + (fov.width_deg / 2.0) / cos_center_dec + ra_step

    lines: list[list[Point]] = []
    labels: list[GridLabel] = []

    dec_samples = np.linspace(dec_lo, dec_hi, _GRID_SAMPLES_PER_LINE)
    first_ra = math.ceil(ra_lo / ra_step) * ra_step
    ra_value = first_ra
    while ra_value <= ra_hi:
        xs, ys = wcs.world_to_pixel(np.full(_GRID_SAMPLES_PER_LINE, ra_value), dec_samples)
        segments = _clip_polyline_to_frame(xs, ys, width, height)
        lines.extend(segments)
        if style.show_labels:
            for segment in segments:
                lx, ly = _pick_grid_label_point(segment, "ra", style, width, height)
                labels.append(GridLabel(lx, ly, _format_ra_sexagesimal(ra_value), axis="ra"))
        ra_value += ra_step

    ra_samples = np.linspace(ra_lo, ra_hi, _GRID_SAMPLES_PER_LINE)
    first_dec = math.ceil(dec_lo / dec_step) * dec_step
    dec_value = first_dec
    while dec_value <= dec_hi:
        xs, ys = wcs.world_to_pixel(ra_samples, np.full(_GRID_SAMPLES_PER_LINE, dec_value))
        segments = _clip_polyline_to_frame(xs, ys, width, height)
        lines.extend(segments)
        if style.show_labels:
            for segment in segments:
                lx, ly = _pick_grid_label_point(segment, "dec", style, width, height)
                labels.append(GridLabel(lx, ly, _format_dec_sexagesimal(dec_value), axis="dec"))
        dec_value += dec_step

    return GridGeometry(lines=lines, labels=labels, style=style)


# ------------------------------------------------------------------ compass ----

_COMPASS_ANGULAR_DELTA_DEG = 0.01
_COMPASS_DEFAULT_ANCHOR_FRACTION = 0.92  # bottom-right, per user request


@dataclass(frozen=True)
class CompassGeometry:
    anchor: Point
    north_end: Point
    east_end: Point
    style: CompassStyle


def compute_compass_geometry(wcs: SirilWcs, style: CompassStyle) -> CompassGeometry | None:
    if not style.enabled:
        return None
    width, height = float(wcs.native_width), float(wcs.native_height)
    anchor_x = style.anchor_x if style.anchor_x is not None else width * _COMPASS_DEFAULT_ANCHOR_FRACTION
    anchor_y = style.anchor_y if style.anchor_y is not None else height * _COMPASS_DEFAULT_ANCHOR_FRACTION

    # Sampled at the anchor's own sky position (not necessarily the image center) so
    # the arrow direction stays locally accurate even when the anchor has been
    # dragged, in a field with real rotation or distortion.
    ref_ra, ref_dec = wcs.pixel_to_world(anchor_x, anchor_y)
    cos_dec = max(math.cos(math.radians(ref_dec)), 1e-6)
    north_x, north_y = wcs.world_to_pixel(ref_ra, ref_dec + _COMPASS_ANGULAR_DELTA_DEG)
    east_x, east_y = wcs.world_to_pixel(ref_ra + _COMPASS_ANGULAR_DELTA_DEG / cos_dec, ref_dec)

    arrow_len = min(width, height) * style.arrow_length_fraction

    def _scaled_end(tx: float, ty: float) -> Point:
        dx, dy = tx - anchor_x, ty - anchor_y
        dist = math.hypot(dx, dy)
        if dist <= 1e-9:
            return anchor_x, anchor_y
        return anchor_x + dx / dist * arrow_len, anchor_y + dy / dist * arrow_len

    return CompassGeometry(
        anchor=(anchor_x, anchor_y),
        north_end=_scaled_end(north_x, north_y),
        east_end=_scaled_end(east_x, east_y),
        style=style,
    )
