"""Style presets: built-in (code-defined, brief #17) plus user-created (QSettings-backed).

Built-in presets are never persisted to QSettings, so a corrupted or reset settings file
can never lose them; user presets layer on top under a separate settings group.
"""

from __future__ import annotations

import json
from dataclasses import asdict, replace

from PyQt6.QtCore import QSettings

from ..annotation.models import (
    BackgroundMode,
    CompassStyle,
    ConnectorStyle,
    GridStyle,
    InfoBoxStyle,
    LabelStyle,
    MarkerShape,
    MarkerStyle,
    NameDisplayMode,
    OverlaySettings,
    StylePreset,
)
from .project import label_style_from_dict, marker_style_from_dict, style_preset_from_dict, to_jsonable

ORG_NAME = "SirilModernAnnotator"
APP_NAME = "AnnotationEditor"
_USER_PRESETS_KEY = "user_style_presets"


def _builtin_presets() -> dict[str, StylePreset]:
    # Only the default preset ships built-in now -- the other four (Scientific,
    # Outreach, Social Media, Print) were removed per user request after "Scientific"'s
    # much smaller flat radius/font-size left them with a field of tiny, hard-to-read
    # markers and no quick way back to a sane default (see reset_style_btn in
    # style_panel.py, which now handles "go back to default" instead of relying on
    # re-selecting a preset). Users can still build and save their own via "Save As…".
    return {
        "Minimal Modern": StylePreset(
            name="Minimal Modern",
            marker_style=MarkerStyle(shape=MarkerShape.CIRCLE, color="#f5f5f5", stroke_width=1.0, radius=12.0, opacity=0.85),
            # "Inter" isn't bundled with macOS or Windows by default -- a real user
            # report confirmed it silently falls back to Tahoma on Windows (no warning
            # at all, unlike the Qt console warning this triggers on macOS), so labels
            # were never actually rendering in the intended font on either platform.
            # "Verdana" ships with both out of the box and reads cleanly at small
            # on-image label sizes.
            label_style=LabelStyle(font_family="Verdana", font_size=11.0, text_color="#f5f5f5", background_mode=BackgroundMode.TRANSLUCENT, shadow=True, name_display=NameDisplayMode.COMMON_THEN_CATALOG),
            connector_style=ConnectorStyle.STRAIGHT,
            connector_color="#a0a0a0",
            connector_width=0.8,
        ),
    }


BUILTIN_PRESETS: dict[str, StylePreset] = _builtin_presets()
DEFAULT_PRESET_NAME = "Minimal Modern"


def default_preset() -> StylePreset:
    return BUILTIN_PRESETS[DEFAULT_PRESET_NAME]


# Marker *radius* still needs to track image resolution -- our geometry is native-pixel
# space (ARCHITECTURE.md #4), and a flat radius looked fine at one resolution and was
# nearly invisible at another (confirmed against a real 2952x1936 image). Stroke width
# and font size, by contrast, are legibility choices, not size-of-object choices: real-
# world testing converged on fixed, resolution-independent defaults (6px strokes, 60pt
# labels) that read clearly at typical "fit to window" zoom regardless of native
# resolution, rather than compounding with the radius scale factor. Padding/corner
# radius stay proportional to the *new* font size (not to image resolution) so their
# visual ratio to the text matches what the base preset intended.
_REFERENCE_LONG_EDGE_PX = 2000.0
_DEFAULT_STROKE_WIDTH_PX = 6.0
_DEFAULT_FONT_SIZE_PT = 60.0


def default_preset_for_image(width: int, height: int) -> StylePreset:
    base = default_preset()
    radius_scale = max(1.0, max(width, height) / _REFERENCE_LONG_EDGE_PX)
    typography_scale = _DEFAULT_FONT_SIZE_PT / base.label_style.font_size
    marker = replace(
        base.marker_style,
        radius=base.marker_style.radius * radius_scale,
        stroke_width=_DEFAULT_STROKE_WIDTH_PX,
        size_from_angular_size=True,
    )
    label = replace(
        base.label_style,
        font_size=_DEFAULT_FONT_SIZE_PT,
        padding=base.label_style.padding * typography_scale,
        corner_radius=base.label_style.corner_radius * typography_scale,
    )
    return replace(
        base,
        marker_style=marker,
        label_style=label,
        connector_width=_DEFAULT_STROKE_WIDTH_PX,
    )


