"""LocalCsvProvider parses Siril's bundled CSV catalog schema correctly (brief #35),
using fixture files mirroring the real messier.csv/ngc.csv structure documented in
RESEARCH.md #8 (name,ra,dec,diameter,mag,alias)."""

from __future__ import annotations

from pathlib import Path

import pytest

from siril_modern_annotator.annotation.catalogs import (
    CatalogProvider,
    CompositeProvider,
    LocalCsvProvider,
    GumProvider,
    RcwCorrectedPositionProvider,
    Sh2CorrectedPositionProvider,
    bayer_designation_to_greek,
    count_local_catalog_entries,
)
from siril_modern_annotator.annotation.models import Annotation
from siril_modern_annotator.annotation.wcs import SirilWcs

_FIXTURES = Path(__file__).parent / "fixtures" / "catalogue"
_WIDTH, _HEIGHT = 4000, 3000
_PIXEL_SCALE_DEG = 1.5 / 3600.0


def _wcs() -> SirilWcs:
    header = {
        "NAXIS": 2, "NAXIS1": _WIDTH, "NAXIS2": _HEIGHT,
        "CTYPE1": "RA---TAN", "CTYPE2": "DEC--TAN",
        "CRPIX1": _WIDTH / 2.0, "CRPIX2": _HEIGHT / 2.0,
        "CRVAL1": 310.0, "CRVAL2": 41.0,
        "CDELT1": -_PIXEL_SCALE_DEG, "CDELT2": _PIXEL_SCALE_DEG,
        "CUNIT1": "deg", "CUNIT2": "deg",
    }
    return SirilWcs.from_header_dict(header, _WIDTH, _HEIGHT)


def test_available_catalogs_reflects_present_files():
    provider = LocalCsvProvider(_FIXTURES)
    assert provider.available_catalogs == {"messier", "ngc", "bright_star"}


def test_bayer_designation_to_greek_known_prefixes():
    """Regression test: Siril's own annotation renders Bayer-lettered star names with
    the actual Greek letter (e.g. 'ξ Cyg'), but its bundled stars.csv stores the Latin-
    transliterated form ('ksi Cyg') -- confirmed by a real side-by-side screenshot."""
    assert bayer_designation_to_greek("ksi Cyg") == "ξ Cyg"
    assert bayer_designation_to_greek("alf Sco") == "α Sco"
    assert bayer_designation_to_greek("ome Oph") == "ω Oph"
    assert bayer_designation_to_greek("rho Oph A") == "ρ Oph A"


def test_bayer_designation_to_greek_leaves_unrecognized_names_unchanged():
    assert bayer_designation_to_greek("HD 12345") == "HD 12345"
    assert bayer_designation_to_greek("NGC7000") == "NGC7000"


def test_bright_star_catalog_names_converted_to_greek_letters():
    provider = LocalCsvProvider(_FIXTURES)
    results = provider.query(_wcs(), {"bright_star"})
    names = {a.catalog_name for a in results}
    assert "ξ Cyg" in names
    assert "α Sco" in names
    assert "ksi Cyg" not in names
    # A name with no recognized Bayer prefix must pass through unchanged.
    assert "HD-TEST-NOPREFIX" in names


def test_in_field_object_is_returned_out_of_field_is_not():
    provider = LocalCsvProvider(_FIXTURES)
    results = provider.query(_wcs(), {"messier"})
    names = {a.catalog_name for a in results}
    assert "M-TEST-CENTER" in names
    assert "M-TEST-FAR" not in names


def test_common_name_parsed_from_alias_first_token():
    provider = LocalCsvProvider(_FIXTURES)
    results = provider.query(_wcs(), {"messier"})
    center = next(a for a in results if a.catalog_name == "M-TEST-CENTER")
    assert center.common_name == "Test Nebula"
    assert center.magnitude == 8.4
    assert center.angular_size == 6.0


def test_missing_mag_and_diameter_handled_gracefully():
    provider = LocalCsvProvider(_FIXTURES)
    results = provider.query(_wcs(), {"ngc"})
    nomag = next(a for a in results if a.catalog_name == "NGC-TEST-NOMAG")
    assert nomag.magnitude is None
    assert nomag.angular_size is None


def test_magnitude_limit_filters_dim_objects():
    provider = LocalCsvProvider(_FIXTURES)
    results = provider.query(_wcs(), {"ngc"}, mag_limit=12.0)
    names = {a.catalog_name for a in results}
    assert "NGC-TEST-NEAR" in names
    assert "NGC-TEST-DIM" not in names  # mag 99.0 excluded


def test_native_pixel_coordinates_are_populated():
    # M-TEST-CENTER sits exactly at CRVAL. Compare against wcs.world_to_pixel() directly
    # rather than hardcoding index arithmetic -- SirilWcs works in *displayed* pixel
    # space (vertically flipped from the FITS header's raw convention; see test_wcs.py).
    wcs = _wcs()
    provider = LocalCsvProvider(_FIXTURES)
    results = provider.query(wcs, {"messier"})
    center = next(a for a in results if a.catalog_name == "M-TEST-CENTER")
    expected_x, expected_y = wcs.world_to_pixel(310.0, 41.0)
    assert abs(center.image_x - expected_x) < 1.0
    assert abs(center.image_y - expected_y) < 1.0


