"""default_preset_for_image scales marker *radius* by resolution, since marker geometry
is defined in native pixel space (ARCHITECTURE.md #4) -- a flat radius looked fine at
one resolution and was nearly invisible against a real ~3000px-wide Siril image. Stroke
width and font size are fixed, resolution-independent legibility defaults instead (real-
world testing showed scaling them by resolution compounded into absurd sizes on large
images)."""

from __future__ import annotations

from dataclasses import replace

from PyQt6.QtCore import QSettings

from siril_modern_annotator.persistence import presets as preset_store


def _use_temp_settings(monkeypatch, tmp_path):
    """User-created presets are QSettings-backed (same store as last_used.py); point
    them at an isolated temp .ini per test so runs never read or write the real user's
    settings store."""
    ini_path = str(tmp_path / "settings.ini")
    monkeypatch.setattr(preset_store, "_settings", lambda: QSettings(ini_path, QSettings.Format.IniFormat))


def test_reference_resolution_still_scales_radius_up_from_flat_default():
    base = preset_store.default_preset()
    scaled = preset_store.default_preset_for_image(2000, 1500)
    assert scaled.marker_style.radius == base.marker_style.radius


def test_larger_image_scales_radius_proportionally():
    base = preset_store.default_preset()
    scaled = preset_store.default_preset_for_image(4000, 2600)  # long edge 2x reference
    assert scaled.marker_style.radius == base.marker_style.radius * 2


def test_smaller_image_never_shrinks_radius_below_flat_default():
    base = preset_store.default_preset()
    scaled = preset_store.default_preset_for_image(400, 300)
    assert scaled.marker_style.radius == base.marker_style.radius


def test_stroke_width_and_font_size_are_fixed_regardless_of_resolution():
    small = preset_store.default_preset_for_image(800, 600)
    large = preset_store.default_preset_for_image(12000, 8000)
    for style in (small, large):
        assert style.marker_style.stroke_width == 6.0
        assert style.connector_width == 6.0
        assert style.label_style.font_size == 60.0


def test_angular_size_scaling_enabled_by_default_for_new_images():
    base = preset_store.default_preset()
    assert base.marker_style.size_from_angular_size is False
    scaled = preset_store.default_preset_for_image(3000, 2000)
    assert scaled.marker_style.size_from_angular_size is True


def test_save_user_preset_round_trips(monkeypatch, tmp_path):
    _use_temp_settings(monkeypatch, tmp_path)
    preset = replace(preset_store.default_preset(), name="My Preset")
    preset_store.save_user_preset(preset)
    loaded = preset_store.load_user_presets()
    assert set(loaded.keys()) == {"My Preset"}
    assert loaded["My Preset"].marker_style.radius == preset.marker_style.radius


def test_delete_user_preset_removes_it(monkeypatch, tmp_path):
    _use_temp_settings(monkeypatch, tmp_path)
    preset_store.save_user_preset(replace(preset_store.default_preset(), name="Temp"))
    assert "Temp" in preset_store.load_user_presets()
    preset_store.delete_user_preset("Temp")
    assert "Temp" not in preset_store.load_user_presets()


def test_delete_user_preset_on_unknown_name_is_a_no_op(monkeypatch, tmp_path):
    _use_temp_settings(monkeypatch, tmp_path)
    preset_store.delete_user_preset("Never Existed")  # must not raise
    assert preset_store.load_user_presets() == {}


def test_all_presets_merges_builtin_and_user_but_builtin_is_never_deletable_via_store(monkeypatch, tmp_path):
    """delete_user_preset only ever touches the user-preset store -- Minimal Modern
    lives in BUILTIN_PRESETS, a plain module-level dict, so there's no persistence path
    that could remove it even if something passed its name in by mistake."""
    _use_temp_settings(monkeypatch, tmp_path)
    assert "Minimal Modern" not in preset_store.load_user_presets()
    preset_store.delete_user_preset("Minimal Modern")  # must not raise or affect BUILTIN_PRESETS
    assert "Minimal Modern" in preset_store.BUILTIN_PRESETS
    assert "Minimal Modern" in preset_store.all_presets()


def test_grid_line_width_at_reference_dimension_equals_the_floor():
    # 1250 = _GRID_LINE_WIDTH_MIN_PX / _OVERLAY_LINE_WIDTH_FRACTION -- the breakeven
    # point below which the scaled value would undercut the floor.
    overlay = preset_store.default_overlay_settings_for_image(1250, 900)
    assert overlay.grid.line_width == 1.0


def test_larger_image_scales_grid_line_width_proportionally():
    overlay = preset_store.default_overlay_settings_for_image(3000, 2500)  # short edge = 2x the breakeven
    assert overlay.grid.line_width == 2.0


def test_smaller_image_never_shrinks_grid_line_width_below_the_floor():
    overlay = preset_store.default_overlay_settings_for_image(400, 300)
    assert overlay.grid.line_width == 1.0


def test_constellation_line_width_matches_the_grid_s_scaling():
    # Constellation lines deliberately reuse the grid's own fraction/floor (see
    # default_overlay_settings_for_image's comment) so both read as the same subtle
    # visual weight rather than needing a third tuned constant.
    overlay = preset_store.default_overlay_settings_for_image(3000, 2500)
    assert overlay.constellations.line_width == overlay.grid.line_width


def test_constellation_label_size_matches_the_shared_overlay_label_size():
    overlay = preset_store.default_overlay_settings_for_image(3000, 2500)
    assert overlay.constellations.label_font_size == overlay.grid.label_font_size == overlay.compass.label_font_size


def test_compass_line_width_at_reference_dimension_equals_the_floor():
    # 2000 = _COMPASS_LINE_WIDTH_MIN_PX / _OVERLAY_LINE_WIDTH_FRACTION, which happens to
    # match _REFERENCE_LONG_EDGE_PX above -- coincidence of the chosen constants, not a
    # dependency between the two.
    overlay = preset_store.default_overlay_settings_for_image(2000, 1600)
    assert overlay.compass.line_width == 1.6


def test_larger_image_scales_compass_line_width_proportionally():
    overlay = preset_store.default_overlay_settings_for_image(4000, 5000)  # short edge = 2x the breakeven
    assert overlay.compass.line_width == 3.2


def test_smaller_image_never_shrinks_compass_line_width_below_the_floor():
    overlay = preset_store.default_overlay_settings_for_image(400, 300)
    assert overlay.compass.line_width == 1.6


def test_only_minimal_modern_ships_as_a_builtin_preset():
    """Regression test: the app used to ship five built-in presets (Scientific,
    Outreach, Social Media, Print in addition to Minimal Modern), but switching to one
    with a much smaller flat radius/font-size (Scientific) left a user with tiny,
    hard-to-read markers and no quick way back -- re-selecting Minimal Modern from the
    same combo just reapplies its flat values, not the resolution-scaled default.
    Per user request, only the default ships built-in now; users can still create and
    save their own via the Style panel's "Save As…" button (save_user_preset)."""
    assert set(preset_store.BUILTIN_PRESETS.keys()) == {"Minimal Modern"}
    assert preset_store.DEFAULT_PRESET_NAME == "Minimal Modern"
