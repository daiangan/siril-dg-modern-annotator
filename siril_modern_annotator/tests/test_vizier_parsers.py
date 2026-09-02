"""Per-catalog VizieR row parsers (annotation/catalogs.py), tested offline against
fixture data mirroring the *real* schemas confirmed by live-querying VizieR earlier this
session -- these replaced a previous generic column-name guesser that silently returned
zero rows for every one of these catalogs because none of the real column names/formats
matched what it assumed (decimal "ra"/"dec" columns; VII/118 and VII/20 both use
sexagesimal strings under catalog-specific names, and VII/20 uses a different equinox
entirely). No network access here -- VizierProvider.query() itself is not covered by
this offline suite (see README.md); only the row-level parsing logic is. One
exception: the query()-level catalog-filtering regression test near the bottom of this
file, which fully mocks astroquery.vizier.Vizier rather than making a real call.

Note: this module previously also covered SIMBAD common-name resolution
(resolve_common_names_via_simbad, _best_common_name). That feature was removed after
real-world use showed SIMBAD's TAP service, and its only mirror, too unreliable to be
worth keeping -- see annotation/catalogs.py's VizierProvider docstring. Those tests were
removed along with the feature; the hard-timeout tests below remain because
_run_with_hard_timeout is still used to bound VizieR's own queries.
"""

from __future__ import annotations

import pytest
from astropy.table import Table

from siril_modern_annotator.annotation import catalogs as catalogs_module
from siril_modern_annotator.annotation.catalogs import (
    VizierProvider,
    _format_v50_name,
    _run_with_hard_timeout,
    _v50_row_to_annotation,
    _vii20_row_to_annotation,
    _vii118_row_to_annotation,
    _GalaxyShape,
    _nearest_galaxy_shape,
    _position_angle_to_screen_rotation_deg,
    _sga2020_row_to_shape,
    _vii21_row_to_annotation,
    _vii192_row_to_annotation,
    _vii213_row_to_annotation,
    _vii216_row_to_annotation,
    _vii220a_row_to_annotation,
    _vii237_row_to_shape,
    _v163_row_to_annotation,
    _vii272_row_to_annotation,
    _vii9_row_to_annotation,
    bayer_designation_to_greek,
)
from siril_modern_annotator.annotation.wcs import SirilWcs

_PIXEL_SCALE_DEG = 3.0 / 3600.0  # 3"/px -- generous field for these tests
_WIDTH, _HEIGHT = 4000, 4000


def _wcs_at(center_ra: float, center_dec: float) -> SirilWcs:
    header = {
        "NAXIS": 2, "NAXIS1": _WIDTH, "NAXIS2": _HEIGHT,
        "CTYPE1": "RA---TAN", "CTYPE2": "DEC--TAN",
        "CRPIX1": _WIDTH / 2.0, "CRPIX2": _HEIGHT / 2.0,
        "CRVAL1": center_ra, "CRVAL2": center_dec,
        "CDELT1": -_PIXEL_SCALE_DEG, "CDELT2": _PIXEL_SCALE_DEG,
        "CUNIT1": "deg", "CUNIT2": "deg",
    }
    return SirilWcs.from_header_dict(header, _WIDTH, _HEIGHT)


# --- VII/118 (NGC 2000.0) -- real rows from a live query near Orion ------------------

_VII118_COLUMNS = ["Name", "Type", "RAB2000", "DEB2000", "Source", "Const", "l_size", "size", "mag", "n_mag", "Desc"]


def _vii118_table(rows: list[list[str]]) -> Table:
    return Table(rows=rows, names=_VII118_COLUMNS, dtype=[str] * len(_VII118_COLUMNS))


def test_vii118_ngc_row_parses_as_ngc():
    table = _vii118_table([["1924", "Gx", "05 27.9", "-05 19", "r", "Ori", "", "--", "13.0", "p", "vF, pL, iR, st nr"]])
    wcs = _wcs_at(81.975, -5.3167)
    ann = _vii118_row_to_annotation(table[0], wcs, None)
    assert ann is not None
    assert ann.catalog == "ngc"
    assert ann.catalog_name == "NGC1924"
    assert ann.object_type == "galaxy"
    assert ann.magnitude == 13.0
    assert ann.ra == pytest.approx(81.975, abs=1e-3)
    assert ann.dec == pytest.approx(-5.3167, abs=0.01)


def test_vii118_ic_row_parses_as_ic():
    table = _vii118_table([["I 420", "", "05 32.3", "-04 30", "d", "Ori", "", "--", "--", "", "vF, spp *9 (not verified)"]])
    wcs = _wcs_at(83.075, -4.5)
    ann = _vii118_row_to_annotation(table[0], wcs, None)
    assert ann is not None
    assert ann.catalog == "ic"
    assert ann.catalog_name == "IC420"
    assert ann.object_type == "unknown"  # blank Type code


def test_vii118_ic_row_with_4digit_number_has_no_space_in_name_field():
    """Regression test for a real bug: a user's screenshot showed labels
    "NGCI5067"/"NGCI5070" instead of "IC5067"/"IC5070". Root cause -- the "Name" field
    is fixed-width, and the space between "I" and the number is itself part of that
    padding: present for 3-digit IC numbers ("I 420") but absent once the number is
    wide enough to fill the field ("I5067", confirmed via a live VizieR query of this
    exact field). Matching only "I " (with a required space) silently treated these as
    NGC and prepended "NGC" to the still-"I"-prefixed raw name."""
    table = _vii118_table([["I5067", "Nb", "20 50.8", "+44 21", "s", "Cyg", "", "10.0", "--", "", ""]])
    wcs = _wcs_at(312.7, 44.35)
    ann = _vii118_row_to_annotation(table[0], wcs, None)
    assert ann is not None
    assert ann.catalog == "ic"
    assert ann.catalog_name == "IC5067"


def test_vii118_messier_cross_reference_wins_via_desc_field():
    """NGC 1976 = M42: the Messier identity only appears as free text in Desc, and it
    must take priority over the bare NGC designation for both catalog and display name."""
    table = _vii118_table(
        [["1976", "Nb", "05 35.4", "-05 27", "s", "Ori", "", "66.0", "4.0", "", "!!! theta1 Ori and the great neb; = M42"]]
    )
    wcs = _wcs_at(83.85, -5.45)
    ann = _vii118_row_to_annotation(table[0], wcs, None)
    assert ann is not None
    assert ann.catalog == "messier"
    assert ann.catalog_name == "M42"
    assert ann.object_type == "nebula"
    assert ann.angular_size == 66.0