def test_local_csv_provider_accepts_a_custom_catalog_files_mapping(tmp_path):
    """Regression test for Siril's own persistent Astrometry > Annotate > Search
    Object list (user-DSO-catalogue.csv) -- lives in a different directory than the
    bundled messier/ngc/etc CSVs and under a catalog key ("user_dso") not in the
    default _LOCAL_CATALOG_FILES mapping, so LocalCsvProvider needs to accept an
    override rather than always reading the module-level default. Also confirms the
    real schema's extra pmra/pmdec/bmag columns (proper motion, blue magnitude -- none
    of which _parse_file reads) don't break parsing."""
    csv_path = tmp_path / "user-DSO-catalogue.csv"
    csv_path.write_text(
        "name,ra,dec,pmra,pmdec,mag,bmag,alias\n"
        "HD 191765,310.0,41.0,-4.99,-8.445,8.08,8.08,wr134\n"
    )
    provider = LocalCsvProvider(tmp_path, catalog_files={"user_dso": "user-DSO-catalogue.csv"})
    assert provider.available_catalogs == {"user_dso"}
    results = provider.query(_wcs(), {"user_dso"})
    assert len(results) == 1
    assert results[0].catalog == "user_dso"
    assert results[0].catalog_name == "HD 191765"


def test_local_csv_provider_with_custom_mapping_ignores_unmapped_catalogs(tmp_path):
    csv_path = tmp_path / "user-DSO-catalogue.csv"
    csv_path.write_text("name,ra,dec,mag,alias\nHD 191765,310.0,41.0,8.08,wr134\n")
    provider = LocalCsvProvider(tmp_path, catalog_files={"user_dso": "user-DSO-catalogue.csv"})
    # "messier" isn't in this custom mapping at all -- must not fall back to the
    # module-level default and must not error.
    assert provider.query(_wcs(), {"messier"}) == []


def test_count_local_catalog_entries_missing_file_returns_zero(tmp_path):
    assert count_local_catalog_entries(tmp_path, "user-DSO-catalogue.csv") == 0


def test_count_local_catalog_entries_counts_data_rows_not_header(tmp_path):
    csv_path = tmp_path / "user-DSO-catalogue.csv"
    csv_path.write_text(
        "name,ra,dec,mag,alias\n"
        "Object A,10.0,20.0,8.0,\n"
        "Object B,11.0,21.0,9.0,\n"
        "Object C,12.0,22.0,10.0,\n"
    )
    assert count_local_catalog_entries(tmp_path, "user-DSO-catalogue.csv") == 3


def test_composite_provider_merges_and_dedupes():
    provider = CompositeProvider(
        [LocalCsvProvider(_FIXTURES), LocalCsvProvider(_FIXTURES)]
    )
    results = provider.query(_wcs(), {"messier", "ngc"})
    names = [a.catalog_name for a in results]
    # Querying the same source twice should not double every object.
    assert len(names) == len(set(names))


class _StubProvider(CatalogProvider):
    """Minimal fixed-result provider for testing dedup priority independent of any
    real provider's iteration order."""

    def __init__(
        self, catalog: str, catalog_name: str, ra: float, dec: float,
        object_type: str | None = None, image_x: float = 0.0, image_y: float = 0.0,
        simbad_id: str | None = None,
        galaxy_major_axis_arcmin: float | None = None,
        galaxy_minor_axis_arcmin: float | None = None,
        galaxy_position_angle_screen_deg: float | None = None,
        common_name: str | None = None,
    ):
        self._ann = Annotation(
            catalog=catalog, catalog_name=catalog_name, ra=ra, dec=dec,
            image_x=image_x, image_y=image_y, priority=0,
            object_type=object_type if object_type is not None else catalog,
            simbad_id=simbad_id,
            galaxy_major_axis_arcmin=galaxy_major_axis_arcmin,
            galaxy_minor_axis_arcmin=galaxy_minor_axis_arcmin,
            galaxy_position_angle_screen_deg=galaxy_position_angle_screen_deg,
            common_name=common_name,
        )
        self._catalog = catalog

    @property
    def available_catalogs(self):
        return {self._catalog}

    def query(self, wcs, catalogs, mag_limit=None):
        return [self._ann] if self._catalog in catalogs else []


def test_dedupe_prefers_messier_name_regardless_of_arrival_order():
    """Regression test: the same physical object (M31 / NGC 224) was observed to
    display under whichever catalog's provider happened to return it first -- which
    depended on non-deterministic `set` iteration order across process runs, not on any
    real priority. The higher-priority catalog (messier) must win even when its
    provider's result arrives *after* the lower-priority one (ngc)."""
    ngc_first = CompositeProvider(
        [
            _StubProvider("ngc", "NGC224", 10.6847, 41.269),
            _StubProvider("messier", "M31", 10.6847, 41.269),
        ]
    )
    messier_first = CompositeProvider(
        [
            _StubProvider("messier", "M31", 10.6847, 41.269),
            _StubProvider("ngc", "NGC224", 10.6847, 41.269),
        ]
    )
    for provider in (ngc_first, messier_first):
        results = provider.query(_wcs(), {"messier", "ngc"})
        assert len(results) == 1
        assert results[0].catalog_name == "M31"
        assert results[0].catalog == "messier"


