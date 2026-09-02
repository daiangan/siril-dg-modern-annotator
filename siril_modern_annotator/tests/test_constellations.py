"""Constellation stick-figure lines + name labels: the CSV loader
(annotation/constellations.py) and the frame-filtering geometry it feeds
(annotation/renderer.py's compute_constellation_geometry) -- an image-level overlay,
same category as the RA/Dec grid/compass (see test_grid_compass.py), not a
CatalogProvider/Annotation."""

from __future__ import annotations

from pathlib import Path

from siril_modern_annotator.annotation.constellations import (
    ConstellationLine,
    ConstellationName,
    load_constellation_lines,
    load_constellation_names,
)
from siril_modern_annotator.annotation.models import ConstellationStyle
from siril_modern_annotator.annotation.renderer import compute_constellation_geometry
from siril_modern_annotator.annotation.wcs import SirilWcs

_WIDTH, _HEIGHT = 2000, 1500
_PIXEL_SCALE_DEG = 1.5 / 3600.0


def _wcs(center_ra=180.0, center_dec=30.0) -> SirilWcs:
    header = {
        "NAXIS": 2, "NAXIS1": _WIDTH, "NAXIS2": _HEIGHT,
        "CTYPE1": "RA---TAN", "CTYPE2": "DEC--TAN",
        "CRPIX1": _WIDTH / 2.0, "CRPIX2": _HEIGHT / 2.0,
        "CRVAL1": center_ra, "CRVAL2": center_dec,
        "CDELT1": -_PIXEL_SCALE_DEG, "CDELT2": _PIXEL_SCALE_DEG,
        "CUNIT1": "deg", "CUNIT2": "deg",
    }
    return SirilWcs.from_header_dict(header, _WIDTH, _HEIGHT)


# ------------------------------------------------------------------------ loader ----


def test_load_constellation_lines_parses_real_schema(tmp_path: Path):
    # Real header/row shape, live-confirmed against Siril's own bundled
    # constellations.csv.
    (tmp_path / "constellations.csv").write_text(
        "ra,dec,ra1,dec1\n"
        "30.9735,42.3187,24.201,41.4019\n"
        "24.201,41.4019,24.4995,48.6155\n",
        encoding="utf-8",
    )
    lines = load_constellation_lines(tmp_path)
    assert lines == [
        ConstellationLine(30.9735, 42.3187, 24.201, 41.4019),
        ConstellationLine(24.201, 41.4019, 24.4995, 48.6155),
    ]


def test_load_constellation_lines_skips_rows_with_a_malformed_coordinate(tmp_path: Path):
    (tmp_path / "constellations.csv").write_text(
        "ra,dec,ra1,dec1\n"
        "30.9735,42.3187,24.201,41.4019\n"
        "not-a-number,42.3187,24.201,41.4019\n",
        encoding="utf-8",
    )
    lines = load_constellation_lines(tmp_path)
    assert len(lines) == 1


def test_load_constellation_lines_missing_file_returns_empty_list(tmp_path: Path):
    assert load_constellation_lines(tmp_path) == []


def test_load_constellation_names_parses_real_schema(tmp_path: Path):
    (tmp_path / "constellationsnames.csv").write_text(
        "name,alias,ra,dec\n"
        "Aquila,Aql,295.0083,3.4114\n"
        "Andromeda,And,12.1958,37.4314\n",
        encoding="utf-8",
    )
    names = load_constellation_names(tmp_path)
    assert names == [
        ConstellationName("Aquila", 295.0083, 3.4114),
        ConstellationName("Andromeda", 12.1958, 37.4314),
    ]


def test_load_constellation_names_skips_blank_name(tmp_path: Path):
    (tmp_path / "constellationsnames.csv").write_text(
        "name,alias,ra,dec\n"
        ",Aql,295.0083,3.4114\n"
        "Andromeda,And,12.1958,37.4314\n",
        encoding="utf-8",
    )
    names = load_constellation_names(tmp_path)
    assert len(names) == 1
    assert names[0].name == "Andromeda"


def test_load_constellation_names_missing_file_returns_empty_list(tmp_path: Path):
    assert load_constellation_names(tmp_path) == []


# --------------------------------------------------------------------- geometry ----


def test_constellation_geometry_empty_when_disabled():
    wcs = _wcs()
    lines = [ConstellationLine(180.0, 30.0, 181.0, 30.0)]
    names = [ConstellationName("Test", 180.0, 30.0)]
    geo = compute_constellation_geometry(wcs, ConstellationStyle(enabled=False), lines, names)
    assert geo.lines == []
    assert geo.labels == []


def test_constellation_geometry_keeps_a_line_fully_inside_the_frame():
    wcs = _wcs(center_ra=180.0, center_dec=30.0)
    # Both endpoints close to the field center -- comfortably inside the frame.
    lines = [ConstellationLine(180.0, 30.0, 180.01, 30.01)]
    geo = compute_constellation_geometry(wcs, ConstellationStyle(enabled=True), lines, [])
    assert len(geo.lines) == 1
    (x0, y0), (x1, y1) = geo.lines[0]
    assert 0.0 <= x0 <= _WIDTH and 0.0 <= y0 <= _HEIGHT
    assert 0.0 <= x1 <= _WIDTH and 0.0 <= y1 <= _HEIGHT


def test_constellation_geometry_keeps_a_line_with_only_one_endpoint_inside():
    """Per explicit user decision: a segment crossing the frame edge stays visible
    (both raw endpoints, un-clipped) as long as at least one endpoint projects inside
    the frame -- exact geometric clipping at the boundary was decided against."""
    wcs = _wcs(center_ra=180.0, center_dec=30.0)
    lines = [ConstellationLine(180.0, 30.0, 180.0, 90.0)]  # far end near the pole, well outside
    geo = compute_constellation_geometry(wcs, ConstellationStyle(enabled=True), lines, [])
    assert len(geo.lines) == 1


def test_constellation_geometry_drops_a_line_entirely_outside_the_frame():
    wcs = _wcs(center_ra=180.0, center_dec=30.0)
    lines = [ConstellationLine(0.0, -60.0, 1.0, -60.0)]  # nowhere near the frame
    geo = compute_constellation_geometry(wcs, ConstellationStyle(enabled=True), lines, [])
    assert geo.lines == []


def test_constellation_geometry_keeps_a_name_label_inside_the_frame():
    wcs = _wcs(center_ra=180.0, center_dec=30.0)
    names = [ConstellationName("Inside", 180.0, 30.0)]
    geo = compute_constellation_geometry(wcs, ConstellationStyle(enabled=True), [], names)
    assert len(geo.labels) == 1
    assert geo.labels[0].text == "Inside"


def test_constellation_geometry_drops_a_name_label_outside_the_frame():
    wcs = _wcs(center_ra=180.0, center_dec=30.0)
    names = [ConstellationName("Outside", 0.0, -60.0)]
    geo = compute_constellation_geometry(wcs, ConstellationStyle(enabled=True), [], names)
    assert geo.labels == []


def test_constellation_geometry_omits_labels_when_show_labels_false():
    wcs = _wcs(center_ra=180.0, center_dec=30.0)
    names = [ConstellationName("Inside", 180.0, 30.0)]
    style = ConstellationStyle(enabled=True, show_labels=False)
    geo = compute_constellation_geometry(wcs, style, [], names)
    assert geo.labels == []


def test_constellation_geometry_empty_data_produces_empty_geometry():
    wcs = _wcs()
    geo = compute_constellation_geometry(wcs, ConstellationStyle(enabled=True), [], [])
    assert geo.lines == []
    assert geo.labels == []
