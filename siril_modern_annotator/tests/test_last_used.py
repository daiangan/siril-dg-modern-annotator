"""Last-used general settings (catalogs, global style, per-catalog colors) must round-
trip through QSettings and be distinct from both named user presets (presets.py) and
per-image project layout files (project.py) -- brief: restore the user's last-used
general configuration on a brand-new/different image instead of hardcoded defaults.

Preview stretch mode is deliberately NOT part of this: it's a one-off viewing choice
for comparing against Siril's own preview, not a general style/catalog preference --
confirmed by a real regression report where remembering it caused the image to reopen
in "Auto Stretch" after the user briefly switched to it for a side-by-side comparison.

Each test points QSettings at an isolated temp .ini file (via monkeypatching
last_used._settings) so runs never read or write the real user's settings store.
"""

from __future__ import annotations

from PyQt6.QtCore import QSettings

from siril_modern_annotator.annotation.models import (
    CompassStyle,
    DecLabelPosition,
    GridStyle,
    InfoBoxCorner,
    InfoBoxStyle,
    MarkerShape,
    OverlaySettings,
    RaLabelPosition,
)
from siril_modern_annotator.persistence import last_used
from siril_modern_annotator.persistence import presets as preset_store
from siril_modern_annotator.persistence.project import ExportSettings


def _use_temp_settings(monkeypatch, tmp_path):
    ini_path = str(tmp_path / "settings.ini")

    def _settings():
        return QSettings(ini_path, QSettings.Format.IniFormat)

    monkeypatch.setattr(last_used, "_settings", _settings)


def test_no_saved_style_returns_none(monkeypatch, tmp_path):
    _use_temp_settings(monkeypatch, tmp_path)
    assert last_used.load_last_used_style() is None


def test_style_round_trips(monkeypatch, tmp_path):
    _use_temp_settings(monkeypatch, tmp_path)
    style = preset_store.default_preset_for_image(4000, 3000)
    last_used.save_last_used_style(style)
    loaded = last_used.load_last_used_style()
    assert loaded is not None
    assert loaded.marker_style.shape == style.marker_style.shape
    assert loaded.marker_style.radius == style.marker_style.radius
    assert loaded.label_style.font_size == style.label_style.font_size


def test_corrupted_style_value_falls_back_to_none(monkeypatch, tmp_path):
    _use_temp_settings(monkeypatch, tmp_path)
    last_used._settings().setValue(last_used._STYLE_KEY, "not valid json")
    assert last_used.load_last_used_style() is None


def test_no_saved_catalogs_returns_none(monkeypatch, tmp_path):
    _use_temp_settings(monkeypatch, tmp_path)
    assert last_used.load_last_used_catalogs() is None


def test_catalogs_round_trip(monkeypatch, tmp_path):
    _use_temp_settings(monkeypatch, tmp_path)
    catalogs = {"messier", "ngc", "ldn"}
    last_used.save_last_used_catalogs(catalogs)
    assert last_used.load_last_used_catalogs() == catalogs


def test_empty_catalog_set_round_trips_as_empty_not_none(monkeypatch, tmp_path):
    # A user who deliberately unchecks every catalog should stay at zero catalogs next
    # time, not silently fall back to "no saved preference" -> all catalogs on.
    _use_temp_settings(monkeypatch, tmp_path)
    last_used.save_last_used_catalogs(set())
    assert last_used.load_last_used_catalogs() == set()


def test_style_survives_marker_shape_enum_round_trip(monkeypatch, tmp_path):
    _use_temp_settings(monkeypatch, tmp_path)
    style = preset_store.default_preset()
    from dataclasses import replace

    style = replace(style, marker_style=replace(style.marker_style, shape=MarkerShape.BRACKETS))
    last_used.save_last_used_style(style)
    loaded = last_used.load_last_used_style()
    assert loaded.marker_style.shape == MarkerShape.BRACKETS


def test_no_saved_catalog_colors_returns_none(monkeypatch, tmp_path):
    _use_temp_settings(monkeypatch, tmp_path)
    assert last_used.load_last_used_catalog_colors() is None


def test_catalog_colors_round_trip(monkeypatch, tmp_path):
    _use_temp_settings(monkeypatch, tmp_path)
    colors = {"messier": "#F2C572", "ngc": "#7FC8C4"}
    last_used.save_last_used_catalog_colors(colors)
    assert last_used.load_last_used_catalog_colors() == colors


def test_corrupted_catalog_colors_value_falls_back_to_none(monkeypatch, tmp_path):
    _use_temp_settings(monkeypatch, tmp_path)
    last_used._settings().setValue(last_used._CATALOG_COLORS_KEY, "not valid json")
    assert last_used.load_last_used_catalog_colors() is None


# ---------------------------------------------------------------- export settings ----


def test_no_saved_export_settings_returns_none(monkeypatch, tmp_path):
    _use_temp_settings(monkeypatch, tmp_path)
    assert last_used.load_last_used_export_settings() is None


def test_export_settings_round_trip(monkeypatch, tmp_path):
    _use_temp_settings(monkeypatch, tmp_path)
    settings = ExportSettings(
        format="tiff16", resolution_mode="custom", scale_percent=150.0,
        custom_width=4000, custom_height=3000, jpeg_quality=80, dpi=150,
    )
    last_used.save_last_used_export_settings(settings)
    loaded = last_used.load_last_used_export_settings()
    assert loaded == settings