def test_dedupe_matches_same_designation_even_when_positions_disagree():
    """Regression test for a real bug: a user's screenshot showed NGC6989/NGC6996/
    NGC6997 each rendered twice. Root cause -- VII/118's own coordinates are low
    precision (RAB2000/DEB2000 give only ~0.1min RA / 1' Dec, up to ~30-60" of
    rounding error), so the same physical object sourced from VII/118 vs. Siril's own
    precise local CSV can legitimately fall outside a position-only dedup threshold.
    Same catalog + same normalized designation must be recognized as a duplicate
    regardless of how far apart their reported positions are."""
    ra1, dec1 = 313.130, 44.201  # VII/118-like, imprecise
    ra2, dec2 = 313.1057, 44.2036  # local-CSV-like, precise
    # Confirm the two stub positions really are outside the position-only 30" threshold,
    # so this test actually exercises the name-based fallback, not the old position path.
    assert abs(ra1 - ra2) * 3600 > 30 or abs(dec1 - dec2) * 3600 > 30

    provider = CompositeProvider(
        [
            _StubProvider("ngc", "NGC6989", ra1, dec1),
            _StubProvider("ngc", "NGC6989", ra2, dec2),
        ],
        dedupe_radius_arcsec=30.0,
    )

    results = provider.query(_wcs(), {"ngc"})
    assert len(results) == 1


def test_dedupe_keeps_the_first_arrivals_position_on_a_same_designation_tie():
    """Regression test for a real screenshot: NGC5471 (a compact HII knot in M101)
    rendered noticeably off its true position -- root cause was the same as
    test_dedupe_matches_same_designation_even_when_positions_disagree's bug, one level
    deeper. Once same-designation dedup correctly recognizes the two results as one
    object, it still has to pick *which* of the two positions to keep -- and it must
    keep the precise one (Siril's local CSV), not VII/118's coarse ~30-60" rounded
    coordinates. _catalog_provider() in main_window.py deliberately queries
    LocalCsvProvider before VizierProvider so the precise result is always the one
    dedup sees first; this test locks in that "first arrival wins the position tie"
    contract directly against CompositeProvider, independent of that wiring."""
    precise_ra, precise_dec = 211.12041, 54.3975  # Siril's own local ngc.csv for NGC5471
    imprecise_ra, imprecise_dec = 211.15, 54.4  # VII/118 RAB2000/DEB2000, rounded

    provider = CompositeProvider(
        [
            _StubProvider("ngc", "NGC5471", precise_ra, precise_dec, image_x=100.0, image_y=200.0),
            _StubProvider("ngc", "NGC5471", imprecise_ra, imprecise_dec, image_x=999.0, image_y=999.0),
        ],
        dedupe_radius_arcsec=30.0,
    )
    results = provider.query(_wcs(), {"ngc"})
    assert len(results) == 1
    assert (results[0].ra, results[0].dec) == (precise_ra, precise_dec)
    assert (results[0].image_x, results[0].image_y) == (100.0, 200.0)


def test_dedupe_fills_in_a_real_object_type_without_touching_the_kept_position():
    """LocalCsvProvider has no real object-type data and sets it to the catalog name
    itself as a placeholder (see LocalCsvProvider._parse_file); VII/118 does carry a
    real NGC2000.0 type. When Local's precise result wins the position tie (previous
    test), its placeholder type must still be backfilled from VII/118's richer record
    -- without that result's imprecise position leaking through."""
    provider = CompositeProvider(
        [
            _StubProvider(
                "ngc", "NGC5471", 211.12041, 54.3975,
                object_type="ngc", image_x=100.0, image_y=200.0,
            ),
            _StubProvider(
                "ngc", "NGC5471", 211.15, 54.4,
                object_type="HII region", image_x=999.0, image_y=999.0,
            ),
        ],
        dedupe_radius_arcsec=30.0,
    )
    results = provider.query(_wcs(), {"ngc"})
    assert len(results) == 1
    assert results[0].object_type == "HII region"
    assert (results[0].image_x, results[0].image_y) == (100.0, 200.0)


def test_dedupe_fills_in_simbad_id_from_the_vizier_cross_reference():
    """Regression test for a real report: Siril's own bundled stars.csv (read by
    LocalCsvProvider, which wins bright_star's display name via the position tie above)
    names this star "b01 Cyg" -- SIMBAD rejects that outright as an ambiguous/malformed
    identifier. VizieR's V/50 (via VizierProvider) cross-references the same star, at
    the same position, to "27 Cyg" / HD 191026 -- confirmed live on SIMBAD that both are
    identifiers for the one object. The dedup merge must carry that HD number over as
    simbad_id without touching the "b01 Cyg" display name LocalCsvProvider already won."""
    provider = CompositeProvider(
        [
            _StubProvider(
                "bright_star", "b01 Cyg", 301.590697, 35.972469,
                image_x=100.0, image_y=200.0,
            ),
            _StubProvider(
                "bright_star", "27 Cyg", 301.590697, 35.972469,
                image_x=999.0, image_y=999.0, simbad_id="HD 191026",
            ),
        ],
        dedupe_radius_arcsec=30.0,
    )
    results = provider.query(_wcs(), {"bright_star"})
    assert len(results) == 1
    assert results[0].catalog_name == "b01 Cyg"
    assert results[0].simbad_id == "HD 191026"
    assert (results[0].image_x, results[0].image_y) == (100.0, 200.0)


