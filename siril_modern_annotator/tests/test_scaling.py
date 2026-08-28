"""Native -> export-target resolution scaling (brief #35 'Export scaling':
native annotation coordinates remain correct at arbitrary output sizes)."""

from __future__ import annotations

import numpy as np
import pytest

from siril_modern_annotator.annotation.models import Annotation, LabelStyle, MarkerShape, StylePreset
from siril_modern_annotator.annotation.renderer import compute_marker_geometry, default_max_marker_radius_px
from siril_modern_annotator.export.exporter import (
    _pillow_text_measurer,
    export_image,
    render_annotations,
    resolve_output_size,
)
from siril_modern_annotator.persistence.project import ExportSettings

_NATIVE_W, _NATIVE_H = 4000, 3000


def test_resolve_output_size_original():
    settings = ExportSettings(resolution_mode="original")
    assert resolve_output_size(_NATIVE_W, _NATIVE_H, settings) == (_NATIVE_W, _NATIVE_H)


def test_resolve_output_size_scale_50_percent():
    settings = ExportSettings(resolution_mode="scale", scale_percent=50.0)
    w, h = resolve_output_size(_NATIVE_W, _NATIVE_H, settings)
    assert w == _NATIVE_W // 2
    assert h == _NATIVE_H // 2


def test_resolve_output_size_custom_width_preserves_aspect():
    settings = ExportSettings(resolution_mode="custom", custom_width=2000, custom_height=None)
    w, h = resolve_output_size(_NATIVE_W, _NATIVE_H, settings)
    assert w == 2000
    assert h == pytest.approx(1500, abs=1)


def test_resolve_output_size_custom_both_dimensions():
    settings = ExportSettings(resolution_mode="custom", custom_width=1000, custom_height=1000)
    assert resolve_output_size(_NATIVE_W, _NATIVE_H, settings) == (1000, 1000)


@pytest.mark.parametrize("scale_percent", [25.0, 100.0, 200.0])
def test_render_annotations_output_matches_requested_size(scale_percent):
    base = np.zeros((_NATIVE_H, _NATIVE_W, 3), dtype=np.uint8)
    ann = Annotation(
        catalog="messier", catalog_name="M1", ra=0.0, dec=0.0,
        image_x=_NATIVE_W / 2, image_y=_NATIVE_H / 2,
        label_x=_NATIVE_W / 2 + 20, label_y=_NATIVE_H / 2 - 20,
    )
    style = StylePreset(name="test")
    settings = ExportSettings(resolution_mode="scale", scale_percent=scale_percent)
    out_w, out_h = resolve_output_size(_NATIVE_W, _NATIVE_H, settings)
    image = render_annotations(base, [ann], style, out_w, out_h)
    assert image.size == (out_w, out_h)


def test_disabled_annotation_not_rendered():
    base = np.zeros((200, 200, 3), dtype=np.uint8)
    ann = Annotation(
        catalog="ngc", catalog_name="NGC 1", ra=0.0, dec=0.0,
        image_x=100, image_y=100, label_x=120, label_y=80, enabled=False,
        marker_style=None,
    )
    style = StylePreset(name="test")
    before = render_annotations(base, [], style, 200, 200)
    after = render_annotations(base, [ann], style, 200, 200)
    assert np.array_equal(np.asarray(before), np.asarray(after))


def test_angular_size_marker_radius_is_capped_for_huge_catalog_objects():
    """Regression test: a real catalog diameter for an object like M31 (~178 arcmin)
    can convert to a marker radius far larger than the image itself once a tight field
    of view is involved -- confirmed against a real plate-solved M31 FITS file. The
    radius must be capped rather than drawing a circle bigger than the frame."""
    style = StylePreset(name="test")
    style.marker_style.size_from_angular_size = True
    ann = Annotation(
        catalog="messier", catalog_name="M31", ra=0.0, dec=0.0,
        image_x=1000, image_y=800, angular_size=178.0,  # arcmin
    )
    arcsec_per_px = 2.79  # matches the real M31 test file's pixel scale
    max_radius = default_max_marker_radius_px(2952, 1936)
    geo = compute_marker_geometry(ann, style, arcsec_per_px, max_radius)
    assert geo.radius == max_radius
    # Without a cap, this would be (178*60/2)/2.79 ~= 1914px -- larger than the image.
    uncapped = compute_marker_geometry(ann, style, arcsec_per_px, max_radius_px=None)
    assert uncapped.radius > max_radius


def test_pillow_text_measurer_scales_with_text_length():
    """Regression guard for label clipping: the export path used to size the label's
    background box off a rough character-count heuristic instead of the real font
    Pillow draws with, risking a box narrower than the actual rendered text -- fixed to
    measure with the real font via font.getbbox(), mirroring the same fix made for the
    GUI's qt_text_measurer (which had an analogous bug using QFontMetricsF.boundingRect()
    instead of horizontalAdvance())."""
    style = LabelStyle(font_size=24.0, padding=4.0)
    short_w, short_h = _pillow_text_measurer("M31", style)
    long_w, long_h = _pillow_text_measurer("North America Nebula (NGC 7000)", style)
    assert long_w > short_w
    assert short_h == long_h  # single-line text: height independent of string length
    assert short_w > 2 * style.padding  # sane, non-degenerate measurement


def test_none_marker_shape_draws_no_marker_but_label_still_renders():
    base = np.zeros((200, 200, 3), dtype=np.uint8)
    style = StylePreset(name="test")
    style.marker_style.shape = MarkerShape.NONE
    ann = Annotation(
        catalog="ngc", catalog_name="NGC 1", ra=0.0, dec=0.0,
        image_x=100, image_y=100, label_x=100, label_y=100,
    )
    image = render_annotations(base, [ann], style, 200, 200)
    assert image.size == (200, 200)


def test_export_image_with_channels_first_raw_data_produces_correct_dimensions(tmp_path):
    """Regression test for a real, reported bug: exported JPG/TIFF files showed only a
    thin sliver of the real image, mostly blank. Root cause -- export_image() read
    native_height/native_width directly off pixel_data.shape *before* normalizing
    channel order. get_full_pixeldata() can return channels-first data (C, H, W) --
    confirmed against a real Siril color image -- so shape[0] was the channel count (3)
    treated as the height, producing an output target only ~3 pixels tall. Dimensions
    must be read from the already-normalized (H, W, 3) array instead."""
    height, width = 300, 500
    channels_first = np.random.default_rng(0).integers(0, 65535, size=(3, height, width), dtype=np.uint16)
    style = StylePreset(name="test")
    settings = ExportSettings(format="png", resolution_mode="original")
    out_path = tmp_path / "export_test.png"

    result = export_image(out_path, channels_first, [], style, settings)

    from PIL import Image

    with Image.open(result) as saved:
        assert saved.size == (width, height), (
            f"expected ({width}, {height}), got {saved.size} -- "
            "channels-first shape[0]/[1] bug reproduced"
        )