def test_corrupted_export_settings_value_falls_back_to_none(monkeypatch, tmp_path):
    _use_temp_settings(monkeypatch, tmp_path)
    last_used._settings().setValue(last_used._EXPORT_KEY, "not valid json")
    assert last_used.load_last_used_export_settings() is None


# --------------------------------------------------------------- overlay settings ----


def _resolution_scaled_defaults() -> OverlaySettings:
    # Mirrors presets.default_overlay_settings_for_image's shape (distinct
    # size-dependent values from the flat dataclass defaults) without needing a real
    # image size -- what matters for these tests is that these fields survive
    # apply_last_used_overlay_settings untouched.
    return OverlaySettings(
        grid=GridStyle(label_font_size=27.0, line_width=3.0),
        compass=CompassStyle(label_font_size=27.0, line_width=3.0),
        info_box=InfoBoxStyle(font_size=27.0, padding=18.0, border_radius=11.0, margin=44.0, text="from this FITS header"),
    )


def test_apply_last_used_overlay_settings_with_nothing_saved_returns_defaults_unchanged(monkeypatch, tmp_path):
    _use_temp_settings(monkeypatch, tmp_path)
    defaults = _resolution_scaled_defaults()
    result = last_used.apply_last_used_overlay_settings(defaults)
    assert result is defaults
    assert result.grid.line_width == 3.0
    assert result.info_box.text == "from this FITS header"


def test_overlay_settings_round_trip_restores_size_independent_fields(monkeypatch, tmp_path):
    _use_temp_settings(monkeypatch, tmp_path)
    saved = OverlaySettings(
        grid=GridStyle(
            enabled=True, color="#123456", opacity=0.42, show_labels=False,
            ra_label_position=RaLabelPosition.BOTTOM, dec_label_position=DecLabelPosition.LEFT,
        ),
        compass=CompassStyle(enabled=True, color="#654321", arrow_length_fraction=0.11),
        info_box=InfoBoxStyle(
            enabled=True, corner=InfoBoxCorner.TOP_RIGHT, background_color="#abcdef",
            background_opacity=0.77, text_color="#fedcba",
        ),
    )
    last_used.save_last_used_overlay_settings(saved)

    defaults = _resolution_scaled_defaults()
    result = last_used.apply_last_used_overlay_settings(defaults)

    assert result.grid.enabled is True
    assert result.grid.color == "#123456"
    assert result.grid.opacity == 0.42
    assert result.grid.show_labels is False
    assert result.grid.ra_label_position is RaLabelPosition.BOTTOM
    assert result.grid.dec_label_position is DecLabelPosition.LEFT
    assert result.compass.enabled is True
    assert result.compass.color == "#654321"
    assert result.compass.arrow_length_fraction == 0.11
    assert result.info_box.enabled is True
    assert result.info_box.corner is InfoBoxCorner.TOP_RIGHT
    assert result.info_box.background_color == "#abcdef"
    assert result.info_box.background_opacity == 0.77
    assert result.info_box.text_color == "#fedcba"


def test_overlay_settings_apply_never_touches_size_dependent_or_per_image_fields(monkeypatch, tmp_path):
    """Regression guard for the actual design constraint here: line_width/
    label_font_size/padding/border_radius/margin are resolution-scaled fresh per image
    (presets.default_overlay_settings_for_image) and info_box.text/anchor_x/anchor_y
    are per-image data -- restoring any of these from a previous, possibly
    different-sized image would be wrong, not "consistent"."""
    _use_temp_settings(monkeypatch, tmp_path)
    saved = OverlaySettings(
        grid=GridStyle(enabled=True, color="#111111"),
        compass=CompassStyle(enabled=True, color="#222222"),
        info_box=InfoBoxStyle(enabled=True, background_color="#333333"),
    )
    last_used.save_last_used_overlay_settings(saved)

    defaults = _resolution_scaled_defaults()
    defaults.compass.anchor_x, defaults.compass.anchor_y = 123.0, 456.0
    defaults.info_box.anchor_x, defaults.info_box.anchor_y = 78.0, 90.0
    result = last_used.apply_last_used_overlay_settings(defaults)

    assert result.grid.line_width == 3.0
    assert result.grid.label_font_size == 27.0
    assert result.compass.line_width == 3.0
    assert result.compass.label_font_size == 27.0
    assert result.info_box.font_size == 27.0
    assert result.info_box.padding == 18.0
    assert result.info_box.border_radius == 11.0
    assert result.info_box.margin == 44.0
    assert result.info_box.text == "from this FITS header"
    assert (result.compass.anchor_x, result.compass.anchor_y) == (123.0, 456.0)
    assert (result.info_box.anchor_x, result.info_box.anchor_y) == (78.0, 90.0)


def test_corrupted_overlay_settings_value_falls_back_to_defaults(monkeypatch, tmp_path):
    _use_temp_settings(monkeypatch, tmp_path)
    last_used._settings().setValue(last_used._OVERLAY_KEY, "not valid json")
    defaults = _resolution_scaled_defaults()
    result = last_used.apply_last_used_overlay_settings(defaults)
    assert result is defaults
    assert result.grid.enabled is False  # untouched flat dataclass default
