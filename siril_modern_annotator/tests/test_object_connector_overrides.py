"""Per-object connector style/color/width overrides (Selected Object tab, "Use global
style for this object" unchecked). Real gap this fixes: the Connector group in that
tab's editor was fully interactive (style/color/width controls all worked and looked
live) but silently did nothing -- Annotation had no fields to capture those values
into at all, so every edit was discarded the moment the user made it. marker_style/
label_style already worked as a full-struct per-object override; connector settings
needed their own fields since ConnectorStyle/color/width have no bundling struct on
StylePreset either."""

from __future__ import annotations

import json

from siril_modern_annotator.annotation.models import Annotation, ConnectorStyle, StylePreset
from siril_modern_annotator.annotation.renderer import resolve_connector_color
from siril_modern_annotator.persistence.project import annotation_from_dict, to_jsonable
from dataclasses import asdict


def _ann(catalog: str = "messier", **overrides) -> Annotation:
    return Annotation(
        catalog=catalog, catalog_name="Test", ra=0.0, dec=0.0,
        image_x=100.0, image_y=100.0, **overrides,
    )


def _style() -> StylePreset:
    return StylePreset(name="test", connector_style=ConnectorStyle.ELBOW, connector_color="#8a8a8a", connector_width=1.0)


def test_effective_connector_style_falls_back_to_global_when_unset():
    ann = _ann()
    style = _style()
    assert ann.effective_connector_style(style) is ConnectorStyle.ELBOW


def test_effective_connector_style_uses_per_object_override():
    ann = _ann(connector_style=ConnectorStyle.CURVED)
    style = _style()
    assert ann.effective_connector_style(style) is ConnectorStyle.CURVED


def test_effective_connector_width_falls_back_to_global_when_unset():
    ann = _ann()
    style = _style()
    assert ann.effective_connector_width(style) == 1.0


def test_effective_connector_width_uses_per_object_override():
    ann = _ann(connector_width=3.5)
    style = _style()
    assert ann.effective_connector_width(style) == 3.5


def test_effective_connector_width_zero_override_is_respected_not_treated_as_unset():
    """A falsy-but-real override (0.0) must not be confused with "no override" -- this
    is exactly why effective_connector_width checks `is not None`, not truthiness."""
    ann = _ann(connector_width=0.0)
    style = _style()
    assert ann.effective_connector_width(style) == 0.0


def test_resolve_connector_color_per_object_override_beats_catalog_color():
    ann = _ann(catalog="sh2", connector_color="#123456")
    style = _style()
    assert resolve_connector_color(ann, style, {"sh2": "#F2938C"}) == "#123456"


def test_resolve_connector_color_per_object_override_beats_global_default():
    ann = _ann(connector_color="#123456")
    style = _style()
    assert resolve_connector_color(ann, style, None) == "#123456"


def test_resolve_connector_color_falls_back_through_catalog_then_global_when_unset():
    ann = _ann(catalog="sh2")
    style = _style()
    assert resolve_connector_color(ann, style, {"sh2": "#F2938C"}) == "#F2938C"
    assert resolve_connector_color(ann, style, None) == style.connector_color


def test_connector_style_round_trips_through_project_json_as_real_enum():
    """Regression test: ConnectorStyle is a str-backed Enum, but
    annotation.renderer.compute_connector_points compares it with `is`, not `==`.
    ConnectorStyle.ELBOW == "elbow" is True, but ConnectorStyle.ELBOW is "elbow" is
    False -- so a project file round trip that left connector_style as a bare string
    would silently break every connector-style comparison instead of raising."""
    ann = _ann(connector_style=ConnectorStyle.CURVED)
    payload = json.loads(json.dumps(to_jsonable(asdict(ann))))
    loaded = annotation_from_dict(payload)
    assert loaded.connector_style is ConnectorStyle.CURVED
    assert type(loaded.connector_style) is ConnectorStyle


def test_connector_style_none_round_trips_as_none_not_a_string():
    ann = _ann()
    assert ann.connector_style is None
    payload = json.loads(json.dumps(to_jsonable(asdict(ann))))
    loaded = annotation_from_dict(payload)
    assert loaded.connector_style is None
