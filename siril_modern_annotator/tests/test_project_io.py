"""Saving and reopening a project file preserves positions/styles (brief #35)."""

from __future__ import annotations

import json
from pathlib import Path

from siril_modern_annotator.annotation.models import (
    Annotation,
    BackgroundMode,
    CompassStyle,
    ConnectorStyle,
    ConstellationStyle,
    GridStyle,
    InfoBoxCorner,
    InfoBoxStyle,
    LabelStyle,
    MarkerShape,
    MarkerStyle,
    NameDisplayMode,
    OverlaySettings,
    StylePreset,
)
from siril_modern_annotator.persistence.project import (
    CatalogConfig,
    ExportSettings,
    ProjectData,
    load,
    project_path_for_image,
    save,
)


def _sample_project() -> ProjectData:
    annotations = [
        Annotation(
            catalog="messier", catalog_name="M31", common_name="Andromeda Galaxy",
            ra=10.68, dec=41.27, image_x=1200.5, image_y=800.25,
            object_type="galaxy", magnitude=3.4, angular_size=178.0,
            marker_x=1210.0, marker_y=805.0,
            label_x=1250.0, label_y=770.0, manually_positioned=True,
            marker_style=MarkerStyle(
                shape=MarkerShape.ELLIPSE, color="#ff0000",
                radius_x=45.0, radius_y=18.0, rotation_deg=32.0,
            ),
            label_style=LabelStyle(background_mode=BackgroundMode.SOLID, name_display=NameDisplayMode.COMMON_ONLY),
            priority=5, locked=True,
        ),
        Annotation(
            catalog="ngc", catalog_name="NGC 7000", ra=314.75, dec=44.34,
            image_x=500.0, image_y=300.0, enabled=False,
        ),
    ]
    style = StylePreset(
        name="Custom",
        marker_style=MarkerStyle(radius=22.0),
        label_style=LabelStyle(font_size=14.0),
        connector_style=ConnectorStyle.CURVED,
        connector_color="#00ff00",
        connector_width=2.5,
    )
    return ProjectData(
        source_width=6248,
        source_height=4176,
        source_identifier="NGC7000_LRGB",
        catalog_config=CatalogConfig(enabled_catalogs={"messier", "ngc"}, magnitude_limit=14.0),
        global_style=style,
        annotations=annotations,
        export_settings=ExportSettings(format="jpeg", jpeg_quality=85, resolution_mode="scale", scale_percent=50.0),
        overlay_settings=OverlaySettings(
            grid=GridStyle(enabled=True, color="#ff8800", opacity=0.4, line_width=2.0),
            compass=CompassStyle(enabled=True, color="#00ffaa", anchor_x=1200.0, anchor_y=800.0),
            info_box=InfoBoxStyle(
                enabled=True, text="Camera: Foo\nGain: 100", corner=InfoBoxCorner.TOP_RIGHT,
                background_color="#111111", anchor_x=300.0, anchor_y=400.0,
            ),
            constellations=ConstellationStyle(enabled=True, color="#bbccdd", show_labels=False),
        ),
    )


