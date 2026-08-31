"""Full-resolution compositing and PNG/JPEG/TIFF export (brief #21-23).

Per RESEARCH.md #11, sirilpy's only save helper (save_image_file) is FITS-only, so
PNG/JPEG/TIFF export is plain Python: Pillow for 8-bit formats, tifffile for 16-bit TIFF.
This module never imports PyQt6 — it takes a plain NumPy array plus a plain list of
Annotation objects and returns bytes/writes a file, so it can run inside a QThread
worker (ARCHITECTURE.md #9-10) and be unit-tested headlessly.

The renderer used here (annotation.renderer geometry + Pillow ImageDraw here) is
independent of the interactive Qt canvas's renderer, but both call the *same* geometry
functions in annotation/renderer.py, guaranteeing identical placement.
"""

from __future__ import annotations

import logging
import math
import platform
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

import numpy as np
from PIL import Image, ImageDraw, ImageFont

logger = logging.getLogger(__name__)

from ..annotation.models import (
    Annotation,
    BackgroundMode,
    LabelStyle,
    MarkerShape,
    OverlaySettings,
    StylePreset,
)
from ..annotation.pixel_utils import correct_fits_row_order, to_hwc_uint8
from ..annotation.renderer import (
    GRID_LABEL_LINE_GAP_PX,
    default_max_marker_radius_px,
    clamp_rotated_label_point,
    compute_compass_geometry,
    compute_connector_points,
    compute_grid_geometry,
    compute_info_box_geometry,
    compute_label_geometry,
    compute_marker_geometry,
    grid_label_perpendicular_offset,
    resolve_connector_color,
)
from ..annotation.wcs import SirilWcs
from ..persistence.project import ExportSettings

ProgressCallback = Callable[[str], None]


def _noop_progress(_msg: str) -> None:
    return None


def resolve_output_size(
    native_width: int, native_height: int, settings: ExportSettings
) -> tuple[int, int]:
    if settings.resolution_mode == "scale":
        factor = settings.scale_percent / 100.0
        return max(1, round(native_width * factor)), max(1, round(native_height * factor))
    if settings.resolution_mode == "custom":
        if settings.custom_width and not settings.custom_height:
            aspect = native_height / native_width
            return settings.custom_width, max(1, round(settings.custom_width * aspect))
        if settings.custom_height and not settings.custom_width:
            aspect = native_width / native_height
            return max(1, round(settings.custom_height * aspect)), settings.custom_height
        if settings.custom_width and settings.custom_height:
            return settings.custom_width, settings.custom_height
    return native_width, native_height


def _to_uint8_rgb(pixel_data: np.ndarray) -> np.ndarray:
    """Normalizes Siril pixel data (uint16/float32, mono or RGB, channel position not
    assumed -- see annotation.pixel_utils) into an 8-bit HxWx3 RGB array for
    compositing/export."""
    return correct_fits_row_order(to_hwc_uint8(pixel_data))


_WINDOWS_FONTS_DIR = Path("C:/Windows/Fonts")
# /System/Library/Fonts/Supplemental is where macOS actually keeps the classic
# "Microsoft core fonts" set (Verdana, Georgia, Times New Roman, Comic Sans, ...) --
# confirmed by direct inspection of a real machine; the top-level Fonts folder only has
# Apple's own system faces (San Francisco, Helvetica, etc.), not these.
_MACOS_FONTS_DIRS = [
    Path("/System/Library/Fonts/Supplemental"),
    Path("/System/Library/Fonts"),
    Path("/Library/Fonts"),
]
_LINUX_FONTS_DIRS = [Path("/usr/share/fonts"), Path("/usr/local/share/fonts")]