# --- VizierProvider.query() -- one exception to this module's "no network" scope: a
# fully mocked astroquery.vizier.Vizier, so this stays a real unit test rather than a
# live network call, per the module docstring above.


def test_vizier_provider_filters_out_reclassified_catalogs_not_requested(monkeypatch):
    """Regression test for a real report: with only "messier" checked in the app's
    Catalogs menu, NGC/IC objects still appeared on the canvas. VII/118 is one combined
    NGC/IC/Messier catalog -- querying it can't be restricted server-side to just the
    subset requested, and each row's own catalog is only decided *after* parsing (a
    Messier cross-ref in Desc promotes a row to "messier", see
    _vii118_row_to_annotation) -- so query() must filter its results back down to what
    was actually requested, not return everything VII/118 happens to have in the field."""
    table = _vii118_table(
        [
            # M42, via NGC1976's Desc cross-reference -- classifies as "messier".
            ["1976", "Nb", "05 35.4", "-05 27", "s", "Ori", "", "66.0", "4.0", "", "!!! theta1 Ori and the great neb; = M42"],
            # A plain NGC object, no Messier cross-reference at all.
            ["1924", "Gx", "05 27.9", "-05 19", "r", "Ori", "", "--", "13.0", "p", "vF, pL, iR, st nr"],
        ]
    )

    class _FakeVizier:
        def __init__(self, *args, **kwargs):
            pass

        def query_region(self, *args, **kwargs):
            return [table]

    monkeypatch.setattr("astroquery.vizier.Vizier", _FakeVizier)
    monkeypatch.setattr(catalogs_module, "_vizier_available", True)

    wcs = _wcs_at(83.0, -5.4)  # covers both M42 (83.85, -5.45) and NGC1924 (81.975, -5.3167)
    provider = VizierProvider()

    messier_only = provider.query(wcs, {"messier"})
    assert {a.catalog for a in messier_only} == {"messier"}
    assert {a.catalog_name for a in messier_only} == {"M42"}

    ngc_only = provider.query(wcs, {"ngc"})
    assert {a.catalog for a in ngc_only} == {"ngc"}
    assert {a.catalog_name for a in ngc_only} == {"NGC1924"}

    both = provider.query(wcs, {"messier", "ngc"})
    assert {a.catalog_name for a in both} == {"M42", "NGC1924"}


def test_vii118_object_outside_field_is_dropped():
    table = _vii118_table([["9999", "Gx", "12 00.0", "+00 00", "s", "Vir", "", "--", "--", "", ""]])
    wcs = _wcs_at(83.85, -5.45)  # Orion field -- nowhere near RA 12h/Dec 0
    assert _vii118_row_to_annotation(table[0], wcs, None) is None


def test_vii118_mag_limit_filters_dim_objects():
    table = _vii118_table([["1924", "Gx", "05 27.9", "-05 19", "r", "Ori", "", "--", "13.0", "p", ""]])
    wcs = _wcs_at(81.975, -5.3167)
    assert _vii118_row_to_annotation(table[0], wcs, mag_limit=10.0) is None
    assert _vii118_row_to_annotation(table[0], wcs, mag_limit=14.0) is not None


def test_vii118_malformed_coordinates_return_none_not_crash():
    table = _vii118_table([["1924", "Gx", "not-a-coord", "-05 19", "r", "Ori", "", "--", "--", "", ""]])
    wcs = _wcs_at(81.975, -5.3167)
    assert _vii118_row_to_annotation(table[0], wcs, None) is None


# --- V/50 (Yale Bright Star Catalogue) -- real rows from a live query near Cygnus -----

_V50_COLUMNS = ["HR", "Name", "HD", "ADS", "VarID", "RAJ2000", "DEJ2000", "Vmag", "B-V", "SpType", "NoteFlag"]


def _v50_table(rows: list[list[str]]) -> Table:
    return Table(rows=rows, names=_V50_COLUMNS, dtype=[str] * len(_V50_COLUMNS))


def test_v50_alpha_abbreviation_mismatch_with_siril_is_handled():
    """Real, confirmed mismatch: V/50 abbreviates alpha as "Alp" (first three letters),
    while Siril's own local catalog uses "alf" (phonetic) -- both must resolve to the
    same Greek letter."""
    table = _v50_table(
        [["7924", "50Alp Cyg", "197345", "", "", "20 41 25.9", "+45 16 49", "1.25", "0.09", "A2Iae", "*"]]
    )
    wcs = _wcs_at(310.358, 45.28)
    ann = _v50_row_to_annotation(table[0], wcs, None)
    assert ann is not None
    assert ann.catalog_name == "α Cyg"
    assert ann.catalog == "bright_star"
    assert ann.magnitude == 1.25


def test_v50_flamsteed_only_name_falls_back_reasonably():
    table = _v50_table([["7828", "43    Cyg", "195068", "", "", "20 32 00.0", "+41 00 00", "6.23", "-0.06", "B9.5Vn", "*"]])
    wcs = _wcs_at(308.0, 41.0)
    ann = _v50_row_to_annotation(table[0], wcs, None)
    assert ann is not None
    assert ann.catalog_name == "43 Cyg"


def test_v50_unnamed_star_falls_back_to_hd_number():
    table = _v50_table([["1806", "", "35640", "", "", "05 26 02.4", "-05 31 06", "6.23", "-0.06", "B9.5Vn", "*"]])
    wcs = _wcs_at(81.51, -5.52)
    ann = _v50_row_to_annotation(table[0], wcs, None)
    assert ann is not None
    assert ann.catalog_name == "HD 35640"


def test_v50_xi_abbreviation_matches_siril_convention():
    table = _v50_table([["8079", "62Xi  Cyg", "200905", "", "", "21 04 26.0", "+43 55 40", "3.72", "1.62", "K4Ib", "*"]])
    wcs = _wcs_at(316.11, 43.93)
    ann = _v50_row_to_annotation(table[0], wcs, None)
    assert ann.catalog_name == "ξ Cyg"


