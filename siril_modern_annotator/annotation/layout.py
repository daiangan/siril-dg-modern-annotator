"""Automatic label placement / collision avoidance ("Auto Arrange", brief #11).

Pure data/geometry, no Qt. The GUI may inject a more accurate `text_measurer` callable
(backed by QFontMetrics) for pixel-perfect bounding boxes; the default estimator here is
a reasonable character-count heuristic used both for headless/testable behavior and as
the fallback when no measurer is supplied.

Algorithm (per brief #11):
  1. Sort objects by priority (lower number = more important, placed first).
  2. Generate 8 candidate label positions around each marker (compass directions), at
     a couple of distance rings.
  3. Score each candidate by overlap with already-placed labels/markers and by how far
     it falls outside the image bounds.
  4. Pick the lowest-scoring candidate; add its bounding box to the occupied set.
  5. Manually positioned / locked annotations are never moved, but their boxes still
     count as obstacles for everything placed after them.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Iterable

from .models import Annotation, LabelStyle, StylePreset

TextMeasurer = Callable[[str, LabelStyle], tuple[float, float]]
MarkerRadiusFn = Callable[[Annotation], float]

_DIRECTIONS = [
    "N", "NE", "E", "SE", "S", "SW", "W", "NW",
]
# The gap between a marker's edge and its auto-placed label, as (minimum px, extra
# fraction of the marker's own radius). A real screenshot showed labels landing right at
# -- or inside -- the connector's "already attached" threshold (radius + a few px) by
# default, so the name looked stuck to the circle with no visible connector line, which
# is not how Siril's own default annotation looks. _LABEL_GAP_MIN_PX guarantees a clearly
# visible offset even for small markers; the fractional term gives large (angular-size-
# scaled) markers a bit more breathing room without the gap becoming huge in absolute terms.
_LABEL_GAP_MIN_PX = 28.0
_LABEL_GAP_RADIUS_FRACTION = 0.12
_DISTANCE_RING_GAP_MULTIPLIERS = (1.0, 1.8)

# Large fixed penalty per unit overlap area, dwarfing the small distance tie-breaker so
# "no collision" candidates always win over "closer but colliding" ones.
_OVERLAP_PENALTY = 1.0
_OUT_OF_BOUNDS_PENALTY = 4.0
_DISTANCE_TIEBREAK_WEIGHT = 0.001


@dataclass(frozen=True)
class BBox:
    x0: float
    y0: float
    x1: float
    y1: float

    def overlap_area(self, other: "BBox") -> float:
        ix0, iy0 = max(self.x0, other.x0), max(self.y0, other.y0)
        ix1, iy1 = min(self.x1, other.x1), min(self.y1, other.y1)
        if ix1 <= ix0 or iy1 <= iy0:
            return 0.0
        return (ix1 - ix0) * (iy1 - iy0)

    def out_of_bounds_area(self, width: float, height: float) -> float:
        clipped = BBox(
            max(self.x0, 0.0), max(self.y0, 0.0), min(self.x1, width), min(self.y1, height)
        )
        clipped_area = max(0.0, clipped.x1 - clipped.x0) * max(0.0, clipped.y1 - clipped.y0)
        full_area = max(0.0, self.x1 - self.x0) * max(0.0, self.y1 - self.y0)
        return max(0.0, full_area - clipped_area)


def _default_text_measurer(text: str, style: LabelStyle) -> tuple[float, float]:
    """Mirrors annotation.renderer.default_text_measurer (duplicated rather than
    imported to avoid a circular import -- renderer.py imports BBox from this module).
    Supports multi-line text (custom display names/notes can span several lines)."""
    lines = text.split("\n") or [""]
    line_height = style.font_size * 1.35
    width = max((len(line) * style.font_size * 0.62 for line in lines), default=0.0)
    height = line_height * len(lines)
    return width + 2 * style.padding, height + 2 * style.padding


def _marker_bbox(ann: Annotation, radius: float) -> BBox:
    return BBox(
        ann.image_x - radius, ann.image_y - radius, ann.image_x + radius, ann.image_y + radius
    )


def _candidate_bbox(cx: float, cy: float, direction: str, w: float, h: float) -> tuple[float, float, BBox]:
    """Given an anchor point (cx, cy) and a compass direction, return the label's
    (label_x, label_y) top-left position plus its bounding box."""
    gap = 4.0
    if direction == "N":
        x, y = cx - w / 2, cy - gap - h
    elif direction == "S":
        x, y = cx - w / 2, cy + gap
    elif direction == "E":
        x, y = cx + gap, cy - h / 2
    elif direction == "W":
        x, y = cx - gap - w, cy - h / 2
    elif direction == "NE":
        x, y = cx + gap, cy - gap - h
    elif direction == "SE":
        x, y = cx + gap, cy + gap
    elif direction == "SW":
        x, y = cx - gap - w, cy + gap
    else:  # NW
        x, y = cx - gap - w, cy - gap - h
    return x, y, BBox(x, y, x + w, y + h)


def auto_arrange(
    annotations: Iterable[Annotation],
    global_style: StylePreset,
    image_width: float,
    image_height: float,
    text_measurer: TextMeasurer | None = None,
    keep_manual: bool = True,
    marker_radius_fn: MarkerRadiusFn | None = None,
) -> None:
    """Mutates label_x/label_y in place for every enabled annotation not excluded by
    keep_manual (manually_positioned or locked).

    marker_radius_fn should return each annotation's *actual rendered* marker radius --
    not just MarkerStyle.radius -- when angular-size-based marker scaling is in play
    (annotation.renderer.compute_marker_geometry). Without it, a real bug was confirmed:
    a large object's real on-screen circle (e.g. M31 at ~770px capped radius) is many
    times bigger than the flat style radius (~18px) used to pick a candidate distance,
    so the label lands deep inside the marker instead of near its edge, and the
    connector line's "already attached" check (annotation.renderer's
    _ATTACHED_THRESHOLD_PX vs. the real radius) then suppresses the connector too. The
    default here (flat style radius) is kept only for headless testability where no
    angular-size scaling is in play.
    """
    measurer = text_measurer or _default_text_measurer
    radius_fn = marker_radius_fn or (
        lambda a: a.effective_marker_style(global_style).radius
    )
    items = [a for a in annotations if a.enabled]
    ordered = sorted(items, key=lambda a: (a.priority, a.catalog_name))

    occupied: list[BBox] = []
    fixed = [
        a for a in ordered if keep_manual and (a.manually_positioned or a.locked)
    ]
    movable = [a for a in ordered if a not in fixed]

    for ann in fixed:
        occupied.append(_marker_bbox(ann, radius_fn(ann)))
        if ann.label_x is not None and ann.label_y is not None:
            label_style = ann.effective_label_style(global_style)
            text = ann.display_name(label_style.name_display)
            w, h = measurer(text, label_style)
            occupied.append(BBox(ann.label_x, ann.label_y, ann.label_x + w, ann.label_y + h))

    for ann in movable:
        marker_radius = radius_fn(ann)
        label_style = ann.effective_label_style(global_style)
        text = ann.display_name(label_style.name_display)
        w, h = measurer(text, label_style)
        marker_box = _marker_bbox(ann, marker_radius)
        occupied.append(marker_box)

        best_score = None
        best_pos = None
        best_bbox = None
        base_gap = max(_LABEL_GAP_MIN_PX, marker_radius * _LABEL_GAP_RADIUS_FRACTION)
        for ring in _DISTANCE_RING_GAP_MULTIPLIERS:
            radius = marker_radius + base_gap * ring
            for direction in _DIRECTIONS:
                cx, cy = _anchor_point(ann.image_x, ann.image_y, direction, radius)
                x, y, bbox = _candidate_bbox(cx, cy, direction, w, h)
                overlap = sum(bbox.overlap_area(o) for o in occupied)
                oob = bbox.out_of_bounds_area(image_width, image_height)
                distance = radius
                score = (
                    overlap * _OVERLAP_PENALTY
                    + oob * _OUT_OF_BOUNDS_PENALTY
                    + distance * _DISTANCE_TIEBREAK_WEIGHT
                )
                if best_score is None or score < best_score:
                    best_score = score
                    best_pos = (x, y)
                    best_bbox = bbox

        ann.label_x, ann.label_y = best_pos
        occupied.append(best_bbox)


def _anchor_point(mx: float, my: float, direction: str, radius: float) -> tuple[float, float]:
    import math

    angles = {
        "N": 90, "NE": 45, "E": 0, "SE": -45, "S": -90, "SW": -135, "W": 180, "NW": 135,
    }
    angle = math.radians(angles[direction])
    # Image Y grows downward in pixel space; "N" (up) should decrease y.
    return mx + radius * math.cos(angle), my - radius * math.sin(angle)
