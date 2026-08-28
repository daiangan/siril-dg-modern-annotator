"""Custom display names/notes can span multiple lines (a "bigger tooltip"-style
description, per user request) -- every text measurer used across the app (the
Qt-free default in annotation/renderer.py and annotation/layout.py, and the Pillow-
based one in export/exporter.py) must size a multi-line label taller than a
single-line one, using the widest line's width, not just crash or mis-measure."""

from __future__ import annotations

import pytest

from siril_modern_annotator.annotation.layout import _default_text_measurer as layout_measurer
from siril_modern_annotator.annotation.models import LabelStyle
from siril_modern_annotator.annotation.renderer import default_text_measurer
from siril_modern_annotator.export.exporter import _pillow_text_measurer


def test_renderer_default_measurer_multiline_taller_than_single_line():
    style = LabelStyle(font_size=14.0, padding=4.0)
    w1, h1 = default_text_measurer("NGC 7000", style)
    w2, h2 = default_text_measurer("NGC 7000\nNorth America Nebula", style)
    assert h2 > h1
    assert w2 >= w1  # second line is longer, so max-line-width should grow too


def test_renderer_default_measurer_three_lines_roughly_triple_height_of_one():
    style = LabelStyle(font_size=14.0, padding=0.0)
    _, h1 = default_text_measurer("A", style)
    _, h3 = default_text_measurer("A\nB\nC", style)
    assert h3 == pytest.approx(h1 * 3, rel=0.05)


def test_layout_measurer_matches_renderer_measurer_shape():
    style = LabelStyle(font_size=14.0, padding=4.0)
    w1, h1 = layout_measurer("A", style)
    w2, h2 = layout_measurer("A\nB\nC", style)
    assert h2 > h1


def test_pillow_measurer_multiline_taller_and_uses_widest_line():
    style = LabelStyle(font_size=20.0, padding=4.0)
    w_short, h_short = _pillow_text_measurer("M31", style)
    w_multi, h_multi = _pillow_text_measurer("M31\nAndromeda Galaxy\nA faint smudge", style)
    assert h_multi > h_short
    w_longest_line_only, _ = _pillow_text_measurer("Andromeda Galaxy", style)
    assert w_multi == pytest.approx(w_longest_line_only, rel=0.05)


def test_pillow_measurer_handles_empty_lines_without_crashing():
    style = LabelStyle(font_size=16.0, padding=2.0)
    w, h = _pillow_text_measurer("M31\n\nSecond paragraph", style)
    assert w > 0 and h > 0


def test_render_annotations_end_to_end_with_multiline_custom_name():
    """Full pipeline, not just the measurer: a multi-line custom_display_name must
    render without crashing (draw.multiline_text, not draw.text -- Pillow's plain
    text() draws an embedded "\\n" as a literal glyph gap on one line, not a line
    break)."""
    import numpy as np

    from siril_modern_annotator.annotation.models import Annotation, StylePreset
    from siril_modern_annotator.export.exporter import render_annotations

    base = np.zeros((400, 400, 3), dtype=np.uint8)
    ann = Annotation(
        catalog="messier", catalog_name="M31", ra=0.0, dec=0.0,
        image_x=200, image_y=200, label_x=220, label_y=180,
        custom_display_name="Andromeda Galaxy\nOur nearest large neighbor\nvisible to the naked eye",
    )
    style = StylePreset(name="test")
    image = render_annotations(base, [ann], style, 400, 400)
    assert image.size == (400, 400)

