"""LocalCsvProvider parses Siril's bundled CSV catalog schema correctly (brief #35),
using fixture files mirroring the real messier.csv/ngc.csv structure documented in
RESEARCH.md #8 (name,ra,dec,diameter,mag,alias)."""

from __future__ import annotations

from pathlib import Path

from siril_modern_annotator.annotation.catalogs import (
    CatalogProvider,
    CompositeProvider,
    LocalCsvProvider,
    bayer_designation_to_greek,
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

    def __init__(self, catalog: str, catalog_name: str, ra: float, dec: float):
        self._ann = Annotation(
            catalog=catalog, catalog_name=catalog_name, ra=ra, dec=dec,
            image_x=0.0, image_y=0.0, priority=0,
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
