"""Per-catalog marker/connector colors (brief: "each catalog should have its own
color... modify the Marker and Connector"). compute_marker_geometry/resolve_connector_
color are the single resolution point both the interactive Qt canvas (annotation_item.py)
and the Pillow exporter (export/exporter.py) call, so a test here covers both."""

from __future__ import annotations

from dataclasses import replace

from siril_modern_annotator.annotation.catalogs import (
    DEFAULT_CATALOG_COLORS,
    ONLINE_ONLY_CATALOGS,
    SUPPORTED_CATALOGS,
)
from siril_modern_annotator.annotation.models import Annotation, LabelStyle, MarkerStyle, NameDisplayMode, StylePreset
from siril_modern_annotator.annotation.renderer import (
    compute_label_geometry,
    compute_marker_geometry,
    resolve_connector_color,
    resolve_marker_color,
)
from siril_modern_annotator.persistence import presets as preset_store


def _ann(catalog: str, marker_style=None, label_style=None) -> Annotation:
    return Annotation(
        catalog=catalog, catalog_name="Test", ra=0.0, dec=0.0,
        image_x=100.0, image_y=100.0, marker_style=marker_style, label_style=label_style,
    )


def test_default_catalog_colors_cover_every_supported_catalog():
    # DEFAULT_CATALOG_COLORS is a superset, not an exact match, of SUPPORTED_CATALOGS:
    # every *queryable* catalog needs a color (that's what this test guards), but
    # DEFAULT_CATALOG_COLORS also carries "user" -- the color a manually-placed custom
    # object renders with -- which is deliberately absent from SUPPORTED_CATALOGS
    # (that dict drives the Catalogs toolbar's fetch-toggle menu; "user" objects are
    # never fetched, so a checkbox for it would do nothing).
    assert set(SUPPORTED_CATALOGS.keys()) <= set(DEFAULT_CATALOG_COLORS.keys())
    assert set(DEFAULT_CATALOG_COLORS.keys()) - set(SUPPORTED_CATALOGS.keys()) == {"user"}


def test_online_only_catalogs_is_derived_not_stale():
    """Regression test: a catalog with a VizieR ID but no bundled Siril CSV has no
    offline fallback at all (unlike messier/ngc/ic/sh2/bright_star, which still work
    offline via their local file even when VizieR is unreachable) -- main_window.py
    uses this set to keep such catalogs off by default and to show a "needs an
    internet connection" status message instead of a misleading "0 objects" one.
    Barnard, LBN, RCW, vdB, Arp, Hickson, SNR, Abell, and WR are the only ones right
    now (Gum is VizieR-only in name but not in mechanism -- GumProvider reads bundled
    Python data, so it's deliberately absent here); this must stay correct as more
    VizieR-only catalogs are added, without anyone having to remember to update a
    hand-maintained list."""
    assert ONLINE_ONLY_CATALOGS == {
        "barnard", "lbn", "rcw", "vdb", "arp", "hickson", "snr", "abell", "wr",
    }
    assert ONLINE_ONLY_CATALOGS <= set(SUPPORTED_CATALOGS)


def test_default_catalog_colors_are_valid_distinct_hex_and_pastel():
    """Pastel per user request: none of the shipped defaults should be a fully-
    saturated primary (e.g. pure #ff0000), and every value must be a well-formed
    6-digit hex color distinct from every other catalog's."""
    colors = list(DEFAULT_CATALOG_COLORS.values())
    assert len(colors) == len(set(colors)), "catalog default colors must be distinct"
    for hex_color in colors:
        assert hex_color.startswith("#") and len(hex_color) == 7
        r, g, b = int(hex_color[1:3], 16), int(hex_color[3:5], 16), int(hex_color[5:7], 16)
        # A pastel tone stays reasonably bright and never lets one channel crash to 0
        # while another sits at 255 (that combination is a saturated primary, not a
        # soft/pastel hue).
        assert min(r, g, b) > 90, f"{hex_color} is too dark/saturated to read as pastel"


def test_catalog_color_applies_to_marker_when_no_per_object_override():
    ann = _ann("messier")
    style = StylePreset(name="test")
    catalog_colors = {"messier": "#F2C572"}
    geo = compute_marker_geometry(ann, style, catalog_colors=catalog_colors)
    assert geo.style.color == "#F2C572"