def test_v50_populates_simbad_id_from_hd_even_when_name_is_present():
    """Real report: SIMBAD rejects the reconstructed name "b01 Cyg" outright
    ("incorrect format for catalogs") because a lowercase single-letter Bayer prefix
    collides with several unrelated catalogs' own identifier syntax. Rather than
    special-casing that (and the next format SIMBAD chokes on), catalog_name stays the
    friendly display string and simbad_id carries V/50's own HD number instead --
    confirmed live that "HD <number>" always resolves on SIMBAD."""
    table = _v50_table([["7539", "b01Cyg", "186408", "", "", "19 58 22.0", "+35 05 00", "5.20", "0.63", "G2V", "*"]])
    wcs = _wcs_at(299.59, 35.08)
    ann = _v50_row_to_annotation(table[0], wcs, None)
    assert ann is not None
    assert ann.simbad_id == "HD 186408"


def test_v50_falls_back_to_hr_when_hd_is_blank():
    table = _v50_table([["7539", "", "", "", "", "19 58 22.0", "+35 05 00", "5.20", "0.63", "G2V", "*"]])
    wcs = _wcs_at(299.59, 35.08)
    ann = _v50_row_to_annotation(table[0], wcs, None)
    assert ann is not None
    assert ann.simbad_id == "HR 7539"


def test_v50_simbad_id_is_none_when_neither_hd_nor_hr_present():
    table = _v50_table([["", "43    Cyg", "", "", "", "20 32 00.0", "+41 00 00", "6.23", "-0.06", "B9.5Vn", "*"]])
    wcs = _wcs_at(308.0, 41.0)
    ann = _v50_row_to_annotation(table[0], wcs, None)
    assert ann is not None
    assert ann.simbad_id is None


# --- VII/20 (Sharpless 1959) -- real row from a live query, B1900 equinox -----------

_VII20_COLUMNS = ["Sh2", "GlLund", "GbLund", "GLon", "GLat", "RA1900", "DE1900", "Diam", "Form", "Struct", "Bright"]


def _vii20_table(rows: list[list[str]]) -> Table:
    return Table(rows=rows, names=_VII20_COLUMNS, dtype=[str] * len(_VII20_COLUMNS))


def test_vii20_b1900_coordinates_are_precessed_to_j2000():
    """Sh2-107 real B1900 coordinates from a live VizieR query; the parsed J2000
    position must differ meaningfully from a naive "treat as J2000" parse -- B1900 to
    J2000 precession over ~100 years is not negligible."""
    table = _vii20_table([["107", "45.2", "-4.5", "77.4", "-3.7", "20 38 54.0", "+35 59 00", "5", "2", "2", "2"]])
    # Field wide enough to contain both the naive-J2000 and properly-precessed positions.
    wcs = _wcs_at(310.0, 36.5)
    ann = _vii20_row_to_annotation(table[0], wcs, None)
    assert ann is not None
    assert ann.catalog == "sh2"
    assert ann.catalog_name == "Sh2-107"
    assert ann.angular_size == 5.0
    # Naive (wrong) parse would put this at ~309.725 RA; precession shifts it noticeably.
    from astropy.coordinates import Angle
    from astropy import units as u
    naive_ra = Angle("20 38 54.0", unit=u.hourangle).degree
    assert abs(ann.ra - naive_ra) > 0.1, "B1900->J2000 precession does not appear to have been applied"


# --- VII/220A (Barnard 1919, dark nebulae) -- real row from a live query near the ------
# --- Horsehead (B33), confirmed 2026-08-31. A third equinox (B1875) alongside VII/118's
# --- B2000 and VII/20's B1900 -- this parser sidesteps it entirely by using VizieR's own
# --- pre-converted _RA.icrs/_DE.icrs columns instead of transforming RA1875/DE1875 itself.

_VII220A_COLUMNS = ["Barn", "RA1875", "DE1875", "Diam", "_RA.icrs", "_DE.icrs"]


def _vii220a_table(rows: list[list]) -> Table:
    return Table(rows=rows, names=_VII220A_COLUMNS, dtype=[str, str, str, "float32", str, str])


def test_vii220a_real_row_parses_as_barnard():
    table = _vii220a_table([["33", "05 34 36", "-02 32", 4.0, "05 40 52.9", "-02 27 57"]])
    wcs = _wcs_at(85.22, -2.466)
    ann = _vii220a_row_to_annotation(table[0], wcs, None)
    assert ann is not None
    assert ann.catalog == "barnard"
    assert ann.catalog_name == "B33"
    assert ann.object_type == "dark nebula"
    assert ann.angular_size == 4.0
    assert ann.magnitude is None  # dark nebulae have no point-source magnitude
    assert ann.ra == pytest.approx(85.22, abs=0.01)
    assert ann.dec == pytest.approx(-2.466, abs=0.01)


def test_vii220a_missing_diameter_still_parses():
    # VizieR represents a missing float column as NaN, not a "--" string, for a real
    # float32-typed column -- str(nan) == "nan", which _row_str's existing missing-
    # value filter already catches.
    table = _vii220a_table([["72", "17 20 06", "-23 16", float("nan"), "17 23 30.0", "-23 46 00"]])
    wcs = _wcs_at(260.875, -23.767)
    ann = _vii220a_row_to_annotation(table[0], wcs, None)
    assert ann is not None
    assert ann.catalog_name == "B72"
    assert ann.angular_size is None


def test_vii220a_missing_barn_number_returns_none():
    table = _vii220a_table([["", "05 34 36", "-02 32", 4.0, "05 40 52.9", "-02 27 57"]])
    wcs = _wcs_at(85.22, -2.466)
    assert _vii220a_row_to_annotation(table[0], wcs, None) is None


def test_vii220a_object_outside_field_is_dropped():
    table = _vii220a_table([["33", "05 34 36", "-02 32", 4.0, "05 40 52.9", "-02 27 57"]])
    wcs = _wcs_at(310.0, 45.0)  # Cygnus field -- nowhere near B33
    assert _vii220a_row_to_annotation(table[0], wcs, None) is None


def test_vii220a_malformed_coordinates_return_none_not_crash():
    table = _vii220a_table([["33", "05 34 36", "-02 32", 4.0, "not-a-coord", "-02 27 57"]])
    wcs = _wcs_at(85.22, -2.466)
    assert _vii220a_row_to_annotation(table[0], wcs, None) is None


# --- VII/9 (Lynds' Catalogue of Bright Nebulae) -- real row from a live query near ------
# --- Cygnus. Same shape as VII/220A above: native RA1950/DE1950 are minute-precision -----
# --- only and B1950, so this parser uses VizieR's own pre-converted _RA.icrs/_DE.icrs -----
# --- instead, same as the Barnard parser does. ---------------------------------------------