def test_dedupe_fills_in_galaxy_shape_from_the_vizier_cross_reference():
    """Regression test for a real report: a Messier galaxy (M31/M51/M101 all
    reproduced this) always has a messier.csv entry via LocalCsvProvider, which wins
    the same-catalog priority tie and survives dedup unchanged (see the comment on
    that tie above) -- but VizierProvider._enrich_galaxy_shapes only ever runs on
    VizierProvider's own freshly-parsed results, before CompositeProvider merges them.
    Without carrying galaxy_major_axis_arcmin/etc. across the same way simbad_id
    already is, the surviving LocalCsvProvider-sourced annotation silently lost its
    fitted-ellipse data -- confirmed live: every galaxy rendered as a plain circle
    regardless of which image was tested, since every one of them was a Messier
    object with a local CSV entry winning this exact tie."""
    provider = CompositeProvider(
        [
            _StubProvider(
                "messier", "M51", 202.4696, 47.1953, object_type="galaxy",
                image_x=100.0, image_y=200.0,
            ),
            _StubProvider(
                "messier", "M51", 202.4696, 47.1953, object_type="galaxy",
                image_x=999.0, image_y=999.0,
                galaxy_major_axis_arcmin=13.527, galaxy_minor_axis_arcmin=11.6427,
                galaxy_position_angle_screen_deg=-118.38,
            ),
        ],
        dedupe_radius_arcsec=30.0,
    )
    results = provider.query(_wcs(), {"messier"})
    assert len(results) == 1
    assert (results[0].image_x, results[0].image_y) == (100.0, 200.0)  # position tie unaffected
    assert results[0].galaxy_major_axis_arcmin == pytest.approx(13.527)
    assert results[0].galaxy_minor_axis_arcmin == pytest.approx(11.6427)
    assert results[0].galaxy_position_angle_screen_deg == pytest.approx(-118.38)


def test_dedupe_never_merges_a_star_with_a_nearby_nebula():
    """Regression test for a real screenshot comparison: a bright star sitting almost
    exactly at Sh2-9's cataloged position was silently dropped as a "duplicate" of the
    nebula, even though Siril's own annotator marks the star individually. A star and an
    extended object (nebula/cluster/galaxy) can legitimately share a position -- e.g. the
    star that illuminates/is embedded in the nebula -- without being the same object, so
    position-based dedup must never merge across those catalog kinds."""
    ra, dec = 246.75, -25.60  # same position for both stub results
    provider = CompositeProvider(
        [
            _StubProvider("bright_star", "sig Sco", ra, dec),
            _StubProvider("sh2", "Sh2-9", ra, dec),
        ],
        dedupe_radius_arcsec=30.0,
    )
    results = provider.query(_wcs(), {"bright_star", "sh2"})
    names = {a.catalog_name for a in results}
    assert names == {"sig Sco", "Sh2-9"}


def test_dedupe_never_merges_wr_with_a_nearby_nebula():
    """Same guard as bright_star above, real-world case: WR136 is the actual central
    star of the Crescent Nebula (NGC 6888) -- a real astrophotography target where the
    star and the nebula it's blowing into space legitimately share a position without
    being the same object. WR is deliberately excluded from _DEEP_SKY_CATALOGS for
    exactly this reason (see _iii215_row_to_annotation's docstring)."""
    ra, dec = 302.559, 38.355  # WR136/NGC 6888's real position
    provider = CompositeProvider(
        [
            _StubProvider("wr", "WR 136", ra, dec, object_type="Wolf-Rayet star"),
            _StubProvider("ngc", "NGC6888", ra, dec, object_type="nebula"),
        ],
        dedupe_radius_arcsec=30.0,
    )
    results = provider.query(_wcs(), {"wr", "ngc"})
    names = {a.catalog_name for a in results}
    assert names == {"WR 136", "NGC6888"}


def test_dedupe_still_merges_same_position_across_deep_sky_catalogs():
    """Deep-sky catalogs (messier/ngc/ic/sh2/ldn) legitimately cross-reference the same
    physical object under different designations (e.g. M42 == NGC1976), so position-
    based dedup across those catalogs must still work -- only star-vs-extended-object
    merging is disallowed."""
    ra, dec = 83.82, -5.39
    provider = CompositeProvider(
        [
            _StubProvider("ngc", "NGC1976", ra, dec),
            _StubProvider("messier", "M42", ra, dec),
        ],
        dedupe_radius_arcsec=30.0,
    )
    results = provider.query(_wcs(), {"messier", "ngc"})
    assert len(results) == 1
    assert results[0].catalog_name == "M42"