def test_catalog_color_is_ignored_when_object_has_its_own_marker_override():
    """A per-object style override is a more specific, deliberate user choice than a
    catalog-wide default and must always win."""
    override = MarkerStyle(color="#123456")
    ann = _ann("messier", marker_style=override)
    style = StylePreset(name="test")
    catalog_colors = {"messier": "#F2C572"}
    geo = compute_marker_geometry(ann, style, catalog_colors=catalog_colors)
    assert geo.style.color == "#123456"


def test_no_catalog_color_falls_back_to_global_marker_color():
    ann = _ann("ngc")
    style = StylePreset(name="test")
    geo = compute_marker_geometry(ann, style, catalog_colors={"messier": "#F2C572"})
    assert geo.style.color == style.marker_style.color


def test_resolve_connector_color_uses_catalog_color_when_present():
    ann = _ann("sh2")
    style = StylePreset(name="test", connector_color="#8a8a8a")
    assert resolve_connector_color(ann, style, {"sh2": "#F2938C"}) == "#F2938C"


def test_resolve_connector_color_falls_back_to_global_when_catalog_has_none():
    ann = _ann("ic")
    style = StylePreset(name="test", connector_color="#8a8a8a")
    assert resolve_connector_color(ann, style, {"sh2": "#F2938C"}) == "#8a8a8a"


def test_resolve_connector_color_with_no_catalog_colors_at_all():
    ann = _ann("ic")
    style = StylePreset(name="test", connector_color="#8a8a8a")
    assert resolve_connector_color(ann, style, None) == "#8a8a8a"


def test_resolve_marker_color_uses_catalog_color_when_no_override():
    """This is what the Style panel's "Selected Object" editor calls to decide what
    color to display/commit for a not-yet-overridden object -- must match what
    actually renders on screen (compute_marker_geometry), not the flat global default,
    per a real report that unchecking "Use global style" silently changed the color."""
    ann = _ann("sh2")
    style = StylePreset(name="test")
    assert resolve_marker_color(ann, style, {"sh2": "#F2938C"}) == "#F2938C"


def test_resolve_marker_color_per_object_override_beats_catalog_color():
    override = MarkerStyle(color="#123456")
    ann = _ann("sh2", marker_style=override)
    style = StylePreset(name="test")
    assert resolve_marker_color(ann, style, {"sh2": "#F2938C"}) == "#123456"


def test_resolve_marker_color_falls_back_to_global_with_no_catalog_color():
    ann = _ann("ngc")
    style = StylePreset(name="test")
    assert resolve_marker_color(ann, style, None) == style.marker_style.color


def test_resolve_marker_color_matches_compute_marker_geometry():
    """The editor-population helper and the actual renderer must never disagree."""
    ann = _ann("messier")
    style = StylePreset(name="test")
    catalog_colors = {"messier": "#F2C572"}
    assert resolve_marker_color(ann, style, catalog_colors) == compute_marker_geometry(
        ann, style, catalog_colors=catalog_colors
    ).style.color


def test_catalog_color_does_not_mutate_the_shared_global_style():
    """compute_marker_geometry must return a *copy* with the color swapped in, not
    mutate global_style.marker_style in place (which is shared across every object)."""
    ann = _ann("messier")
    style = StylePreset(name="test")
    original_color = style.marker_style.color
    compute_marker_geometry(ann, style, catalog_colors={"messier": "#F2C572"})
    assert style.marker_style.color == original_color


# --- Label background color: "match catalog color" is the default (background_color
# = None), per explicit user decision after reviewing a side-by-side mockup. An
# explicit hex string is a deliberate override and always wins, same precedence as
# marker/connector.


def test_label_style_default_background_color_is_none_meaning_inherit():
    assert LabelStyle().background_color is None


def test_minimal_modern_preset_inherits_catalog_color_by_default():
    """The shipped default preset must not hardcode a flat background color -- that
    would silently defeat the new "match catalog color" default for every fresh
    install."""
    assert preset_store.default_preset().label_style.background_color is None
    assert preset_store.default_preset_for_image(4000, 3000).label_style.background_color is None