_VII9_COLUMNS = ["Seq", "Diam1", "_RA.icrs", "_DE.icrs"]


def _vii9_table(rows: list[list]) -> Table:
    return Table(rows=rows, names=_VII9_COLUMNS, dtype=[str, "float32", str, str])


def test_vii9_real_row_parses_as_lbn():
    table = _vii9_table([["245", 45.0, "20 27 49.0", "+39 59 59"]])
    wcs = _wcs_at(306.954, 39.9997)
    ann = _vii9_row_to_annotation(table[0], wcs, None)
    assert ann is not None
    assert ann.catalog == "lbn"
    assert ann.catalog_name == "LBN 245"
    assert ann.object_type == "nebula"
    assert ann.angular_size == 45.0
    assert ann.magnitude is None  # nebulae have no point-source magnitude
    assert ann.ra == pytest.approx(306.954, abs=0.01)
    assert ann.dec == pytest.approx(39.9997, abs=0.01)


def test_vii9_missing_diameter_still_parses():
    table = _vii9_table([["245", float("nan"), "20 27 49.0", "+39 59 59"]])
    wcs = _wcs_at(306.954, 39.9997)
    ann = _vii9_row_to_annotation(table[0], wcs, None)
    assert ann is not None
    assert ann.catalog_name == "LBN 245"
    assert ann.angular_size is None


def test_vii9_missing_seq_number_returns_none():
    table = _vii9_table([["", 45.0, "20 27 49.0", "+39 59 59"]])
    wcs = _wcs_at(306.954, 39.9997)
    assert _vii9_row_to_annotation(table[0], wcs, None) is None


def test_vii9_object_outside_field_is_dropped():
    table = _vii9_table([["245", 45.0, "20 27 49.0", "+39 59 59"]])
    wcs = _wcs_at(85.22, -2.466)  # Orion field -- nowhere near LBN 245
    assert _vii9_row_to_annotation(table[0], wcs, None) is None


def test_vii9_malformed_coordinates_return_none_not_crash():
    table = _vii9_table([["245", 45.0, "not-a-coord", "+39 59 59"]])
    wcs = _wcs_at(306.954, 39.9997)
    assert _vii9_row_to_annotation(table[0], wcs, None) is None


# --- VII/216 (Rodgers, Campbell & Whiteoak) -- real row from a live query near --------
# --- Carina. Same shape as VII/9/VII/220A: native RAB1950/DEB1950 are minute- ---------
# --- precision only and B1950, so this parser uses VizieR's own pre-converted --------
# --- _RA.icrs/_DE.icrs instead. -------------------------------------------------------

_VII216_COLUMNS = ["RCW", "MajAxis", "_RA.icrs", "_DE.icrs"]


def _vii216_table(rows: list[list]) -> Table:
    return Table(rows=rows, names=_VII216_COLUMNS, dtype=[str, "float32", str, str])


def test_vii216_real_row_parses_as_rcw():
    table = _vii216_table([["53", 210.0, "10 41 55.2", "-59 45 43"]])
    wcs = _wcs_at(160.48, -59.7619)
    ann = _vii216_row_to_annotation(table[0], wcs, None)
    assert ann is not None
    assert ann.catalog == "rcw"
    assert ann.catalog_name == "RCW 53"
    assert ann.object_type == "nebula"
    assert ann.angular_size == 210.0
    assert ann.magnitude is None  # emission regions have no point-source magnitude
    assert ann.ra == pytest.approx(160.48, abs=0.01)
    assert ann.dec == pytest.approx(-59.7619, abs=0.01)


def test_vii216_missing_major_axis_still_parses():
    table = _vii216_table([["53", float("nan"), "10 41 55.2", "-59 45 43"]])
    wcs = _wcs_at(160.48, -59.7619)
    ann = _vii216_row_to_annotation(table[0], wcs, None)
    assert ann is not None
    assert ann.catalog_name == "RCW 53"
    assert ann.angular_size is None


def test_vii216_missing_rcw_number_returns_none():
    table = _vii216_table([["", 210.0, "10 41 55.2", "-59 45 43"]])
    wcs = _wcs_at(160.48, -59.7619)
    assert _vii216_row_to_annotation(table[0], wcs, None) is None


def test_vii216_object_outside_field_is_dropped():
    table = _vii216_table([["53", 210.0, "10 41 55.2", "-59 45 43"]])
    wcs = _wcs_at(306.954, 39.9997)  # Cygnus field -- nowhere near RCW 53
    assert _vii216_row_to_annotation(table[0], wcs, None) is None


def test_vii216_malformed_coordinates_return_none_not_crash():
    table = _vii216_table([["53", 210.0, "not-a-coord", "-59 45 43"]])
    wcs = _wcs_at(160.48, -59.7619)
    assert _vii216_row_to_annotation(table[0], wcs, None) is None


# --- VII/21 (van den Bergh) -- real row from a live query near the Pleiades. ----------
# --- Unlike every parser above, _RA/_DE are already plain J2000 decimal degrees, ------
# --- so no sexagesimal parse/frame transform is needed at all. -----------------------

_VII21_COLUMNS = ["VdB", "HD", "_RA", "_DE"]


def _vii21_table(rows: list[list]) -> Table:
    return Table(rows=rows, names=_VII21_COLUMNS, dtype=[str, str, "float64", "float64"])


def test_vii21_real_row_parses_as_vdb():
    table = _vii21_table([["20", "23302", 56.2189, 24.11334]])
    wcs = _wcs_at(56.75, 24.12)
    ann = _vii21_row_to_annotation(table[0], wcs, None)
    assert ann is not None
    assert ann.catalog == "vdb"
    assert ann.catalog_name == "vdB 20"
    assert ann.object_type == "reflection nebula"
    # This catalog carries no nebula size/magnitude field at all -- HD/SpType/Vmag in
    # the real schema describe the illuminating star, not the nebula itself, so this
    # parser deliberately doesn't map any of them onto angular_size/magnitude.
    assert ann.angular_size is None
    assert ann.magnitude is None
    assert ann.ra == pytest.approx(56.2189, abs=1e-4)
    assert ann.dec == pytest.approx(24.11334, abs=1e-4)


def test_vii21_missing_vdb_number_returns_none():
    table = _vii21_table([["", "23302", 56.2189, 24.11334]])
    wcs = _wcs_at(56.75, 24.12)
    assert _vii21_row_to_annotation(table[0], wcs, None) is None