def test_dedupe_merges_rcw_with_its_ngc_cross_reference():
    """Per explicit user decision: RCW joins the same cross-catalog dedup class as
    messier/ngc/ic/sh2/ldn/barnard/lbn -- confirmed live on SIMBAD that RCW 53 and
    NGC 3372 (the Carina Nebula) are the same object, so both catalogs reporting it at
    the same position must merge into one marker, not two."""
    ra, dec = 160.48, -59.76
    provider = CompositeProvider(
        [
            _StubProvider("rcw", "RCW 53", ra, dec),
            _StubProvider("ngc", "NGC3372", ra, dec),
        ],
        dedupe_radius_arcsec=30.0,
    )
    results = provider.query(_wcs(), {"rcw", "ngc"})
    assert len(results) == 1


def test_dedupe_never_merges_vdb_with_a_same_position_ngc_entry():
    """Per explicit user decision: unlike RCW, vdB is deliberately left out of the
    cross-catalog dedup class -- each row is positioned at the *illuminating star*
    (see _vii21_row_to_annotation's docstring), a different physical anchor than an
    NGC/IC centroid for the same region, so merging them by position alone would be
    wrong more often than right."""
    ra, dec = 56.75, 24.12
    provider = CompositeProvider(
        [
            _StubProvider("vdb", "vdB 20", ra, dec),
            _StubProvider("ngc", "NGC1432", ra, dec),
        ],
        dedupe_radius_arcsec=30.0,
    )
    results = provider.query(_wcs(), {"vdb", "ngc"})
    names = {a.catalog_name for a in results}
    assert names == {"vdB 20", "NGC1432"}


def test_dedupe_merges_arp_with_its_ngc_cross_reference_ngc_name_wins():
    """Every Arp entry already carries a real NGC/UGC/MCG cross-reference (see
    _vii192_row_to_annotation) -- Arp 1 is NGC 2857. When both catalogs report the
    same galaxy at the same position, they must merge into one marker, with NGC's
    more commonly cited name winning (ngc's priority is lower than arp's)."""
    ra, dec = 141.16, 49.36
    provider = CompositeProvider(
        [
            _StubProvider("arp", "Arp 1", ra, dec, object_type="galaxy"),
            _StubProvider("ngc", "NGC2857", ra, dec, object_type="galaxy"),
        ],
        dedupe_radius_arcsec=30.0,
    )
    results = provider.query(_wcs(), {"arp", "ngc"})
    assert len(results) == 1
    assert results[0].catalog_name == "NGC2857"


def test_dedupe_never_merges_hickson_with_a_same_position_ngc_entry():
    """Per the same reasoning already established for vdB: a Hickson compact group and
    one of its individual member galaxies are conceptually different objects even
    when their catalog positions are close, so Hickson is deliberately left out of
    the cross-catalog dedup class."""
    ra, dec = 6.5, 25.72
    provider = CompositeProvider(
        [
            _StubProvider("hickson", "HCG 1", ra, dec, object_type="galaxy group"),
            _StubProvider("ngc", "NGC7803", ra, dec, object_type="galaxy"),
        ],
        dedupe_radius_arcsec=30.0,
    )
    results = provider.query(_wcs(), {"hickson", "ngc"})
    names = {a.catalog_name for a in results}
    assert names == {"HCG 1", "NGC7803"}


def test_dedupe_merges_snr_with_its_ngc_cross_reference():
    """Confirmed live on SIMBAD: many cataloged SNRs share a position with an existing
    Messier/NGC object (e.g. the Crab Nebula = M1 = NGC 1952) -- SNR joins the same
    cross-catalog dedup class as RCW/Gum/etc. so they merge into one marker, with the
    more commonly cited Messier/NGC name winning."""
    ra, dec = 83.63, 22.01  # M1/Crab Nebula's real position
    provider = CompositeProvider(
        [
            _StubProvider("snr", "SNR G184.6-05.8", ra, dec, object_type="supernova remnant"),
            _StubProvider("messier", "M1", ra, dec, object_type="nebula"),
        ],
        dedupe_radius_arcsec=30.0,
    )
    results = provider.query(_wcs(), {"snr", "messier"})
    assert len(results) == 1
    assert results[0].catalog_name == "M1"


def test_dedupe_merges_abell_with_a_same_position_ngc_entry():
    """Abell joins the same cross-catalog dedup class as SNR/RCW/etc. -- most of
    Abell's 86 planetary nebulae were previously uncatalogued, but the rare one that
    does coincide with an existing NGC/IC entry must still merge into one marker."""
    ra, dec = 246.89056, 27.90929  # Abell 39's real position
    provider = CompositeProvider(
        [
            _StubProvider("abell", "Abell 39", ra, dec, object_type="planetary nebula"),
            _StubProvider("ngc", "NGC-TEST", ra, dec, object_type="planetary nebula"),
        ],
        dedupe_radius_arcsec=30.0,
    )
    results = provider.query(_wcs(), {"abell", "ngc"})
    assert len(results) == 1
    assert results[0].catalog_name == "NGC-TEST"


def test_abell_stays_standalone_with_no_overlapping_catalog():
    ra, dec = 246.89056, 27.90929
    provider = CompositeProvider(
        [_StubProvider("abell", "Abell 39", ra, dec, object_type="planetary nebula")],
        dedupe_radius_arcsec=30.0,
    )
    results = provider.query(_wcs(), {"abell"})
    assert len(results) == 1
    assert results[0].catalog_name == "Abell 39"


