"""ExportDialog's `initial` parameter (brief: remember export settings across
sessions, per user request) seeds every widget from a previous ExportSettings instead
of always starting from the flat hardcoded defaults baked into __init__."""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication

from siril_modern_annotator.gui.export_dialog import ExportDialog
from siril_modern_annotator.persistence.project import ExportSettings

_app = QApplication.instance() or QApplication([])


def test_no_initial_uses_the_flat_hardcoded_defaults():
    dialog = ExportDialog(4000, 3000)
    settings = dialog.export_settings()
    assert settings.format == "jpeg"
    assert settings.resolution_mode == "original"
    assert settings.jpeg_quality == 92
    assert settings.dpi == 300


def test_initial_seeds_format_and_simple_fields():
    initial = ExportSettings(format="tiff16", resolution_mode="original", jpeg_quality=80, dpi=150)
    dialog = ExportDialog(4000, 3000, initial=initial)
    settings = dialog.export_settings()
    assert settings.format == "tiff16"
    assert settings.jpeg_quality == 80
    assert settings.dpi == 150


def test_initial_seeds_scale_mode_and_percent():
    initial = ExportSettings(resolution_mode="scale", scale_percent=150.0)
    dialog = ExportDialog(4000, 3000, initial=initial)
    assert dialog.stack.currentIndex() == 1  # scale page, per _RESOLUTION_MODES order
    settings = dialog.export_settings()
    assert settings.resolution_mode == "scale"
    assert settings.scale_percent == 150.0


def test_initial_seeds_custom_width_and_height():
    initial = ExportSettings(resolution_mode="custom", custom_width=1234, custom_height=987)
    dialog = ExportDialog(4000, 3000, initial=initial)
    assert dialog.stack.currentIndex() == 2  # custom page, per _RESOLUTION_MODES order
    settings = dialog.export_settings()
    assert settings.resolution_mode == "custom"
    assert settings.custom_width == 1234
    assert settings.custom_height == 987


def test_initial_with_no_custom_dimensions_falls_back_to_native_size():
    # resolution_mode="original" (the default) never sets custom_width/height -- the
    # width/height spinboxes must still fall back to this image's own native size
    # rather than being left at some stale value.
    initial = ExportSettings(resolution_mode="original")
    dialog = ExportDialog(4000, 3000, initial=initial)
    assert dialog.width_spin.value() == 4000
    assert dialog.height_spin.value() == 3000