def _font_file_candidates(family_name: str) -> list[Path]:
    """Best-effort file-path guesses for a font family name, keyed by platform. Needed
    because Pillow's ImageFont.truetype() resolving a bare family name (rather than an
    actual file path) is a well-known unreliable operation on Windows -- it can raise
    OSError even when the font is genuinely installed, rather than finding it the way
    fontconfig does on Linux/macOS. Confirmed by a real report: Bayer-designation Greek
    letters (annotation.catalogs.bayer_designation_to_greek, e.g. "ξ Sco") rendered
    correctly in the live Qt canvas -- Qt has its own font-fallback machinery Pillow
    lacks -- but showed as a missing-glyph box in the exported image, while the plain-
    ASCII part of the same label ("Sco") rendered fine. That ASCII-fine/non-ASCII-
    broken split is the signature of silently landing in the `except OSError` branch
    below and falling back to Pillow's own minimal built-in font, which has very
    limited Unicode coverage -- not of the requested font itself lacking the glyph."""
    slug = family_name.lower().replace(" ", "")
    system = platform.system()
    if system == "Windows":
        return [_WINDOWS_FONTS_DIR / f"{slug}.ttf", _WINDOWS_FONTS_DIR / f"{slug}.ttc"]
    if system == "Darwin":
        return [d / f"{family_name}.ttf" for d in _MACOS_FONTS_DIRS] + [
            d / f"{family_name}.ttc" for d in _MACOS_FONTS_DIRS
        ]
    return [d / f"{slug}.ttf" for d in _LINUX_FONTS_DIRS]


# Verdana specifically because it's the app's own shipped default (theme_dark.qss,
# presets.py) and confirmed present on both macOS and Windows by default -- used here
# as a safety net when the *requested* font isn't installed at all (not merely
# unreliably located by name), which is a real, separate case from the bare-name-
# lookup issue _font_file_candidates works around: a user's already-saved style
# (persistence/last_used.py saves on every edit) can still reference an older default
# font_family from before this app's own default changed, and no amount of file-path
# guessing finds a font that was never installed to begin with.
_FALLBACK_FONT_FAMILY = "Verdana"


def _resolve_font_file(family_name: str, size: int) -> ImageFont.FreeTypeFont | None:
    """Tries a bare-name lookup, then well-known file-path guesses. Returns None if
    neither works -- i.e. this family isn't installed under this name at all, not just
    unreliably located (see _font_file_candidates)."""
    try:
        return ImageFont.truetype(family_name, size)
    except OSError:
        pass
    for candidate in _font_file_candidates(family_name):
        if candidate.is_file():
            try:
                return ImageFont.truetype(str(candidate), size)
            except OSError:
                continue
    return None


def _font_for_style(style, scale: float) -> ImageFont.FreeTypeFont:
    size = max(1, round(style.font_size * scale))
    font = _resolve_font_file(style.font_family, size)
    if font is not None:
        return font

    if style.font_family != _FALLBACK_FONT_FAMILY:
        # debug, not warning: this is an expected, silently-handled path (a user's
        # already-saved style referencing an older default font_family), not an actual
        # problem -- per user request, nothing about it should surface in Siril's log.
        # Python's logging module writes to this process's stderr by default, which
        # Siril captures and displays as if it came from Siril itself; a WARNING here
        # showed up looking like a real error even though the fallback works correctly.
        logger.debug(
            "Font '%s' is not installed on this system; falling back to '%s' for export.",
            style.font_family, _FALLBACK_FONT_FAMILY,
        )
        font = _resolve_font_file(_FALLBACK_FONT_FAMILY, size)
        if font is not None:
            return font

    logger.debug(
        "Could not locate '%s' or the fallback font '%s' by name or file path; using "
        "Pillow's built-in default font (limited Unicode coverage).",
        style.font_family, _FALLBACK_FONT_FAMILY,
    )
    return ImageFont.load_default(size=size)


def _pillow_text_measurer(text: str, style) -> tuple[float, float]:
    """Measures with the actual font Pillow will draw with, at native (unscaled) size --
    render_annotations later scales the whole bbox uniformly, same as the GUI's
    qt_text_measurer measures with the actual Qt font instead of a character-count
    heuristic. Using the generic annotation.renderer.default_text_measurer heuristic
    here instead would size the label's background box off a rough guess rather than
    the real rendered text, breaking preview/export parity for label sizing.

    Multi-line-aware: custom display names/notes can span several lines (a "bigger
    tooltip"-style description) -- width is the widest line, height is per-line-height
    times the number of lines, matching gui/annotation_item.py's qt_text_measurer."""
    font = _font_for_style(style, scale=1.0)
    lines = text.split("\n") or [""]
    max_width = 0.0
    for line in lines:
        left, right = 0, 0
        if line:
            left, _, right, _ = font.getbbox(line)
        max_width = max(max_width, right - left)
    ascent, descent = font.getmetrics()
    line_height = ascent + descent
    width = max_width + 3.0  # small safety margin, matching gui/annotation_item.py
    height = line_height * len(lines) + 3.0
    return width + 2 * style.padding, height + 2 * style.padding