class _PositionedStubProvider(CatalogProvider):
    """Returns one fixed-position Annotation, bypassing real WCS projection -- lets a
    test place a result exactly in-frame or out-of-frame without needing real sky
    coordinates that happen to land there."""

    def __init__(self, catalog: str, image_x: float, image_y: float):
        self._ann = Annotation(
            catalog=catalog, catalog_name=f"TEST-{image_x}-{image_y}", ra=0.0, dec=0.0,
            image_x=image_x, image_y=image_y,
        )
        self._catalog = catalog

    @property
    def available_catalogs(self):
        return {self._catalog}

    def query(self, wcs, catalogs, mag_limit=None):
        return [self._ann] if self._catalog in catalogs else []


def test_result_inside_native_frame_stays_enabled():
    """Regression test for a real screenshot: objects whose marker sits outside the
    actual photographed frame were rendering in the blank space beside the image, and
    then again in exports. Individual providers query with a small FOV margin so a
    just-off-frame label can still be dragged into view deliberately, but the *default*
    should be to hide anything that isn't actually within the frame."""
    provider = CompositeProvider([_PositionedStubProvider("messier", _WIDTH / 2, _HEIGHT / 2)])
    results = provider.query(_wcs(), {"messier"})
    assert len(results) == 1
    assert results[0].enabled is True


def test_result_outside_native_frame_is_disabled_by_default():
    provider = CompositeProvider([_PositionedStubProvider("messier", -50.0, _HEIGHT / 2)])
    results = provider.query(_wcs(), {"messier"})
    assert len(results) == 1, "out-of-frame results must still be returned (toggleable), not silently dropped"
    assert results[0].enabled is False


def test_result_exactly_on_native_boundary_is_in_frame():
    provider = CompositeProvider([_PositionedStubProvider("messier", 0.0, 0.0)])
    results = provider.query(_wcs(), {"messier"})
    assert results[0].enabled is True


def test_result_just_past_native_boundary_is_disabled():
    provider = CompositeProvider([_PositionedStubProvider("messier", float(_WIDTH), _HEIGHT / 2)])
    results = provider.query(_wcs(), {"messier"})
    assert results[0].enabled is False


# ------------------------------------------------ Sh2CorrectedPositionProvider ----
# Explicitly experimental (GitHub issue #10 + explicit user request) -- see
# sh2_corrected_positions.py's own docstring for the confirmed ~15-16 arcmin
# position-error rationale this addresses.


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


def test_sh2_corrected_position_provider_only_returns_sh2():
    provider = Sh2CorrectedPositionProvider()
    assert provider.available_catalogs == {"sh2"}


def test_sh2_corrected_position_provider_ignores_other_catalogs():
    provider = Sh2CorrectedPositionProvider()
    assert provider.query(_wcs_at(271.0935, -24.331806), {"messier"}) == []


def test_sh2_corrected_position_provider_returns_sh2_25_near_the_lagoon_nebula():
    # 271.0935, -24.331806 is CORRECTED_SH2_POSITIONS[25] -- the corrected coordinate
    # itself, live-extracted from the Sharpless column of Kevin Jardine's Integrated
    # HII Regions catalog (see sh2_corrected_positions.py).
    provider = Sh2CorrectedPositionProvider()
    results = provider.query(_wcs_at(271.0935, -24.331806), {"sh2"})
    names = {a.catalog_name for a in results}
    assert "Sh2-25" in names
    sh2_25 = next(a for a in results if a.catalog_name == "Sh2-25")
    assert sh2_25.catalog == "sh2"
    assert sh2_25.ra == pytest.approx(271.0935)
    assert sh2_25.dec == pytest.approx(-24.331806)


def test_sh2_corrected_position_provider_object_far_from_frame_is_excluded():
    provider = Sh2CorrectedPositionProvider()
    results = provider.query(_wcs_at(0.0, 0.0), {"sh2"})
    assert not any(a.catalog_name == "Sh2-25" for a in results)


def test_sh2_corrected_position_wins_the_dedup_tie_over_the_old_source():
    """Regression test for the actual mechanism this fix relies on: when the corrected
    provider and the old (buggy) source both report "Sh2-25" at different positions,
    CompositeProvider._dedupe's same-designation path must merge them into one marker
    at the *corrected* provider's position -- exactly the "first arrival wins" rule
    already established for local-vs-VizieR precision (see _catalog_provider's own
    comment), since Sh2CorrectedPositionProvider is registered first there."""
    corrected_ra, corrected_dec = 271.0935, -24.331806  # CORRECTED_SH2_POSITIONS[25]
    old_buggy_ra, old_buggy_dec = 271.358458, -24.394283  # Siril's own sh2.csv / VII/20 value
    provider = CompositeProvider(
        [
            Sh2CorrectedPositionProvider(),
            _StubProvider("sh2", "Sh2-25", old_buggy_ra, old_buggy_dec, image_x=999.0, image_y=999.0),
        ],
        dedupe_radius_arcsec=30.0,
    )
    results = provider.query(_wcs_at(corrected_ra, corrected_dec), {"sh2"})
    matches = [a for a in results if a.catalog_name == "Sh2-25"]
    assert len(matches) == 1
    assert matches[0].ra == pytest.approx(corrected_ra)
    assert matches[0].dec == pytest.approx(corrected_dec)


