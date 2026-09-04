""""Identify Star" -- right-click a point on the canvas, resolve the nearest star-type
object(s) to it via SIMBAD's own coordinate search, per explicit user request: Siril's
own annotator (and this app's bright_star catalog, via Siril's bundled stars.csv) only
covers naked-eye-ish stars up to roughly magnitude ~10 -- there was no way to identify
or label anything fainter that Siril didn't already know about.

Deliberately its own module, not a CatalogProvider: CatalogProvider.query(wcs, catalogs,
mag_limit) answers "everything in this field for these named catalogs" -- this answers
"the closest few star-type objects to one specific sky point," a different shape of
question entirely, triggered by a single right-click rather than a whole-field fetch.

Uses astroquery.simbad.Simbad.query_region, confirmed live to return a table with
main_id/ra/dec/otype/V columns (or None when nothing is within the search radius).
Wrapped in _run_with_hard_timeout (see catalogs.py's own docstring on it) -- this exact
class of astroquery network call has a documented real hang in this codebase already
(the same reason VizierProvider's own SIMBAD-TAP name resolution was removed, see
VizierProvider's docstring); reusing the existing safeguard rather than risking that
again.

Star-type filtering keeps only rows whose otype contains "*" -- SIMBAD's own object-type
coding scheme puts every star-branch code somewhere in that family ("*", "**", "WR*",
"SB*", "V*", "PM*", ...), confirmed live against real objects, and confirmed live that
non-star types this app must exclude (open clusters "OpC", HII regions "HII", supernova
remnants "SNR") do not. Known, accepted gap: "Psr" (pulsar) has no asterisk and would be
excluded by this rule -- a real but rare edge case, not worth the added complexity of a
star-type allowlist for v1.
"""

from __future__ import annotations

from dataclasses import dataclass

# Search radius: a small, native-pixel-based click tolerance converted to arcsec via the
# image's own plate-solved pixel scale -- deliberately NOT derived from the user's own
# (customizable, unrelated) marker-radius style setting. Clamped so a very high-
# resolution image doesn't shrink the radius to a useless sub-arcsec sliver, and a very
# wide-field image doesn't balloon it into pulling in unrelated background stars.
_STAR_IDENTIFY_TOLERANCE_NATIVE_PX = 12.0
_STAR_IDENTIFY_MIN_RADIUS_ARCSEC = 3.0
_STAR_IDENTIFY_MAX_RADIUS_ARCSEC = 20.0


def default_radius_arcsec(arcsec_per_px: float) -> float:
    radius = _STAR_IDENTIFY_TOLERANCE_NATIVE_PX * arcsec_per_px
    return max(_STAR_IDENTIFY_MIN_RADIUS_ARCSEC, min(_STAR_IDENTIFY_MAX_RADIUS_ARCSEC, radius))


@dataclass(frozen=True)
class StarCandidate:
    simbad_id: str
    ra: float
    dec: float
    otype: str
    magnitude: float | None
    separation_arcsec: float


def identify_stars(ra: float, dec: float, radius_arcsec: float, max_candidates: int = 3) -> list[StarCandidate]:
    from astropy import units as u
    from astropy.coordinates import SkyCoord
    from astroquery.simbad import Simbad

    from .catalogs import _run_with_hard_timeout

    center = SkyCoord(ra=ra, dec=dec, unit=(u.deg, u.deg))
    custom = Simbad()
    custom.add_votable_fields("otype", "V")
    table = _run_with_hard_timeout(
        lambda: custom.query_region(center, radius=radius_arcsec * u.arcsec),
        timeout_seconds=15,
    )
    if table is None:
        return []

    candidates: list[StarCandidate] = []
    for row in table:
        otype = str(row["otype"]) if "otype" in table.colnames else ""
        if "*" not in otype:
            continue
        row_ra, row_dec = float(row["ra"]), float(row["dec"])
        separation = center.separation(SkyCoord(ra=row_ra, dec=row_dec, unit=(u.deg, u.deg))).arcsec
        magnitude = None
        if "V" in table.colnames:
            v = row["V"]
            try:
                if v is not None and str(v).strip() not in ("", "--", "nan", "masked"):
                    magnitude = float(v)
            except (TypeError, ValueError):
                magnitude = None
        candidates.append(
            StarCandidate(
                simbad_id=str(row["main_id"]),
                ra=row_ra,
                dec=row_dec,
                otype=otype,
                magnitude=magnitude,
                separation_arcsec=separation,
            )
        )

    candidates.sort(key=lambda c: c.separation_arcsec)
    return candidates[:max_candidates]