def render_annotations(
    base_rgb: np.ndarray,
    annotations: list[Annotation],
    global_style: StylePreset,
    output_width: int,
    output_height: int,
    arcsec_per_px: float | None = None,
    catalog_colors: dict[str, str] | None = None,
    wcs: SirilWcs | None = None,
    overlay_settings: OverlaySettings | None = None,
) -> Image.Image:
    """Composites enabled annotations onto base_rgb (already at output_width x
    output_height) using the shared geometry from annotation.renderer, scaled by
    output_width / native_width."""
    native_height, native_width = base_rgb.shape[0], base_rgb.shape[1]
    scale = output_width / native_width if native_width else 1.0
    max_radius_px = default_max_marker_radius_px(native_width, native_height)

    image = Image.fromarray(base_rgb, mode="RGB")
    if (output_width, output_height) != (native_width, native_height):
        image = image.resize((output_width, output_height), Image.LANCZOS)
    image = image.convert("RGBA")
    overlay = Image.new("RGBA", image.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)

    # Grid first (underneath everything, matching GridItem's low z-value in the
    # interactive canvas), objects next, info box after that, compass last (always on
    # top, matching each item's z-value in the interactive canvas: grid=1, marker=10,
    # label=20, info box=25, compass=30) -- grid/compass need a real WCS, silently
    # skipped otherwise (e.g. exporting before an image ever loaded, which shouldn't
    # normally happen but costs nothing to guard).
    if wcs is not None and overlay_settings is not None:
        _draw_grid(overlay, wcs, overlay_settings.grid, scale)

    for ann in annotations:
        if not ann.enabled:
            continue
        marker = compute_marker_geometry(ann, global_style, arcsec_per_px, max_radius_px, catalog_colors)
        label = compute_label_geometry(ann, global_style, _pillow_text_measurer, catalog_colors)
        _draw_connector(draw, ann, marker, label, global_style, scale, catalog_colors)
        _draw_marker(draw, marker, scale)
        _draw_label(draw, label, scale)

    if overlay_settings is not None:
        _draw_info_box(draw, overlay_settings.info_box, native_width, native_height, scale)

    if wcs is not None and overlay_settings is not None:
        _draw_compass(draw, wcs, overlay_settings.compass, scale)

    return Image.alpha_composite(image, overlay).convert("RGB")


def _scaled(pt: tuple[float, float], scale: float) -> tuple[float, float]:
    return pt[0] * scale, pt[1] * scale


def _draw_rotated_ellipse_outline(
    draw: ImageDraw.ImageDraw,
    cx: float, cy: float,
    radius_x: float, radius_y: float,
    rotation_deg: float,
    color: tuple[int, int, int, int],
    width: int,
) -> None:
    theta = math.radians(rotation_deg)
    cos_t, sin_t = math.cos(theta), math.sin(theta)
    steps = 72
    points = []
    for i in range(steps + 1):
        a = 2 * math.pi * i / steps
        local_x, local_y = radius_x * math.cos(a), radius_y * math.sin(a)
        points.append((
            cx + local_x * cos_t - local_y * sin_t,
            cy + local_x * sin_t + local_y * cos_t,
        ))
    for p0, p1 in zip(points, points[1:]):
        draw.line([p0[0], p0[1], p1[0], p1[1]], fill=color, width=width)