def test_vii21_object_outside_field_is_dropped():
    table = _vii21_table([["20", "23302", 56.2189, 24.11334]])
    wcs = _wcs_at(160.48, -59.7619)  # Carina field -- nowhere near the Pleiades
    assert _vii21_row_to_annotation(table[0], wcs, None) is None


def test_vii21_malformed_coordinates_return_none_not_crash():
    table = Table(
        rows=[["20", "23302", "not-a-number", 24.11334]],
        names=_VII21_COLUMNS, dtype=[str, str, str, "float64"],
    )
    wcs = _wcs_at(56.75, 24.12)
    assert _vii21_row_to_annotation(table[0], wcs, None) is None


# --- VII/192 (Arp/Webb) -- real rows from a live query near Arp 1/NGC 2857. -----------
# --- RAJ2000/DEJ2000 are already J2000 (RA has seconds, Dec is only DD MM.M -- no ------
# --- seconds -- but Angle parses both). Every row carries a real NGC/UGC/MCG cross- ---
# --- reference, used as simbad_id since bare "Arp <n>" is confirmed unreliable on -----
# --- SIMBAD for at least some numbers (a collision with a different Arp catalog). -----

_VII192_COLUMNS = ["Arp", "Name", "RAJ2000", "DEJ2000", "Size"]


def _vii192_table(rows: list[list]) -> Table:
    return Table(rows=rows, names=_VII192_COLUMNS, dtype=[str, str, str, str, "float32"])


def test_vii192_real_row_parses_as_arp():
    table = _vii192_table([["1", "NGC 2857", "09 24 38", "+49 21.4", 5.2]])
    wcs = _wcs_at(141.16, 49.36)
    ann = _vii192_row_to_annotation(table[0], wcs, None)
    assert ann is not None
    assert ann.catalog == "arp"
    assert ann.catalog_name == "Arp 1"
    assert ann.object_type == "galaxy"
    assert ann.angular_size == pytest.approx(5.2)
    assert ann.simbad_id == "NGC 2857"
    assert ann.ra == pytest.approx(141.16, abs=0.01)
    assert ann.dec == pytest.approx(49.36, abs=0.01)


def test_vii192_missing_size_still_parses():
    table = _vii192_table([["1", "NGC 2857", "09 24 38", "+49 21.4", float("nan")]])
    wcs = _wcs_at(141.16, 49.36)
    ann = _vii192_row_to_annotation(table[0], wcs, None)
    assert ann is not None
    assert ann.angular_size is None


def test_vii192_missing_arp_number_returns_none():
    table = _vii192_table([["", "NGC 2857", "09 24 38", "+49 21.4", 5.2]])
    wcs = _wcs_at(141.16, 49.36)
    assert _vii192_row_to_annotation(table[0], wcs, None) is None


def test_vii192_object_outside_field_is_dropped():
    table = _vii192_table([["1", "NGC 2857", "09 24 38", "+49 21.4", 5.2]])
    wcs = _wcs_at(56.75, 24.12)  # Pleiades field -- nowhere near Arp 1
    assert _vii192_row_to_annotation(table[0], wcs, None) is None


def test_vii192_malformed_coordinates_return_none_not_crash():
    table = _vii192_table([["1", "NGC 2857", "not-a-coord", "+49 21.4", 5.2]])
    wcs = _wcs_at(141.16, 49.36)
    assert _vii192_row_to_annotation(table[0], wcs, None) is None


# --- VII/213 (Hickson, groups sub-table) -- real row from a live query near HCG 1. ----

_VII213_COLUMNS = ["HCG", "AngSize", "_RA.icrs", "_DE.icrs"]


def _vii213_table(rows: list[list]) -> Table:
    return Table(rows=rows, names=_VII213_COLUMNS, dtype=[str, "float32", str, str])


def test_vii213_real_row_parses_as_hickson():
    table = _vii213_table([["1", 2.9, "00 26 00.2", "+25 43 05"]])
    wcs = _wcs_at(6.5, 25.72)
    ann = _vii213_row_to_annotation(table[0], wcs, None)
    assert ann is not None
    assert ann.catalog == "hickson"
    assert ann.catalog_name == "HCG 1"
    assert ann.object_type == "galaxy group"
    assert ann.angular_size == pytest.approx(2.9)
    assert ann.ra == pytest.approx(6.5, abs=0.01)
    assert ann.dec == pytest.approx(25.72, abs=0.01)


def test_vii213_missing_hcg_number_returns_none():
    table = _vii213_table([["", 2.9, "00 26 00.2", "+25 43 05"]])
    wcs = _wcs_at(6.5, 25.72)
    assert _vii213_row_to_annotation(table[0], wcs, None) is None


def test_vii213_object_outside_field_is_dropped():
    table = _vii213_table([["1", 2.9, "00 26 00.2", "+25 43 05"]])
    wcs = _wcs_at(141.16, 49.36)  # Arp 1 field -- nowhere near HCG 1
    assert _vii213_row_to_annotation(table[0], wcs, None) is None


def test_vii213_malformed_coordinates_return_none_not_crash():
    table = _vii213_table([["1", 2.9, "not-a-coord", "+25 43 05"]])
    wcs = _wcs_at(6.5, 25.72)
    assert _vii213_row_to_annotation(table[0], wcs, None) is None


# --- VII/272 (Green 2014, SNRs) -- real row from a live query near Sgr A East. --------
# --- Confirmed live on SIMBAD: the bare designation resolves to the unrelated ---------
# --- Galactic Center region marker -- the catalog's own "SNR " prefix is required. ----

_VII272_COLUMNS = ["SNR", "RAJ2000", "DEJ2000", "Dmaj", "Names"]


def _vii272_table(rows: list[list]) -> Table:
    return Table(rows=rows, names=_VII272_COLUMNS, dtype=[str, str, str, "float32", str])


def test_vii272_real_row_parses_as_snr():
    table = _vii272_table([["G000.0+00.0", "17 45 44", "-29 00", 3.5, "Sgr A East"]])
    wcs = _wcs_at(266.4333, -29.0)
    ann = _vii272_row_to_annotation(table[0], wcs, None)
    assert ann is not None
    assert ann.catalog == "snr"
    assert ann.catalog_name == "SNR G000.0+00.0"
    assert ann.object_type == "supernova remnant"
    assert ann.angular_size == pytest.approx(3.5)
    assert ann.common_name == "Sgr A East"
    assert ann.ra == pytest.approx(266.4333, abs=0.01)
    assert ann.dec == pytest.approx(-29.0, abs=0.01)