# Unlike the object-label font size above (deliberately flat -- see the comment on
# that), grid/compass label text has no background box behind it and reads as either
# "fine" or "invisible" depending on how it scales with the frame -- per a real
# report, the flat 11-13pt default was unreadable on a large image. Proportional to
# the shorter image dimension so it stays legible at "fit to window" zoom regardless
# of native resolution, floored so a small image doesn't get illegibly tiny text either.
_OVERLAY_LABEL_FRACTION = 0.015
_OVERLAY_LABEL_MIN_PT = 14.0

# Same problem as the label font size, for the grid's and compass's own line strokes:
# line_width is native-pixel-space (GridItem.paint/CompassItem.paint draw directly in
# scene/native coordinates), so a flat width that looks fine on a modest image becomes
# a near-invisible hairline once the image's native resolution climbs into the tens of
# megapixels -- per user report. Same fraction for both so they read as the same visual
# weight; each floored at its own flat dataclass default so a small image doesn't get
# an oversized line, and so the compass (originally 1.6px, vs. the grid's 1.0px) keeps
# its slightly heavier look at low resolution too.
_OVERLAY_LINE_WIDTH_FRACTION = 0.0008
_GRID_LINE_WIDTH_MIN_PX = 1.0
_COMPASS_LINE_WIDTH_MIN_PX = 1.6


def default_overlay_settings_for_image(width: int, height: int) -> OverlaySettings:
    label_size = max(_OVERLAY_LABEL_MIN_PT, min(width, height) * _OVERLAY_LABEL_FRACTION)
    short_edge = min(width, height)
    grid_line_width = max(_GRID_LINE_WIDTH_MIN_PX, short_edge * _OVERLAY_LINE_WIDTH_FRACTION)
    compass_line_width = max(_COMPASS_LINE_WIDTH_MIN_PX, short_edge * _OVERLAY_LINE_WIDTH_FRACTION)
    # Info box padding/border radius/margin scale with the same ratio as its font
    # size, relative to InfoBoxStyle's own flat defaults -- same reasoning as
    # label_size above (a flat size looks fine at one resolution, tiny at another),
    # applied to the whole box's proportions rather than just its text.
    info_box_defaults = InfoBoxStyle()
    scale_factor = label_size / info_box_defaults.font_size
    return OverlaySettings(
        grid=GridStyle(label_font_size=label_size, line_width=grid_line_width),
        compass=CompassStyle(label_font_size=label_size, line_width=compass_line_width),
        info_box=InfoBoxStyle(
            font_size=label_size,
            padding=info_box_defaults.padding * scale_factor,
            border_radius=info_box_defaults.border_radius * scale_factor,
            margin=info_box_defaults.margin * scale_factor,
        ),
    )


def _preset_to_dict(preset: StylePreset) -> dict:
    return to_jsonable(asdict(preset))


def _settings() -> QSettings:
    return QSettings(ORG_NAME, APP_NAME)


def load_user_presets() -> dict[str, StylePreset]:
    raw = _settings().value(_USER_PRESETS_KEY, "")
    if not raw:
        return {}
    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return {}
    return {name: style_preset_from_dict(d) for name, d in data.items()}


def save_user_preset(preset: StylePreset) -> None:
    presets = load_user_presets()
    presets[preset.name] = preset
    _settings().setValue(
        _USER_PRESETS_KEY,
        json.dumps({name: _preset_to_dict(p) for name, p in presets.items()}),
    )


def delete_user_preset(name: str) -> None:
    presets = load_user_presets()
    presets.pop(name, None)
    _settings().setValue(
        _USER_PRESETS_KEY,
        json.dumps({n: _preset_to_dict(p) for n, p in presets.items()}),
    )


def all_presets() -> dict[str, StylePreset]:
    merged = dict(BUILTIN_PRESETS)
    merged.update(load_user_presets())
    return merged
