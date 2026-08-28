"""Saving and reopening a project file preserves positions/styles (brief #35)."""

from __future__ import annotations

from pathlib import Path

from siril_modern_annotator.annotation.models import (
    Annotation,
    BackgroundMode,
    ConnectorStyle,
    LabelStyle,
    MarkerShape,
    MarkerStyle,
    NameDisplayMode,
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
            marker_style=MarkerStyle(shape=MarkerShape.BRACKETS, color="#ff0000"),
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
    assert m31.marker_style.shape == MarkerShape.BRACKETS
    assert m31.marker_style.color == "#ff0000"
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


def test_project_path_for_image():
    path = project_path_for_image(Path("/tmp/M31_session1.fits"))
    assert path.name == "M31_session1.annotations.json"


def test_schema_version_written(tmp_path: Path):
    project = _sample_project()
    path = tmp_path / "test.annotations.json"
    save(path, project)
    text = path.read_text(encoding="utf-8")
    assert '"schema_version": 1' in text