def _draw_marker(draw: ImageDraw.ImageDraw, marker, scale: float) -> None:
    style = marker.style
    if style.shape is MarkerShape.NONE:
        return
    cx, cy = marker.x * scale, marker.y * scale
    r = marker.radius * scale
    color = _rgba(style.color, style.opacity)
    width = max(1, round(style.stroke_width * scale))

    if style.shape is MarkerShape.CIRCLE:
        draw.ellipse([cx - r, cy - r, cx + r, cy + r], outline=color, width=width)
    elif style.shape is MarkerShape.DOT:
        rad = max(1.5, style.stroke_width) * scale
        draw.ellipse([cx - rad, cy - rad, cx + rad, cy + rad], fill=color)
    elif style.shape is MarkerShape.CROSSHAIR:
        draw.line([cx - r, cy, cx + r, cy], fill=color, width=width)
        draw.line([cx, cy - r, cx, cy + r], fill=color, width=width)
    elif style.shape is MarkerShape.RETICLE:
        draw.ellipse([cx - r, cy - r, cx + r, cy + r], outline=color, width=width)
        gap = r * 0.35
        for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            draw.line(
                [cx + dx * (r + 2), cy + dy * (r + 2), cx + dx * (r + gap), cy + dy * (r + gap)],
                fill=color, width=width,
            )
    elif style.shape is MarkerShape.BRACKETS:
        arm = r * 0.5
        corners = [(-r, -r), (r, -r), (r, r), (-r, r)]
        directions = [((1, 0), (0, 1)), ((-1, 0), (0, 1)), ((-1, 0), (0, -1)), ((1, 0), (0, -1))]
        for (ox, oy), ((hx, hy), (vx, vy)) in zip(corners, directions):
            px, py = cx + ox, cy + oy
            draw.line([px, py, px + hx * arm, py + hy * arm], fill=color, width=width)
            draw.line([px, py, px + vx * arm, py + vy * arm], fill=color, width=width)
    elif style.shape is MarkerShape.ELLIPSE:
        # Pillow's ImageDraw.ellipse() only draws axis-aligned ellipses -- no rotation
        # support -- so a rotated oval is approximated as a closed polyline around its
        # true (rotated) boundary instead, same "manual line segments" approach
        # BRACKETS above already uses for a shape Pillow has no primitive for.
        _draw_rotated_ellipse_outline(
            draw, cx, cy, marker.radius_x * scale, marker.radius_y * scale,
            marker.rotation_deg, color, width,
        )


def _draw_label(draw: ImageDraw.ImageDraw, label, scale: float) -> None:
    style = label.style
    x0, y0 = label.bbox.x0 * scale, label.bbox.y0 * scale
    x1, y1 = label.bbox.x1 * scale, label.bbox.y1 * scale
    if style.background_mode is not BackgroundMode.NONE:
        alpha = style.background_opacity if style.background_mode is BackgroundMode.TRANSLUCENT else 1.0
        draw.rounded_rectangle(
            [x0, y0, x1, y1],
            radius=max(0, style.corner_radius * scale),
            fill=_rgba(style.background_color, alpha),
        )
    font = _font_for_style(style, scale)
    pad = style.padding * scale
    text_pos = (x0 + pad, y0 + pad)
    # multiline_text (not text) so an embedded "\n" in a custom display name/notes
    # renders as multiple lines instead of Pillow drawing it as one line with a glyph
    # gap -- label.bbox is already sized for every line via _pillow_text_measurer.
    if style.shadow:
        draw.multiline_text((text_pos[0] + 1, text_pos[1] + 1), label.text, font=font, fill=(0, 0, 0, 160))
    if style.outline:
        outline_color = _rgba(style.outline_color, 1.0)
        for dx, dy in ((-1, 0), (1, 0), (0, -1), (0, 1)):
            draw.multiline_text((text_pos[0] + dx, text_pos[1] + dy), label.text, font=font, fill=outline_color)
    draw.multiline_text(text_pos, label.text, font=font, fill=_rgba(style.text_color, 1.0))


def _draw_connector(draw, ann, marker, label, global_style, scale: float, catalog_colors=None) -> None:
    connector_style = ann.effective_connector_style(global_style)
    points = compute_connector_points(ann, marker, label, connector_style)
    if not points:
        return
    scaled = [_scaled(p, scale) for p in points]
    color = _rgba(resolve_connector_color(ann, global_style, catalog_colors), 0.85)
    connector_width = ann.effective_connector_width(global_style)
    width = max(1, round(connector_width * scale))
    if len(scaled) == 3:
        # Approximate the quadratic bezier with a short polyline for raster export.
        pts = _quad_bezier_points(scaled[0], scaled[1], scaled[2], steps=16)
        draw.line(pts, fill=color, width=width, joint="curve")
    else:
        draw.line(scaled, fill=color, width=width)


def _quad_bezier_points(p0, p1, p2, steps: int) -> list[tuple[float, float]]:
    pts = []
    for i in range(steps + 1):
        t = i / steps
        x = (1 - t) ** 2 * p0[0] + 2 * (1 - t) * t * p1[0] + t**2 * p2[0]
        y = (1 - t) ** 2 * p0[1] + 2 * (1 - t) * t * p1[1] + t**2 * p2[1]
        pts.append((x, y))
    return pts