def test_label_background_color_matches_catalog_when_unset():
    ann = _ann("sh2")
    style = StylePreset(name="test")
    geo = compute_label_geometry(ann, style, catalog_colors={"sh2": "#F2938C"})
    assert geo.style.background_color == "#F2938C"


def test_label_background_color_explicit_override_wins_over_catalog_color():
    override = LabelStyle(background_color="#222222")
    ann = _ann("sh2", label_style=override)
    style = StylePreset(name="test")
    geo = compute_label_geometry(ann, style, catalog_colors={"sh2": "#F2938C"})
    assert geo.style.background_color == "#222222"


def test_label_background_color_falls_back_to_flat_default_with_no_catalog_color():
    ann = _ann("sh2")
    style = StylePreset(name="test")
    geo = compute_label_geometry(ann, style, catalog_colors=None)
    assert geo.style.background_color == "#101015"


def test_label_background_color_does_not_mutate_shared_global_style():
    ann = _ann("messier")
    style = StylePreset(name="test")
    compute_label_geometry(ann, style, catalog_colors={"messier": "#F2C572"})
    assert style.label_style.background_color is None


# --- WR default name display: "Catalog then Common" (e.g. "WR 134 (HIP 99377)"), -----
# --- per explicit user request -- scoped to just this one catalog, not a general -----
# --- per-catalog setting every catalog gets. See compute_label_geometry's own ---------
# --- comment for why this lives there rather than baked into a per-object ------------
# --- label_style at catalog-fetch time (that would clobber every other label_style ---
# --- field with flat defaults, the same class of bug already fixed once this ---------
# --- session for galaxy shape data and marker_style). ---------------------------------


def _wr_ann(common_name: str | None = "HIP 99377", label_style=None) -> Annotation:
    return Annotation(
        catalog="wr", catalog_name="WR 134", common_name=common_name,
        ra=0.0, dec=0.0, image_x=100.0, image_y=100.0, label_style=label_style,
    )


def test_wr_object_defaults_to_catalog_then_common():
    ann = _wr_ann()
    style = StylePreset(name="test")  # global default stays CATALOG_ONLY, untouched
    geo = compute_label_geometry(ann, style)
    assert geo.text == "WR 134 (HIP 99377)"
    # The global preset's own name_display is never mutated by this per-catalog default.
    assert style.label_style.name_display is NameDisplayMode.CATALOG_ONLY


def test_wr_object_falls_back_to_catalog_only_without_a_common_name():
    ann = _wr_ann(common_name=None)
    style = StylePreset(name="test")
    geo = compute_label_geometry(ann, style)
    assert geo.text == "WR 134"


def test_non_wr_object_is_unaffected_by_the_wr_default():
    ann = Annotation(
        catalog="messier", catalog_name="M31", common_name="Andromeda Galaxy",
        ra=0.0, dec=0.0, image_x=100.0, image_y=100.0,
    )
    style = StylePreset(name="test")
    geo = compute_label_geometry(ann, style)
    assert geo.text == "M31"  # global default (CATALOG_ONLY), not "M31 (Andromeda Galaxy)"


def test_wr_default_is_overridden_by_a_real_per_object_label_style():
    """Per explicit user decision: fully editable/overridable afterward, same
    precedence as every other auto-derived per-object property in this app -- a real
    per-object label_style (the user manually editing this object's style) always
    wins over the WR-specific default."""
    override = LabelStyle(name_display=NameDisplayMode.COMMON_ONLY)
    ann = _wr_ann(label_style=override)
    style = StylePreset(name="test")
    geo = compute_label_geometry(ann, style)
    assert geo.text == "HIP 99377"


def test_wr_default_is_overridden_by_catalog_only_explicitly_chosen_per_object():
    """Even choosing the *same* mode the app-wide default already uses, once it's a
    real per-object override, must win over the WR-specific auto-default."""
    override = LabelStyle(name_display=NameDisplayMode.CATALOG_ONLY)
    ann = _wr_ann(label_style=override)
    style = StylePreset(name="test")
    geo = compute_label_geometry(ann, style)
    assert geo.text == "WR 134"