def test_vii272_missing_common_name_still_parses():
    table = _vii272_table([["G000.3+00.0", "17 46 15", "-28 38", 15.0, ""]])
    wcs = _wcs_at(266.5625, -28.633)
    ann = _vii272_row_to_annotation(table[0], wcs, None)
    assert ann is not None
    assert ann.catalog_name == "SNR G000.3+00.0"
    assert ann.common_name is None


def test_vii272_missing_designation_returns_none():
    table = _vii272_table([["", "17 45 44", "-29 00", 3.5, "Sgr A East"]])
    wcs = _wcs_at(266.4333, -29.0)
    assert _vii272_row_to_annotation(table[0], wcs, None) is None


def test_vii272_object_outside_field_is_dropped():
    table = _vii272_table([["G000.0+00.0", "17 45 44", "-29 00", 3.5, "Sgr A East"]])
    wcs = _wcs_at(6.5, 25.72)  # HCG 1 field -- nowhere near Sgr A East
    assert _vii272_row_to_annotation(table[0], wcs, None) is None


def test_vii272_malformed_coordinates_return_none_not_crash():
    table = _vii272_table([["G000.0+00.0", "not-a-coord", "-29 00", 3.5, "Sgr A East"]])
    wcs = _wcs_at(266.4333, -29.0)
    assert _vii272_row_to_annotation(table[0], wcs, None) is None


# --- V/163 (HASH), filtered to Abell-numbered rows -- real row from a live query -------
# --- (Abell 39). RAJ2000/DEJ2000 are already plain decimal degrees; MajDiam is in ------
# --- *arcsec*, unlike every other angular-size column in this module (all arcmin), -----
# --- confirmed live via VizieR column units metadata. Confirmed live on SIMBAD: bare ---
# --- "Abell <n>" collides with Abell's own galaxy-cluster catalog for every number -----
# --- tried -- "PN A66 <n>" is the real, reliable identifier, used as simbad_id. --------

_V163_COLUMNS = ["Name", "RAJ2000", "DEJ2000", "MajDiam"]


def _v163_table(rows: list[list]) -> Table:
    return Table(rows=rows, names=_V163_COLUMNS, dtype=[str, "float64", "float64", "float32"])


def test_v163_real_row_parses_as_abell():
    table = _v163_table([["Abell 39", 246.89056, 27.90929, 162.0]])
    wcs = _wcs_at(246.89056, 27.90929)
    ann = _v163_row_to_annotation(table[0], wcs, None)
    assert ann is not None
    assert ann.catalog == "abell"
    assert ann.catalog_name == "Abell 39"
    assert ann.object_type == "planetary nebula"
    assert ann.simbad_id == "PN A66 39"
    # 162 arcsec / 60 = 2.7 arcmin
    assert ann.angular_size == pytest.approx(2.7)
    assert ann.ra == pytest.approx(246.89056)
    assert ann.dec == pytest.approx(27.90929)


def test_v163_non_abell_row_returns_none():
    # HASH's own name for most rows -- e.g. real neighboring row "K 6-4" -- must be
    # silently discarded, same pattern as VII/118's post-parse Messier/NGC/IC split.
    table = _v163_table([["K 6-4", 264.42886, -27.81932, 6.9]])
    wcs = _wcs_at(264.42886, -27.81932)
    assert _v163_row_to_annotation(table[0], wcs, None) is None


def test_v163_missing_maj_diam_still_parses():
    table = _v163_table([["Abell 39", 246.89056, 27.90929, float("nan")]])
    wcs = _wcs_at(246.89056, 27.90929)
    ann = _v163_row_to_annotation(table[0], wcs, None)
    assert ann is not None
    assert ann.angular_size is None


def test_v163_object_outside_field_is_dropped():
    table = _v163_table([["Abell 39", 246.89056, 27.90929, 162.0]])
    wcs = _wcs_at(6.5, 25.72)  # HCG 1 field -- nowhere near Abell 39
    assert _v163_row_to_annotation(table[0], wcs, None) is None


def test_v163_malformed_coordinates_return_none_not_crash():
    table = Table(
        rows=[["Abell 39", "not-a-number", 27.90929, 162.0]],
        names=_V163_COLUMNS, dtype=[str, str, "float64", "float32"],
    )
    wcs = _wcs_at(246.89056, 27.90929)
    assert _v163_row_to_annotation(table[0], wcs, None) is None


# --- Galaxy-shape enrichment (SGA2020 primary, VII/237/HyperLeda fallback) ------------
# --- Real rows from live queries -- see catalogs.py's galaxy-shape-enrichment section ---
# --- for why these two, and their exact schema quirks (SGA2020's RA/Dec/D26/b-a are ------
# --- already plain decimal/linear; VII/237's are sexagesimal strings and log-scaled). ----

_SGA2020_COLUMNS = ["RAJ2000", "DEJ2000", "D26", "b/a", "PA"]


def _sga2020_table(rows: list[list]) -> Table:
    return Table(rows=rows, names=_SGA2020_COLUMNS, dtype=["float64", "float64", "float32", "float32", "float32"])


def test_sga2020_real_row_parses_as_a_galaxy_shape():
    # PGC1283207, live-queried near RA 228.377/DE 5.423.
    table = _sga2020_table([[228.3770804, 5.4231914, 0.4947, 0.5457, 158.2]])
    shape = _sga2020_row_to_shape(table[0])
    assert shape is not None
    assert shape.ra == pytest.approx(228.3770804)
    assert shape.dec == pytest.approx(5.4231914)
    assert shape.major_arcmin == pytest.approx(0.4947)
    assert shape.minor_arcmin == pytest.approx(0.4947 * 0.5457)
    assert shape.pa_deg == pytest.approx(158.2)


def test_sga2020_missing_field_returns_none():
    table = _sga2020_table([[228.3770804, 5.4231914, float("nan"), 0.5457, 158.2]])
    assert _sga2020_row_to_shape(table[0]) is None


_VII237_COLUMNS = ["RAJ2000", "DEJ2000", "logD25", "logR25", "PA"]


def _vii237_table(rows: list[list]) -> Table:
    return Table(rows=rows, names=_VII237_COLUMNS, dtype=[str, str, "float32", "float32", "float32"])