def _rgba(hex_color: str, alpha: float) -> tuple[int, int, int, int]:
    hex_color = hex_color.lstrip("#")
    r, g, b = int(hex_color[0:2], 16), int(hex_color[2:4], 16), int(hex_color[4:6], 16)
    return r, g, b, max(0, min(255, round(alpha * 255)))


def _draw_rotated_text(
    overlay: Image.Image, center: tuple[float, float], text: str, angle_deg: float,
    font: ImageFont.FreeTypeFont, fill,
) -> None:
    """Pillow's ImageDraw has no rotated-text support -- render onto a small
    transparent layer sized to the text, rotate that layer, then alpha-composite it
    centered on `center`. Mirrors _draw_rotated_ellipse_outline's "no library shortcut"
    approach to the same underlying Pillow limitation, just for text instead of a
    shape."""
    bbox = font.getbbox(text)
    text_w, text_h = bbox[2] - bbox[0], bbox[3] - bbox[1]
    if text_w <= 0 or text_h <= 0:
        return
    pad = 4  # headroom so antialiased glyph edges aren't clipped by the rotation
    layer = Image.new("RGBA", (text_w + pad * 2, text_h + pad * 2), (0, 0, 0, 0))
    ImageDraw.Draw(layer).text((pad - bbox[0], pad - bbox[1]), text, font=font, fill=fill)
    # Pillow's rotate() is counter-clockwise-positive for a visually upright image;
    # angle_deg is measured directly off y-down pixel coordinates (clockwise-positive,
    # matching Qt's rotate() in gui/overlay_item.py -- confirmed against a real
    # headless render), so it needs negating here to produce the same visual result as
    # the interactive canvas.
    rotated = layer.rotate(-angle_deg, expand=True, resample=Image.BICUBIC)
    paste_x = round(center[0] - rotated.width / 2.0)
    paste_y = round(center[1] - rotated.height / 2.0)
    overlay.alpha_composite(rotated, (paste_x, paste_y))


def _draw_grid(overlay: Image.Image, wcs: SirilWcs, style, scale: float) -> None:
    if not style.enabled:
        return
    geo = compute_grid_geometry(wcs, style)
    draw = ImageDraw.Draw(overlay)
    color = _rgba(style.color, style.opacity)
    width = max(1, round(style.line_width * scale))
    for line in geo.lines:
        scaled = [_scaled(p, scale) for p in line]
        draw.line(scaled, fill=color, width=width)
    if geo.labels:
        # Per user report/reference screenshot: labels run along the grid line itself
        # (rotated to match its angle), like Siril's own grid overlay, rather than
        # fixed horizontal/vertical text near an edge -- mirrors gui/overlay_item.py's
        # GridItem.paint exactly, so the export matches what's shown on screen.
        font = _font_for_style(LabelStyle(font_family=_FALLBACK_FONT_FAMILY, font_size=style.label_font_size), scale)
        frame_width, frame_height = wcs.native_width * scale, wcs.native_height * scale
        for label in geo.labels:
            bbox = font.getbbox(label.text)
            text_w, text_h = bbox[2] - bbox[0], bbox[3] - bbox[1]
            cx, cy = clamp_rotated_label_point(
                *_scaled((label.x, label.y), scale), label.rotation_deg, text_w, text_h,
                frame_width, frame_height,
            )
            # Per user request: sit noticeably closer to the line than dead center --
            # nudge toward it by however far brings the label's near edge to
            # GRID_LABEL_LINE_GAP_PX away, using the safety margin the clamp above
            # already reserved rather than reducing it. Mirrors GridItem.paint exactly.
            nudge = max(0.0, text_h / 2.0 - GRID_LABEL_LINE_GAP_PX)
            dx, dy = grid_label_perpendicular_offset(label.rotation_deg, nudge)
            _draw_rotated_text(overlay, (cx + dx, cy + dy), label.text, label.rotation_deg, font, color)


def _draw_compass(draw: ImageDraw.ImageDraw, wcs: SirilWcs, style, scale: float) -> None:
    if not style.enabled:
        return
    geo = compute_compass_geometry(wcs, style)
    if geo is None:
        return
    color = _rgba(style.color, 1.0)
    width = max(1, round(style.line_width * scale))
    anchor = _scaled(geo.anchor, scale)
    north = _scaled(geo.north_end, scale)
    east = _scaled(geo.east_end, scale)
    draw.line([anchor, north], fill=color, width=width)
    draw.line([anchor, east], fill=color, width=width)
    font = _font_for_style(LabelStyle(font_family=_FALLBACK_FONT_FAMILY, font_size=style.label_font_size), scale)
    draw.text(north, "N", fill=color, font=font, anchor="mm")
    draw.text(east, "E", fill=color, font=font, anchor="mm")


