"""Technical-details info box overlay (camera/telescope/filter/etc.) -- image-level,
draggable, corner-anchored text box. Pure geometry, following the same convention as
every other function in renderer.py: no Qt/Pillow, consumed identically by both the
interactive canvas (gui/overlay_item.py) and the exporter (export/exporter.py)."""

from __future__ import annotations

from siril_modern_annotator.annotation.models import InfoBoxCorner, InfoBoxStyle
from siril_modern_annotator.annotation.renderer import compute_info_box_geometry

_WIDTH, _HEIGHT = 2000.0, 1500.0
_TEXT = "Camera: ZWO ASI2600MM\nTelescope: RC8\nFilter: Ha\nExposure: 300s"


def test_info_box_none_when_disabled():
    assert compute_info_box_geometry(_TEXT, InfoBoxStyle(enabled=False), _WIDTH, _HEIGHT) is None


def test_info_box_none_when_text_is_blank_or_whitespace_only():
    style = InfoBoxStyle(enabled=True)
    assert compute_info_box_geometry("", style, _WIDTH, _HEIGHT) is None
    assert compute_info_box_geometry("   \n  \n", style, _WIDTH, _HEIGHT) is None


def test_info_box_top_left_corner():
    style = InfoBoxStyle(enabled=True, corner=InfoBoxCorner.TOP_LEFT, margin=20.0)
    geo = compute_info_box_geometry(_TEXT, style, _WIDTH, _HEIGHT)
    assert geo is not None
    assert geo.bbox.x0 == 20.0
    assert geo.bbox.y0 == 20.0


def test_info_box_top_right_corner():
    style = InfoBoxStyle(enabled=True, corner=InfoBoxCorner.TOP_RIGHT, margin=20.0)
    geo = compute_info_box_geometry(_TEXT, style, _WIDTH, _HEIGHT)
    box_width = geo.bbox.x1 - geo.bbox.x0
    assert geo.bbox.x1 == 2000.0 - 20.0
    assert geo.bbox.x0 == 2000.0 - 20.0 - box_width
    assert geo.bbox.y0 == 20.0


def test_info_box_bottom_left_corner_is_the_default():
    style = InfoBoxStyle(enabled=True, margin=20.0)  # corner left at its default
    assert style.corner is InfoBoxCorner.BOTTOM_LEFT
    geo = compute_info_box_geometry(_TEXT, style, _WIDTH, _HEIGHT)
    box_height = geo.bbox.y1 - geo.bbox.y0
    assert geo.bbox.x0 == 20.0
    assert geo.bbox.y1 == 1500.0 - 20.0
    assert geo.bbox.y0 == 1500.0 - 20.0 - box_height


def test_info_box_bottom_right_corner():
    style = InfoBoxStyle(enabled=True, corner=InfoBoxCorner.BOTTOM_RIGHT, margin=20.0)
    geo = compute_info_box_geometry(_TEXT, style, _WIDTH, _HEIGHT)
    assert geo.bbox.x1 == 2000.0 - 20.0
    assert geo.bbox.y1 == 1500.0 - 20.0


def test_info_box_anchor_override_wins_over_corner():
    style = InfoBoxStyle(enabled=True, corner=InfoBoxCorner.TOP_RIGHT, anchor_x=500.0, anchor_y=600.0)
    geo = compute_info_box_geometry(_TEXT, style, _WIDTH, _HEIGHT)
    assert geo.bbox.x0 == 500.0
    assert geo.bbox.y0 == 600.0


def test_info_box_text_is_preserved_verbatim_minus_outer_newlines():
    style = InfoBoxStyle(enabled=True)
    geo = compute_info_box_geometry("\nCamera: Foo\nGain: 100\n", style, _WIDTH, _HEIGHT)
    assert geo.text == "Camera: Foo\nGain: 100"


def test_info_box_grows_with_more_lines_of_text():
    style = InfoBoxStyle(enabled=True)
    short_geo = compute_info_box_geometry("One line", style, _WIDTH, _HEIGHT)
    long_geo = compute_info_box_geometry("One line\nTwo lines\nThree lines\nFour lines", style, _WIDTH, _HEIGHT)
    short_h = short_geo.bbox.y1 - short_geo.bbox.y0
    long_h = long_geo.bbox.y1 - long_geo.bbox.y0
    assert long_h > short_h
