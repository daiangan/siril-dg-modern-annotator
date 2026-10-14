"""Interactive star identification via SIMBAD cone search.

Provides coordinate-based resolution of stellar objects near clicked points,
querying SIMBAD for objects with stellar object types ("*") within a specified
angular radius.
"""

from __future__ import annotations

from dataclasses import dataclass

from .catalogs import _run_with_hard_timeout

# Search radius bounds based on native pixel scale
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
