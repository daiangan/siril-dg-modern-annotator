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
    _vii220a_row_to_annotation,
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

