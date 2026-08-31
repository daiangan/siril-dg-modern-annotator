"""MainWindow._apply_overlay_style_fields -- the field-copy helper behind "Reset to
Default" restoring grid/compass style (line_width, label_font_size, etc.) to their
resolution-scaled defaults, per user report that a manually thinned or pre-scaling flat
line_width survived a style reset. `enabled` and the compass's drag position are
placement/visibility, not style, so callers preserve those explicitly rather than
resetting them -- this is a plain dataclass field copy with no Qt/QApplication
dependency, so it's tested directly without instantiating MainWindow."""

from __future__ import annotations

from siril_modern_annotator.annotation.models import CompassStyle, GridStyle
from siril_modern_annotator.gui.main_window import MainWindow


def test_grid_style_fields_are_copied_from_fresh_except_preserved_ones():
    target = GridStyle(enabled=True, color="#ff0000", opacity=0.9, line_width=0.3)
    fresh = GridStyle(enabled=False, color="#66AADD", opacity=0.6, line_width=2.0)
    MainWindow._apply_overlay_style_fields(target, fresh, preserve={"enabled"})
    assert target.enabled is True  # preserved, not copied from fresh
    assert (target.color, target.opacity, target.line_width) == (fresh.color, fresh.opacity, fresh.line_width)


def test_compass_style_preserves_enabled_and_drag_position():
    target = CompassStyle(enabled=True, line_width=0.4, anchor_x=123.0, anchor_y=456.0)
    fresh = CompassStyle(enabled=False, line_width=3.2, anchor_x=None, anchor_y=None)
    MainWindow._apply_overlay_style_fields(target, fresh, preserve={"enabled", "anchor_x", "anchor_y"})
    assert (target.enabled, target.anchor_x, target.anchor_y) == (True, 123.0, 456.0)
    assert target.line_width == fresh.line_width