def test_sh2_corrected_position_provider_covers_all_312_sharpless_tagged_objects():
    from siril_modern_annotator.annotation.sh2_corrected_positions import CORRECTED_SH2_POSITIONS

    assert len(CORRECTED_SH2_POSITIONS) == 312
    assert set(CORRECTED_SH2_POSITIONS) <= set(range(1, 314))  # Sh2 numbers run 1-313


# ------------------------------------------------ RcwCorrectedPositionProvider ----
# Confirmed real report: a live test with RCW107 showed its marker landing visibly off
# the actual object -- VII/216 (this app's only other RCW source) stores positions at
# only minute/arcminute precision, ~2.5 arcmin off for RCW107 specifically. Mirrors
# Sh2CorrectedPositionProvider exactly; see rcw_corrected_positions.py's own docstring.


def test_rcw_corrected_position_provider_only_returns_rcw():
    provider = RcwCorrectedPositionProvider()
    assert provider.available_catalogs == {"rcw"}


def test_rcw_corrected_position_provider_ignores_other_catalogs():
    provider = RcwCorrectedPositionProvider()
    assert provider.query(_wcs_at(248.452458, -48.112861), {"messier"}) == []


def test_rcw_corrected_position_provider_returns_rcw_107_near_hd_148937():
    # 248.452458, -48.112861 is CORRECTED_RCW_POSITIONS[107] -- the corrected
    # coordinate itself, live-extracted from the rcw column of Kevin Jardine's
    # Integrated HII Regions catalog (see rcw_corrected_positions.py). Confirmed live
    # against SIMBAD's own resolution of "RCW 107" to HD 148937 (16 33 52.39,
    # -48 06 40.5) -- within ~6" of this corrected value, vs. VII/216's own ~2.5
    # arcmin-off position for the same object.
    provider = RcwCorrectedPositionProvider()
    results = provider.query(_wcs_at(248.452458, -48.112861), {"rcw"})
    names = {a.catalog_name for a in results}
    assert "RCW 107" in names
    rcw_107 = next(a for a in results if a.catalog_name == "RCW 107")
    assert rcw_107.catalog == "rcw"
    assert rcw_107.ra == pytest.approx(248.452458)
    assert rcw_107.dec == pytest.approx(-48.112861)


def test_rcw_corrected_position_provider_object_far_from_frame_is_excluded():
    provider = RcwCorrectedPositionProvider()
    results = provider.query(_wcs_at(0.0, 0.0), {"rcw"})
    assert not any(a.catalog_name == "RCW 107" for a in results)


def test_rcw_corrected_position_wins_the_dedup_tie_over_the_old_source():
    """Regression test for the actual mechanism this fix relies on: when the corrected
    provider and the old (VII/216) source both report "RCW 107" at different
    positions, CompositeProvider._dedupe's same-designation path must merge them into
    one marker at the *corrected* provider's position -- same "first arrival wins" rule
    already established for Sh2, since RcwCorrectedPositionProvider is registered first
    in _catalog_provider."""
    corrected_ra, corrected_dec = 248.452458, -48.112861  # CORRECTED_RCW_POSITIONS[107]
    old_vii216_ra, old_vii216_dec = 248.378333, -48.154722  # VII/216's own value
    provider = CompositeProvider(
        [
            RcwCorrectedPositionProvider(),
            _StubProvider("rcw", "RCW 107", old_vii216_ra, old_vii216_dec, image_x=999.0, image_y=999.0),
        ],
        dedupe_radius_arcsec=30.0,
    )
    results = provider.query(_wcs_at(corrected_ra, corrected_dec), {"rcw"})
    matches = [a for a in results if a.catalog_name == "RCW 107"]
    assert len(matches) == 1
    assert matches[0].ra == pytest.approx(corrected_ra)
    assert matches[0].dec == pytest.approx(corrected_dec)


def test_rcw_corrected_position_provider_covers_182_numeric_rcw_designations():
    from siril_modern_annotator.annotation.rcw_corrected_positions import CORRECTED_RCW_POSITIONS

    assert len(CORRECTED_RCW_POSITIONS) == 182
    assert set(CORRECTED_RCW_POSITIONS) <= set(range(1, 183))  # RCW numbers run 1-182


# ------------------------------------------ CompositeProvider._apply_common_names ----
# Per explicit user request: popular names (e.g. "Crescent Nebula" for NGC6888) for
# well-known objects across every catalog, not just Messier (which only ever had one
# because Siril's own messier.csv bundles a name in its "alias" column) -- see
# common_names.py's own docstring for the full source (Wikidata, CC0) and the real data
# cleaning that went into building COMMON_NAMES.


def test_apply_common_names_backfills_a_known_object():
    provider = CompositeProvider([_StubProvider("ngc", "NGC6888", 303.02709, 38.355)])
    results = provider.query(_wcs_at(303.02709, 38.355), {"ngc"})
    ngc6888 = next(a for a in results if a.catalog_name == "NGC6888")
    assert ngc6888.common_name == "Crescent Nebula"