def test_vii237_real_row_parses_as_a_galaxy_shape():
    # PGC2555 (M32/NGC221), live-queried near M31.
    table = _vii237_table([["00 42 41.8", "+40 51 58", 1.96, 0.16, 170.0]])
    shape = _vii237_row_to_shape(table[0])
    assert shape is not None
    assert shape.ra == pytest.approx(10.674167, abs=1e-4)
    assert shape.dec == pytest.approx(40.866111, abs=1e-4)
    # 10**1.96 / 10
    assert shape.major_arcmin == pytest.approx(9.120108, abs=1e-4)
    # major / 10**0.16
    assert shape.minor_arcmin == pytest.approx(6.309573, abs=1e-4)
    assert shape.pa_deg == pytest.approx(170.0)


def test_vii237_malformed_coordinates_return_none_not_crash():
    table = _vii237_table([["not-a-coord", "+40 51 58", 1.96, 0.16, 170.0]])
    assert _vii237_row_to_shape(table[0]) is None


def test_vii237_missing_field_returns_none():
    table = _vii237_table([["00 42 41.8", "+40 51 58", float("nan"), 0.16, 170.0]])
    assert _vii237_row_to_shape(table[0]) is None


def test_nearest_galaxy_shape_matches_within_radius():
    shapes = [_GalaxyShape(ra=10.68, dec=41.27, major_arcmin=5.0, minor_arcmin=3.0, pa_deg=30.0)]
    match = _nearest_galaxy_shape(10.6801, 41.2701, shapes)
    assert match is shapes[0]


def test_nearest_galaxy_shape_ignores_a_match_outside_the_radius():
    shapes = [_GalaxyShape(ra=10.68, dec=41.27, major_arcmin=5.0, minor_arcmin=3.0, pa_deg=30.0)]
    assert _nearest_galaxy_shape(10.68, 42.0, shapes) is None  # ~44' away -- way outside 30"


def test_nearest_galaxy_shape_picks_the_closest_of_several_candidates():
    shapes = [
        _GalaxyShape(ra=10.6850, dec=41.27, major_arcmin=5.0, minor_arcmin=3.0, pa_deg=0.0),  # farther
        _GalaxyShape(ra=10.6801, dec=41.27, major_arcmin=5.0, minor_arcmin=3.0, pa_deg=90.0),  # closer
    ]
    match = _nearest_galaxy_shape(10.68, 41.27, shapes)
    assert match is shapes[1]


def test_nearest_galaxy_shape_empty_list_returns_none():
    assert _nearest_galaxy_shape(10.68, 41.27, []) is None


# --- _position_angle_to_screen_rotation_deg -------------------------------------------
# --- Verified live against a real (unrotated and rotated) synthetic WCS during ---------
# --- development: for a plain north-up frame, PA=0 (pointing north, i.e. straight up ---
# --- on screen) must rotate the ellipse's normally-horizontal wide axis to vertical -----
# --- (+-90 degrees), and PA=90 (pointing east, i.e. horizontal in a standard-oriented ---
# --- image) needs no rotation at all (0 mod 180). ---------------------------------------


def test_position_angle_north_becomes_vertical_on_an_unrotated_wcs():
    wcs = _wcs_at(180.0, 30.0)
    rotation = _position_angle_to_screen_rotation_deg(wcs, 180.0, 30.0, pa_deg=0.0)
    assert abs(abs(rotation) - 90.0) < 0.5


def test_position_angle_east_stays_horizontal_on_an_unrotated_wcs():
    wcs = _wcs_at(180.0, 30.0)
    rotation = _position_angle_to_screen_rotation_deg(wcs, 180.0, 30.0, pa_deg=90.0)
    # 0 mod 180 -- an ellipse rotated 0 or 180 degrees looks identical, so either is correct.
    normalized = abs(rotation) % 180.0
    assert normalized < 0.5 or normalized > 179.5


def test_position_angle_adapts_to_a_genuinely_rotated_wcs():
    """The whole point of projecting through world_to_pixel rather than using a flat
    formula: a real plate-solved frame can carry rotation, and the equivalent screen
    angle must shift by exactly that amount, not stay fixed at the unrotated-frame
    answer."""
    header = {
        "NAXIS": 2, "NAXIS1": _WIDTH, "NAXIS2": _HEIGHT,
        "CTYPE1": "RA---TAN", "CTYPE2": "DEC--TAN",
        "CRPIX1": _WIDTH / 2.0, "CRPIX2": _HEIGHT / 2.0,
        "CRVAL1": 180.0, "CRVAL2": 30.0,
        "CDELT1": -_PIXEL_SCALE_DEG, "CDELT2": _PIXEL_SCALE_DEG,
        "PC1_1": 0.8660254, "PC1_2": -0.5, "PC2_1": 0.5, "PC2_2": 0.8660254,  # 30 degree rotation
        "CUNIT1": "deg", "CUNIT2": "deg",
    }
    rotated_wcs = SirilWcs.from_header_dict(header, _WIDTH, _HEIGHT)
    unrotated_wcs = _wcs_at(180.0, 30.0)
    unrotated = _position_angle_to_screen_rotation_deg(unrotated_wcs, 180.0, 30.0, pa_deg=0.0)
    rotated = _position_angle_to_screen_rotation_deg(rotated_wcs, 180.0, 30.0, pa_deg=0.0)
    assert abs((rotated - unrotated) - 30.0) < 0.5 or abs((rotated - unrotated) + 30.0) < 0.5


# --- VizierProvider._enrich_galaxy_shapes -- full pipeline, fully mocked VizieR --------


def test_vizier_provider_enriches_a_galaxy_annotation_with_sga2020_shape(monkeypatch):
    """End-to-end: querying "messier" (VII/118) for a field containing a galaxy, with
    SGA2020 having a matching shape for it, must set that annotation's
    galaxy_major_axis_arcmin/galaxy_minor_axis_arcmin/galaxy_position_angle_screen_deg
    -- not touch marker_style (see renderer.py's compute_marker_geometry docstring on
    why that matters for catalog color)."""
    vii118 = _vii118_table(
        [["5194", "Gx", "13 29.9", "+47 12", "s", "CVn", "", "11.2", "8.4", "", "!!! Whirlpool Galaxy = M51"]]
    )
    sga2020 = _sga2020_table([[202.4696, 47.1953, 13.527, 0.8607, 28.39]])

    class _FakeVizier:
        def __init__(self, *args, **kwargs):
            pass

        def query_region(self, *args, **kwargs):
            catalog = kwargs.get("catalog")
            if catalog == "VII/118":
                return [vii118]
            if catalog == "J/ApJS/269/3/sga2020":
                return [sga2020]
            return []

    monkeypatch.setattr("astroquery.vizier.Vizier", _FakeVizier)
    monkeypatch.setattr(catalogs_module, "_vizier_available", True)

    wcs = _wcs_at(202.47, 47.20)
    provider = VizierProvider()
    results = provider.query(wcs, {"messier"})
    assert len(results) == 1
    ann = results[0]
    assert ann.catalog_name == "M51"
    assert ann.marker_style is None
    assert ann.galaxy_major_axis_arcmin == pytest.approx(13.527)
    assert ann.galaxy_minor_axis_arcmin == pytest.approx(13.527 * 0.8607)
    assert ann.galaxy_position_angle_screen_deg is not None


