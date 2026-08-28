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

from siril_modern_annotator.annotation.models import MarkerShape
from siril_modern_annotator.persistence import last_used
from siril_modern_annotator.persistence import presets as preset_store


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