def test_apply_common_names_leaves_an_unknown_object_alone():
    provider = CompositeProvider([_StubProvider("ngc", "NGC9999999", 10.0, 20.0)])
    results = provider.query(_wcs_at(10.0, 20.0), {"ngc"})
    assert results[0].common_name is None


def test_apply_common_names_never_overwrites_a_name_a_provider_already_set():
    """Non-clobbering, same precedent as every other cross-provider enrichment field --
    a provider's own common_name (e.g. Siril's messier.csv alias column) always wins."""
    provider = CompositeProvider(
        [_StubProvider("ngc", "NGC6888", 303.02709, 38.355, common_name="The Local Nickname")]
    )
    results = provider.query(_wcs_at(303.02709, 38.355), {"ngc"})
    assert results[0].common_name == "The Local Nickname"


def test_common_names_covers_examples_across_every_included_catalog():
    from siril_modern_annotator.annotation.common_names import COMMON_NAMES

    assert COMMON_NAMES["NGC6888"] == "Crescent Nebula"
    assert COMMON_NAMES["Sh2-101"] == "Tulip Nebula"
    assert COMMON_NAMES["M42"] == "Orion Nebula"
    assert COMMON_NAMES["IC1805"] == "Heart Nebula"
    assert COMMON_NAMES["B33"] == "Horsehead Nebula"
    assert COMMON_NAMES["RCW 53"] == "Carina Nebula"
    assert COMMON_NAMES["Arp 317"] == "Leo Triplet"
    assert COMMON_NAMES["HCG 92"] == "Stephan's Quintet"


# ------------------------------------------------------------------- GumProvider ----
# Per GitHub issue #10, same source as Sh2CorrectedPositionProvider above -- Gum
# (1955, southern HII regions) via Kevin Jardine's Integrated HII Regions catalog.


def test_gum_provider_only_returns_gum():
    provider = GumProvider()
    assert provider.available_catalogs == {"gum"}


def test_gum_provider_ignores_other_catalogs():
    provider = GumProvider()
    assert provider.query(_wcs_at(131.240583, -41.282917), {"messier"}) == []


def test_gum_provider_returns_gum_15():
    # 131.240583, -41.282917 is GUM_OBJECTS["Gum 15"] -- confirmed live on SIMBAD to
    # resolve correctly (a real HII region, unlike some letter-suffixed Gum names --
    # see gum_positions.py's own docstring).
    provider = GumProvider()
    results = provider.query(_wcs_at(131.240583, -41.282917), {"gum"})
    names = {a.catalog_name for a in results}
    assert "Gum 15" in names
    gum15 = next(a for a in results if a.catalog_name == "Gum 15")
    assert gum15.catalog == "gum"
    assert gum15.object_type == "nebula"
    assert gum15.ra == pytest.approx(131.240583)
    assert gum15.dec == pytest.approx(-41.282917)
    assert gum15.angular_size == pytest.approx(14.0926)


def test_gum_provider_object_far_from_frame_is_excluded():
    provider = GumProvider()
    results = provider.query(_wcs_at(0.0, 0.0), {"gum"})
    assert not any(a.catalog_name == "Gum 15" for a in results)


def test_gum_provider_covers_all_67_gum_tagged_objects():
    from siril_modern_annotator.annotation.gum_positions import GUM_OBJECTS

    assert len(GUM_OBJECTS) == 67
    assert "Gum nebula" in GUM_OBJECTS  # the giant Gum Nebula itself, not "Gum N"


def test_gum_merges_with_its_rcw_cross_reference():
    """Confirmed live before implementing: most Gum objects (47 of 67) already carry
    an RCW cross-reference for the same physical nebula in Jardine's own data --
    "gum" joins _DEEP_SKY_CATALOGS (same treatment RCW itself got) so those merge
    into one marker instead of drawing twice, with RCW's own name winning (RCW's
    priority is a lower number than Gum's, so it wins regardless of arrival order --
    unlike Sh2CorrectedPositionProvider's same-catalog tie above, this is a genuine
    cross-catalog priority decision, not an arrival-order one)."""
    ra, dec = 271.180792, -23.545944  # GUM_OBJECTS["Gum 74b"], cross-referenced to RCW 146b
    provider = CompositeProvider(
        [
            GumProvider(),
            _StubProvider("rcw", "RCW 146b", ra, dec),
        ],
        dedupe_radius_arcsec=30.0,
    )
    results = provider.query(_wcs_at(ra, dec), {"gum", "rcw"})
    names = {a.catalog_name for a in results}
    assert names == {"RCW 146b"}


def test_gum_does_not_merge_with_an_unrelated_nearby_star():
    """A star and an extended object can legitimately share a position without being
    the same object -- same guard _DEEP_SKY_CATALOGS already provides for every other
    deep-sky catalog here (see test_dedupe_never_merges_a_star_with_a_nearby_nebula)."""
    ra, dec = 131.240583, -41.282917
    provider = CompositeProvider(
        [
            GumProvider(),
            _StubProvider("bright_star", "Test Star", ra, dec),
        ],
        dedupe_radius_arcsec=30.0,
    )
    results = provider.query(_wcs_at(ra, dec), {"gum", "bright_star"})
    names = {a.catalog_name for a in results}
    assert names == {"Gum 15", "Test Star"}