def test_vizier_provider_falls_back_to_hyperleda_when_sga2020_has_no_match(monkeypatch):
    """The real, motivating case from GitHub issue #9's analysis: SGA2020 doesn't cover
    M31 at all (confirmed live), so VII/237/HyperLeda must still fill in a shape."""
    vii118 = _vii118_table(
        [["224", "Gx", "00 42.7", "+41 16", "s", "And", "", "178.0", "3.4", "", "!!! Andromeda Galaxy = M31"]]
    )
    vii237 = _vii237_table([["00 42 44.3", "+41 16 09", 2.83, 0.36, 35.0]])

    class _FakeVizier:
        def __init__(self, *args, **kwargs):
            pass

        def query_region(self, *args, **kwargs):
            catalog = kwargs.get("catalog")
            if catalog == "VII/118":
                return [vii118]
            if catalog == "J/ApJS/269/3/sga2020":
                return []  # confirmed live: SGA2020 has no M31 entry at all
            if catalog == "VII/237":
                return [vii237]
            return []

    monkeypatch.setattr("astroquery.vizier.Vizier", _FakeVizier)
    monkeypatch.setattr(catalogs_module, "_vizier_available", True)

    wcs = _wcs_at(10.68, 41.27)
    provider = VizierProvider()
    results = provider.query(wcs, {"messier"})
    assert len(results) == 1
    ann = results[0]
    assert ann.catalog_name == "M31"
    assert ann.galaxy_major_axis_arcmin is not None
    assert ann.galaxy_major_axis_arcmin == pytest.approx((10.0**2.83) / 10.0)


def test_vizier_provider_leaves_a_non_galaxy_untouched_by_shape_enrichment(monkeypatch):
    vii118 = _vii118_table(
        [["6989", "OC", "20 51.2", "+72 45", "s", "Cep", "", "16.0", "5.7", "", ""]]
    )

    class _FakeVizier:
        def __init__(self, *args, **kwargs):
            pass

        def query_region(self, *args, **kwargs):
            return [vii118] if kwargs.get("catalog") == "VII/118" else []

    monkeypatch.setattr("astroquery.vizier.Vizier", _FakeVizier)
    monkeypatch.setattr(catalogs_module, "_vizier_available", True)

    wcs = _wcs_at(312.8, 72.75)
    provider = VizierProvider()
    results = provider.query(wcs, {"ngc"})
    assert len(results) == 1
    assert results[0].galaxy_major_axis_arcmin is None


def test_vizier_provider_shape_enrichment_failure_does_not_break_the_base_fetch(monkeypatch):
    """Best-effort: the SGA2020/HyperLeda queries failing outright must never take down
    the base messier/ngc/ic catalog fetch that already succeeded."""
    vii118 = _vii118_table(
        [["5194", "Gx", "13 29.9", "+47 12", "s", "CVn", "", "11.2", "8.4", "", "!!! Whirlpool Galaxy = M51"]]
    )

    class _FakeVizier:
        def __init__(self, *args, **kwargs):
            pass

        def query_region(self, *args, **kwargs):
            catalog = kwargs.get("catalog")
            if catalog == "VII/118":
                return [vii118]
            raise RuntimeError("simulated network failure")

    monkeypatch.setattr("astroquery.vizier.Vizier", _FakeVizier)
    monkeypatch.setattr(catalogs_module, "_vizier_available", True)

    wcs = _wcs_at(202.47, 47.20)
    provider = VizierProvider()
    results = provider.query(wcs, {"messier"})
    assert len(results) == 1
    assert results[0].catalog_name == "M51"
    assert results[0].galaxy_major_axis_arcmin is None


# --- _run_with_hard_timeout ------------------------------------------------------------
# Regression tests for a real, two-part bug found while building and verifying this
# timeout: (1) astroquery's own `timeout=` setting on Vizier did not reliably bound a
# real hang, so calls needed an application-level hard timeout; (2) the first
# implementation of that hard timeout used concurrent.futures.ThreadPoolExecutor, whose
# worker threads are *non-daemon* -- so even though the calling code correctly stopped
# waiting after N seconds, the whole Python *process* still hung at interpreter
# shutdown until the abandoned network call finished (confirmed: multiple real runs
# took the full outer `timeout` kill instead of returning promptly). A plain daemon
# threading.Thread fixes this because Python does not wait for daemon threads at exit.

def test_run_with_hard_timeout_returns_fast_result_immediately():
    assert _run_with_hard_timeout(lambda: 42, timeout_seconds=5) == 42


def test_run_with_hard_timeout_reraises_the_real_exception():
    def _boom():
        raise ValueError("real failure")

    with pytest.raises(ValueError, match="real failure"):
        _run_with_hard_timeout(_boom, timeout_seconds=5)


def test_run_with_hard_timeout_bounds_a_slow_call():
    import time

    start = time.time()
    with pytest.raises(TimeoutError):
        _run_with_hard_timeout(lambda: time.sleep(30), timeout_seconds=0.3)
    elapsed = time.time() - start
    assert elapsed < 5.0, f"hard timeout did not bound the call (took {elapsed:.1f}s)"


def test_run_with_hard_timeout_does_not_block_process_exit_on_abandoned_thread():
    """The specific regression: calling this with a call that never returns must not
    leave anything that would block process/interpreter exit (i.e. the spawned thread
    must be a daemon thread)."""
    import threading
    import time

    before = {t.ident for t in threading.enumerate()}
    with pytest.raises(TimeoutError):
        _run_with_hard_timeout(lambda: time.sleep(30), timeout_seconds=0.2)
    new_threads = [t for t in threading.enumerate() if t.ident not in before]
    assert len(new_threads) == 1
    assert new_threads[0].daemon is True, "abandoned thread must be a daemon thread"