def test_round_trip_preserves_annotation_fields(tmp_path: Path):
    project = _sample_project()
    path = tmp_path / "test.annotations.json"
    save(path, project)
    loaded = load(path)

    assert loaded.source_width == project.source_width
    assert loaded.source_height == project.source_height
    assert loaded.source_identifier == project.source_identifier
    assert loaded.catalog_config.enabled_catalogs == project.catalog_config.enabled_catalogs
    assert loaded.catalog_config.magnitude_limit == project.catalog_config.magnitude_limit

    assert len(loaded.annotations) == 2
    m31 = loaded.annotations[0]
    assert m31.catalog_name == "M31"
    assert m31.common_name == "Andromeda Galaxy"
    assert m31.image_x == 1200.5
    assert m31.marker_x == 1210.0
    assert m31.marker_y == 805.0
    assert m31.label_x == 1250.0
    assert m31.manually_positioned is True
    assert m31.locked is True
    assert m31.priority == 5
    assert m31.marker_style.shape == MarkerShape.ELLIPSE
    assert m31.marker_style.color == "#ff0000"
    assert m31.marker_style.radius_x == 45.0
    assert m31.marker_style.radius_y == 18.0
    assert m31.marker_style.rotation_deg == 32.0
    assert m31.label_style.background_mode == BackgroundMode.SOLID
    assert m31.label_style.name_display == NameDisplayMode.COMMON_ONLY

    ngc = loaded.annotations[1]
    assert ngc.enabled is False
    assert ngc.marker_style is None
    assert ngc.marker_x is None
    assert ngc.marker_y is None

    assert loaded.global_style.name == "Custom"
    assert loaded.global_style.marker_style.radius == 22.0
    assert loaded.global_style.connector_style == ConnectorStyle.CURVED
    assert loaded.global_style.connector_color == "#00ff00"

    assert loaded.export_settings.format == "jpeg"
    assert loaded.export_settings.jpeg_quality == 85
    assert loaded.export_settings.scale_percent == 50.0

    assert loaded.overlay_settings.grid.enabled is True
    assert loaded.overlay_settings.grid.color == "#ff8800"
    assert loaded.overlay_settings.grid.opacity == 0.4
    assert loaded.overlay_settings.grid.line_width == 2.0
    assert loaded.overlay_settings.compass.enabled is True
    assert loaded.overlay_settings.compass.color == "#00ffaa"
    assert loaded.overlay_settings.compass.anchor_x == 1200.0
    assert loaded.overlay_settings.compass.anchor_y == 800.0
    assert loaded.overlay_settings.info_box.enabled is True
    assert loaded.overlay_settings.info_box.text == "Camera: Foo\nGain: 100"
    assert loaded.overlay_settings.info_box.corner == InfoBoxCorner.TOP_RIGHT
    assert loaded.overlay_settings.info_box.background_color == "#111111"
    assert loaded.overlay_settings.info_box.anchor_x == 300.0
    assert loaded.overlay_settings.info_box.anchor_y == 400.0
    assert loaded.overlay_settings.constellations.enabled is True
    assert loaded.overlay_settings.constellations.color == "#bbccdd"
    assert loaded.overlay_settings.constellations.show_labels is False


def test_load_project_saved_before_overlay_settings_existed(tmp_path: Path):
    """A project file saved by an older version of this app has no "overlay_settings"
    key at all -- must fall back to a disabled default rather than raising KeyError."""
    project = _sample_project()
    path = tmp_path / "test.annotations.json"
    save(path, project)
    payload = json.loads(path.read_text(encoding="utf-8"))
    del payload["overlay_settings"]
    path.write_text(json.dumps(payload), encoding="utf-8")

    loaded = load(path)
    assert loaded.overlay_settings.grid.enabled is False
    assert loaded.overlay_settings.compass.enabled is False
    assert loaded.overlay_settings.info_box.enabled is False
    assert loaded.overlay_settings.constellations.enabled is False


def test_load_project_saved_before_info_box_existed(tmp_path: Path):
    """A project file saved after grid/compass shipped but before the info box did
    has "overlay_settings" with a "grid"/"compass" but no "info_box" key -- must fall
    back to a disabled default there too, not raise KeyError."""
    project = _sample_project()
    path = tmp_path / "test.annotations.json"
    save(path, project)
    payload = json.loads(path.read_text(encoding="utf-8"))
    del payload["overlay_settings"]["info_box"]
    path.write_text(json.dumps(payload), encoding="utf-8")

    loaded = load(path)
    assert loaded.overlay_settings.grid.enabled is True  # unaffected
    assert loaded.overlay_settings.info_box.enabled is False


def test_load_project_saved_before_constellations_existed(tmp_path: Path):
    """Same backward-compatibility guarantee as info_box above, for a project file
    saved after info_box shipped but before constellation lines did."""
    project = _sample_project()
    path = tmp_path / "test.annotations.json"
    save(path, project)
    payload = json.loads(path.read_text(encoding="utf-8"))
    del payload["overlay_settings"]["constellations"]
    path.write_text(json.dumps(payload), encoding="utf-8")

    loaded = load(path)
    assert loaded.overlay_settings.grid.enabled is True  # unaffected
    assert loaded.overlay_settings.constellations.enabled is False


def test_project_path_for_image():
    path = project_path_for_image(Path("/tmp/M31_session1.fits"))
    assert path.name == "M31_session1.annotations.json"


def test_schema_version_written(tmp_path: Path):
    project = _sample_project()
    path = tmp_path / "test.annotations.json"
    save(path, project)
    text = path.read_text(encoding="utf-8")
    assert '"schema_version": 1' in text