def _draw_info_box(draw: ImageDraw.ImageDraw, style, image_width: float, image_height: float, scale: float) -> None:
    # compute_info_box_geometry wraps style's font_size/padding into a LabelStyle
    # internally before calling this measurer -- _pillow_text_measurer only needs
    # font_family/font_size from that wrapper, both present, so this Just Works the
    # same way it already does for per-object labels (see compute_label_geometry's
    # own _pillow_text_measurer call below).
    geo = compute_info_box_geometry(style.text, style, image_width, image_height, _pillow_text_measurer)
    if geo is None:
        return
    x0, y0 = _scaled((geo.bbox.x0, geo.bbox.y0), scale)
    x1, y1 = _scaled((geo.bbox.x1, geo.bbox.y1), scale)
    bg = _rgba(style.background_color, style.background_opacity)
    draw.rounded_rectangle([x0, y0, x1, y1], radius=style.border_radius * scale, fill=bg)
    font = _font_for_style(LabelStyle(font_family=_FALLBACK_FONT_FAMILY, font_size=style.font_size), scale)
    text_color = _rgba(style.text_color, 1.0)
    padding = style.padding * scale
    draw.multiline_text((x0 + padding, y0 + padding), geo.text, fill=text_color, font=font)


def export_image(
    output_path: Path,
    pixel_data: np.ndarray,
    annotations: list[Annotation],
    global_style: StylePreset,
    settings: ExportSettings,
    arcsec_per_px: float | None = None,
    icc_profile: bytes | None = None,
    progress: ProgressCallback = _noop_progress,
    catalog_colors: dict[str, str] | None = None,
    wcs: SirilWcs | None = None,
    overlay_settings: OverlaySettings | None = None,
) -> Path:
    progress("Preparing image data...")
    # Dimensions must come from the *normalized* array, not the raw pixel_data's own
    # shape -- a real, confirmed bug: get_full_pixeldata() can return channels-first
    # data (C, H, W), and reading shape[0]/shape[1] directly off that treated the
    # channel count (3) as the height, producing an export target size only ~3 pixels
    # tall. A user's exported JPG showed exactly this: a thin sliver of real image
    # content at the top with the rest of the frame blank.
    base_rgb = _to_uint8_rgb(pixel_data)
    native_height, native_width = base_rgb.shape[0], base_rgb.shape[1]
    out_w, out_h = resolve_output_size(native_width, native_height, settings)

    progress("Rendering full-resolution image...")
    composited = render_annotations(
        base_rgb, annotations, global_style, out_w, out_h, arcsec_per_px, catalog_colors,
        wcs=wcs, overlay_settings=overlay_settings,
    )

    output_path = Path(output_path)
    fmt = settings.format.lower()
    progress(f"Writing {fmt.upper()}...")

    icc_bytes = icc_profile if icc_profile else None

    if fmt == "jpeg" or fmt == "jpg":
        save_kwargs = {"quality": settings.jpeg_quality, "dpi": (settings.dpi, settings.dpi)}
        if icc_bytes:
            save_kwargs["icc_profile"] = icc_bytes
        composited.save(output_path, format="JPEG", **save_kwargs)
    elif fmt == "png":
        save_kwargs = {"dpi": (settings.dpi, settings.dpi)}
        if icc_bytes:
            save_kwargs["icc_profile"] = icc_bytes
        composited.save(output_path, format="PNG", **save_kwargs)
    elif fmt == "tiff8":
        save_kwargs = {"dpi": (settings.dpi, settings.dpi), "compression": "tiff_deflate"}
        if icc_bytes:
            save_kwargs["icc_profile"] = icc_bytes
        composited.save(output_path, format="TIFF", **save_kwargs)
    elif fmt == "tiff16":
        import tifffile

        arr16 = (np.asarray(composited, dtype=np.float32) / 255.0 * 65535.0 + 0.5).astype(np.uint16)
        tifffile.imwrite(
            output_path,
            arr16,
            resolution=(settings.dpi, settings.dpi),
            photometric="rgb",
        )
    else:
        raise ValueError(f"Unsupported export format: {settings.format}")

    progress("Done.")
    return output_path
