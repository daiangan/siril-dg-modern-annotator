"""star_identify.py -- "Identify Star" right-click feature (annotation/star_identify.py).
No network access here: astroquery.simbad.Simbad is fully mocked, same style as
test_vizier_parsers.py's own astroquery.vizier.Vizier mock -- only the query-shaping/
filtering/sorting logic in identify_stars() and default_radius_arcsec() is covered.
Both were live-verified against the real SIMBAD service before this was written (see
star_identify.py's own docstring); this file guards the offline logic around that call,
not the call itself."""

from __future__ import annotations

from astropy.table import Table

from siril_modern_annotator.annotation.star_identify import (
    StarCandidate,
    default_radius_arcsec,
    identify_stars,
)

_COLUMNS = ["main_id", "ra", "dec", "otype", "V"]


def _table(rows: list[list]) -> Table:
    return Table(rows=rows, names=_COLUMNS, dtype=[str, "float64", "float64", str, str])


class _FakeSimbad:
    """Stands in for astroquery.simbad.Simbad -- constructed with no args (matching
    Simbad()), add_votable_fields is a no-op, query_region returns whatever this test
    configured via the module-level _NEXT_RESULT indirection below."""

    def __init__(self, *args, **kwargs):
        pass

    def add_votable_fields(self, *args, **kwargs):
        pass

    def query_region(self, *args, **kwargs):
        return _NEXT_RESULT[0]


_NEXT_RESULT: list = [None]


def _with_result(monkeypatch, table_or_none):
    monkeypatch.setattr("astroquery.simbad.Simbad", _FakeSimbad)
    _NEXT_RESULT[0] = table_or_none


# --- default_radius_arcsec ------------------------------------------------------------


def test_default_radius_scales_linearly_in_the_middle_of_the_range():
    assert default_radius_arcsec(1.0) == 12.0
    assert default_radius_arcsec(0.5) == 6.0


def test_default_radius_clamps_to_the_floor_for_a_high_resolution_image():
    assert default_radius_arcsec(0.01) == 3.0


def test_default_radius_clamps_to_the_ceiling_for_a_wide_field_image():
    assert default_radius_arcsec(5.0) == 20.0


# --- identify_stars: otype filtering ---------------------------------------------------


def test_identify_stars_keeps_star_type_objects(monkeypatch):
    _with_result(monkeypatch, _table([["HD 192163", "303.0273", "38.3549", "WR*", "7.5"]]))
    results = identify_stars(303.0272916666666, 38.35494444444445, radius_arcsec=10.0)
    assert len(results) == 1
    assert results[0].simbad_id == "HD 192163"
    assert results[0].otype == "WR*"
    assert results[0].magnitude == 7.5


def test_identify_stars_keeps_every_star_branch_otype(monkeypatch):
    rows = [
        ["A", "10.0", "20.0", "*", ""],
        ["B", "10.0", "20.0", "**", ""],
        ["C", "10.0", "20.0", "SB*", ""],
        ["D", "10.0", "20.0", "V*", ""],
        ["E", "10.0", "20.0", "PM*", ""],
    ]
    _with_result(monkeypatch, _table(rows))
    results = identify_stars(10.0, 20.0, radius_arcsec=10.0, max_candidates=10)
    assert {c.simbad_id for c in results} == {"A", "B", "C", "D", "E"}


def test_identify_stars_drops_non_star_object_types(monkeypatch):
    # Real, live-confirmed non-star otype codes (see star_identify.py's own docstring):
    # OpC (open cluster), HII (HII region), SNR (supernova remnant). Plus the
    # documented, accepted gap: Psr (pulsar) has no asterisk and is excluded too.
    rows = [
        ["Cluster", "10.0", "20.0", "OpC", ""],
        ["Nebula", "10.0", "20.0", "HII", ""],
        ["Remnant", "10.0", "20.0", "SNR", ""],
        ["Pulsar", "10.0", "20.0", "Psr", ""],
    ]
    _with_result(monkeypatch, _table(rows))
    results = identify_stars(10.0, 20.0, radius_arcsec=10.0, max_candidates=10)
    assert results == []


def test_identify_stars_returns_empty_list_when_simbad_finds_nothing(monkeypatch):
    _with_result(monkeypatch, None)
    assert identify_stars(180.0, 0.0, radius_arcsec=5.0) == []


# --- identify_stars: sorting and truncation ---------------------------------------------


def test_identify_stars_sorts_by_separation_ascending(monkeypatch):
    # All at the same Dec, offset in RA -- farther in RA at fixed Dec is farther in
    # separation, so this exercises real angular-distance sorting, not insertion order.
    rows = [
        ["Far", "10.01", "20.0", "*", ""],
        ["Near", "10.001", "20.0", "*", ""],
        ["Middle", "10.005", "20.0", "*", ""],
    ]
    _with_result(monkeypatch, _table(rows))
    results = identify_stars(10.0, 20.0, radius_arcsec=60.0, max_candidates=10)
    assert [c.simbad_id for c in results] == ["Near", "Middle", "Far"]


def test_identify_stars_truncates_to_max_candidates(monkeypatch):
    rows = [[f"Star{i}", str(10.0 + i * 0.0001), "20.0", "*", ""] for i in range(5)]
    _with_result(monkeypatch, _table(rows))
    results = identify_stars(10.0, 20.0, radius_arcsec=60.0, max_candidates=3)
    assert len(results) == 3
    # Closest 3 kept (ascending RA offset here is ascending separation).
    assert [c.simbad_id for c in results] == ["Star0", "Star1", "Star2"]


# --- identify_stars: magnitude handling -------------------------------------------------


def test_identify_stars_handles_a_missing_magnitude_gracefully(monkeypatch):
    _with_result(monkeypatch, _table([["Faint", "10.0", "20.0", "*", "--"]]))
    results = identify_stars(10.0, 20.0, radius_arcsec=10.0)
    assert len(results) == 1
    assert results[0].magnitude is None


def test_identify_stars_handles_an_empty_magnitude_string_gracefully(monkeypatch):
    _with_result(monkeypatch, _table([["Faint", "10.0", "20.0", "*", ""]]))
    results = identify_stars(10.0, 20.0, radius_arcsec=10.0)
    assert results[0].magnitude is None


# --- StarCandidate is a plain, comparable value type ------------------------------------


def test_star_candidate_is_frozen_and_comparable():
    a = StarCandidate(simbad_id="X", ra=1.0, dec=2.0, otype="*", magnitude=5.0, separation_arcsec=1.0)
    b = StarCandidate(simbad_id="X", ra=1.0, dec=2.0, otype="*", magnitude=5.0, separation_arcsec=1.0)
    assert a == b
    try:
        a.simbad_id = "Y"  # type: ignore[misc]
        assert False, "StarCandidate must be frozen"
    except AttributeError:
        pass
