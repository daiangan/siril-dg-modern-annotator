"""Annotation layout / project file persistence (brief #27).

Saves *only* annotation/layout/style/export state to a `<image>.annotations.json`
sidecar — never pixel data. A schema_version field is included from day one so future
format changes can be migrated instead of breaking old files.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any

from ..annotation.models import (
    Annotation,
    BackgroundMode,
    CompassStyle,
    ConnectorStyle,
    ConstellationStyle,
    DecLabelPosition,
    GridStyle,
    InfoBoxCorner,
    InfoBoxStyle,
    LabelStyle,
    MarkerShape,
    MarkerStyle,
    NameDisplayMode,
    OverlaySettings,
    RaLabelPosition,
    StylePreset,
)

SCHEMA_VERSION = 1


@dataclass
class CatalogConfig:
    enabled_catalogs: set[str] = field(default_factory=set)
    magnitude_limit: float | None = None


@dataclass
class ExportSettings:
    format: str = "jpeg"
    resolution_mode: str = "original"  # original | scale | custom
    scale_percent: float = 100.0
    custom_width: int | None = None
    custom_height: int | None = None
    jpeg_quality: int = 92
    dpi: int = 300


@dataclass
class ProjectData:
    source_width: int
    source_height: int
    source_identifier: str
    catalog_config: CatalogConfig
    global_style: StylePreset
    annotations: list[Annotation]
    export_settings: ExportSettings
    schema_version: int = SCHEMA_VERSION
    # Grid/compass start off every fresh session (per user request) and are only ever
    # persisted per-image, in this same sidecar -- default_factory so an older saved
    # file (from before this field existed) still loads fine, per load()'s .get() below.
    overlay_settings: OverlaySettings = field(default_factory=OverlaySettings)


def to_jsonable(value: Any) -> Any:
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, set):
        return sorted(value)
    if isinstance(value, dict):
        return {k: to_jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [to_jsonable(v) for v in value]
    return value


def save(path: Path, project: ProjectData) -> None:
    payload = {
        "schema_version": project.schema_version,
        "source_width": project.source_width,
        "source_height": project.source_height,
        "source_identifier": project.source_identifier,
        "catalog_config": to_jsonable(asdict(project.catalog_config)),
        "global_style": to_jsonable(asdict(project.global_style)),
        "annotations": [to_jsonable(asdict(a)) for a in project.annotations],
        "export_settings": to_jsonable(asdict(project.export_settings)),
        "overlay_settings": to_jsonable(asdict(project.overlay_settings)),
    }
    Path(path).write_text(json.dumps(payload, indent=2), encoding="utf-8")


def marker_style_from_dict(d: dict) -> MarkerStyle:
    d = dict(d)
    d["shape"] = MarkerShape(d["shape"])
    return MarkerStyle(**d)


def label_style_from_dict(d: dict) -> LabelStyle:
    d = dict(d)
    d["background_mode"] = BackgroundMode(d["background_mode"])
    d["name_display"] = NameDisplayMode(d["name_display"])
    return LabelStyle(**d)


def style_preset_from_dict(d: dict) -> StylePreset:
    d = dict(d)
    d["marker_style"] = marker_style_from_dict(d["marker_style"])
    d["label_style"] = label_style_from_dict(d["label_style"])
    d["connector_style"] = ConnectorStyle(d["connector_style"])
    return StylePreset(**d)


def annotation_from_dict(d: dict) -> Annotation:
    d = dict(d)
    if d.get("marker_style") is not None:
        d["marker_style"] = marker_style_from_dict(d["marker_style"])
    if d.get("label_style") is not None:
        d["label_style"] = label_style_from_dict(d["label_style"])
    # ConnectorStyle is a str-backed Enum, but compute_connector_points compares it
    # with `is`, not `==` -- leaving a bare string loaded straight from JSON would
    # silently fail every one of those comparisons instead of raising, since
    # ConnectorStyle.ELBOW == "elbow" is True but `is` is not.
    if d.get("connector_style") is not None:
        d["connector_style"] = ConnectorStyle(d["connector_style"])
    return Annotation(**d)


def grid_style_from_dict(d: dict) -> GridStyle:
    d = dict(d)
    d["ra_label_position"] = RaLabelPosition(d["ra_label_position"])
    d["dec_label_position"] = DecLabelPosition(d["dec_label_position"])
    return GridStyle(**d)


def info_box_style_from_dict(d: dict) -> InfoBoxStyle:
    d = dict(d)
    d["corner"] = InfoBoxCorner(d["corner"])
    return InfoBoxStyle(**d)


def overlay_settings_from_dict(d: dict) -> OverlaySettings:
    return OverlaySettings(
        grid=grid_style_from_dict(d["grid"]),
        compass=CompassStyle(**d["compass"]),
        # A project file saved before the info box/constellations existed simply has no
        # key for either -- falls back to that style's own defaults (disabled), same
        # reasoning as ProjectData's overlay_settings default_factory below for
        # pre-overlay files.
        info_box=info_box_style_from_dict(d["info_box"]) if "info_box" in d else InfoBoxStyle(),
        constellations=ConstellationStyle(**d["constellations"]) if "constellations" in d else ConstellationStyle(),
    )


def load(path: Path) -> ProjectData:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    schema_version = payload.get("schema_version", 1)
    if schema_version > SCHEMA_VERSION:
        raise ValueError(
            f"Project file schema_version {schema_version} is newer than this version "
            f"of Siril Modern Annotator supports ({SCHEMA_VERSION})."
        )
    catalog_config = CatalogConfig(
        enabled_catalogs=set(payload["catalog_config"]["enabled_catalogs"]),
        magnitude_limit=payload["catalog_config"]["magnitude_limit"],
    )
    global_style = style_preset_from_dict(payload["global_style"])
    annotations = [annotation_from_dict(a) for a in payload["annotations"]]
    export_settings = ExportSettings(**payload["export_settings"])
    # A project file saved before overlay_settings existed simply has no key for it --
    # ProjectData's own default_factory=OverlaySettings (both start disabled) covers
    # that case rather than raising a KeyError.
    overlay_settings = (
        overlay_settings_from_dict(payload["overlay_settings"])
        if "overlay_settings" in payload
        else OverlaySettings()
    )
    return ProjectData(
        source_width=payload["source_width"],
        source_height=payload["source_height"],
        source_identifier=payload["source_identifier"],
        catalog_config=catalog_config,
        global_style=global_style,
        annotations=annotations,
        export_settings=export_settings,
        schema_version=schema_version,
        overlay_settings=overlay_settings,
    )


def project_path_for_image(image_path: Path) -> Path:
    image_path = Path(image_path)
    return image_path.with_suffix("").with_suffix(".annotations.json")
