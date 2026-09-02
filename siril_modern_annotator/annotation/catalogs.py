"""Catalog providers.

Per RESEARCH.md #8: sirilpy exposes no catalog/annotation API. Object data comes from
one of:

  - LocalCsvProvider:  reads Siril's own bundled CSV catalogs directly, located under
                        SirilInterface.get_siril_systemdatadir()/catalogue/. Fast,
                        offline, but no object-type field and sparse common names
                        outside Messier.
  - VizierProvider:     astroquery against VizieR (VII/118 Messier/NGC/IC, VII/20
                        Sharpless, VII/220A Barnard, V/50 bright stars) — mirrors the
                        proven query set used by
                        siril-scripts/utility/Svenesis-AnnotateImage.py. Primary/default
                        source; requires network. (Previously also resolved common
                        names via SIMBAD's TAP service; removed after real-world use
                        showed that service, and its only mirror, too unreliable to be
                        worth the complexity -- see VizierProvider's own docstring.)
  - CompositeProvider: merges results from multiple providers, de-duplicating by
                        angular proximity + name match.

Every provider implements the same CatalogProvider interface so new catalogs (Caldwell,
Abell, vdB, ...) can be added without touching GUI or rendering code (brief #13).
"""

from __future__ import annotations

import csv
import logging
import re
from abc import ABC, abstractmethod
from pathlib import Path

import numpy as np

from .models import Annotation, default_priority_for_catalog
from .wcs import SirilWcs

logger = logging.getLogger(__name__)

# name -> (filename, has_diameter, has_mag, has_alias)
_LOCAL_CATALOG_FILES: dict[str, str] = {
    "messier": "messier.csv",
    "ngc": "ngc.csv",
    "ic": "ic.csv",
    "sh2": "sh2.csv",
    "ldn": "ldn.csv",
    "bright_star": "stars.csv",
}

# Siril's own persistent record of objects manually searched and applied via its
# Astrometry > Annotate > Search Object dialog -- confirmed by inspecting a real
# installation: nothing gets written into the image's FITS header for this (checked
# against a real user-annotated file), it accumulates in this one CSV instead, in a
# *different* directory from _LOCAL_CATALOG_FILES above (SirilBridge.get_user_catalogue_dir(),
# the writable per-user data dir, vs get_system_catalogue_dir()'s read-only app-bundled
# one) -- so this needs its own LocalCsvProvider instance, not just another entry in
# _LOCAL_CATALOG_FILES. Schema is name/ra/dec/pmra/pmdec/mag/bmag/alias -- pmra/pmdec
# (proper motion) and bmag are simply unused by _parse_file, same as any other extra
# column. Catalog key deliberately isn't "user" -- that's already this app's own
# manually-placed custom objects (models.CATALOG_PRIORITY, right-click "Add Custom
# Object"), an unrelated, per-project concept.
USER_CATALOG_FILES: dict[str, str] = {
    "user_dso": "user-DSO-catalogue.csv",
}

# Every catalog we can actually query (via LocalCsvProvider and/or VizierProvider),
# with a display label matching Siril's own catalog-picker naming (brief #13's catalog
# toggle UI) -- deliberately does NOT include constellations/constellation names, since
# we have no provider implementing those yet (ARCHITECTURE.md's future-feature list), and
# a checkbox that does nothing would be misleading.
SUPPORTED_CATALOGS: dict[str, str] = {
    "messier": "Messier Catalogue (M)",
    "ngc": "New General Catalogue (NGC)",
    "ic": "Index Catalogue (IC)",
    "ldn": "Lynds Catalogue of Dark Nebulae (LDN)",
    "sh2": "Sharpless Catalogue (Sh2)",
    "bright_star": "Star Catalogue",
    # Unlike every catalog above, this one has no bundled Siril CSV (see
    # _LOCAL_CATALOG_FILES) -- VII/220A is VizieR-only, so it's also in
    # ONLINE_ONLY_CATALOGS below, which keeps it off by default and drives the
    # "needs an internet connection" status message in main_window.py.
    "barnard": "Barnard Catalogue of Dark Nebulae (B)",
    # Siril's own persistent Astrometry > Annotate > Search Object list (see
    # USER_CATALOG_FILES above) -- not "user", which is this app's own unrelated
    # manually-placed custom objects.
    "user_dso": "Siril User Catalogue",
}

# First-time defaults for per-catalog marker/connector color (brief: "clean and modern,
# pastel tones, like the ones used in PixInsight") -- soft, desaturated hues, one per
# catalog, distinct enough to tell catalogs apart at a glance without the harsh
# saturation of primary colors. Only used to seed persistence/last_used.py's stored
# catalog color map the very first time the app runs; every launch after that restores
# whatever the user last had (including any edits), per brief.
DEFAULT_CATALOG_COLORS: dict[str, str] = {
    "messier": "#F2C572",  # soft amber/gold
    "ngc": "#7FC8C4",  # soft teal
    "ic": "#B79CED",  # soft lavender
    "ldn": "#8FA9D6",  # soft slate blue
    "sh2": "#F2938C",  # soft coral
    "bright_star": "#F5E6A3",  # pale warm yellow
    "barnard": "#9FC9A8",  # soft sage green
    "user_dso": "#E3A8C4",  # dusty rose
    # Not a queryable catalog (deliberately absent from SUPPORTED_CATALOGS above --
    # see that dict's own comment), just the color user-placed custom objects render
    # with by default. Per user request: plain white, so a custom marker reads as
    # clearly distinct from every pastel catalog color at a glance.
    "user": "#FFFFFF",
}

# Field-of-view margin (as a fraction of FOV) used when querying, so labels for objects
# whose *marker* is just outside the frame but whose label could reasonably be dragged
# into view aren't missed entirely. Kept conservative to avoid pulling in the whole sky.
_QUERY_MARGIN_FRACTION = 0.05


class CatalogProvider(ABC):
    """Resolves astronomical objects within a plate-solved field into Annotations."""

    @abstractmethod
    def query(
        self,
        wcs: SirilWcs,
        catalogs: set[str],
        mag_limit: float | None = None,
    ) -> list[Annotation]:
        """Return Annotations for objects within (or near) the field covered by wcs,
        restricted to the given catalog names, optionally magnitude-limited."""
        raise NotImplementedError

    @property
    @abstractmethod
    def available_catalogs(self) -> set[str]:
        raise NotImplementedError


def _run_with_hard_timeout(fn, timeout_seconds: float):
    """Runs fn() with a hard, cross-platform wall-clock timeout, regardless of whether
    the call itself respects any timeout parameter of its own. Real, confirmed need for
    this: astroquery's own `timeout=` setting on Vizier did not reliably bound a real
    hang reproduced in this environment -- a DNS-level or socket-level hang below the
    HTTP layer isn't something an application-level timeout parameter can always catch.

    Uses a plain daemon threading.Thread, NOT concurrent.futures.ThreadPoolExecutor --
    a second real bug found while verifying this fix: ThreadPoolExecutor's worker
    threads are non-daemon, so even though future.result(timeout=N) correctly stops
    *waiting* after N seconds, the Python *process* itself still blocks at interpreter
    shutdown until that abandoned thread finishes (which, for a genuinely hung network
    call, can be minutes) -- Python does not exit while non-daemon threads are alive.
    A daemon thread has no such effect: the process can exit immediately, leaving the
    abandoned thread to be torn down with it. Safe here because every caller also trips
    a circuit breaker on timeout, so at most one thread per provider is ever leaked per
    session."""
    import threading

    outcome: dict = {}

    def _target():
        try:
            outcome["value"] = fn()
        except Exception as exc:  # noqa: BLE001 - re-raised on the calling thread below
            outcome["error"] = exc

    thread = threading.Thread(target=_target, daemon=True)
    thread.start()
    thread.join(timeout=timeout_seconds)
    if thread.is_alive():
        raise TimeoutError(f"Operation did not complete within {timeout_seconds}s")
    if "error" in outcome:
        raise outcome["error"]
    return outcome.get("value")


def _normalize_designation(catalog_name: str) -> str:
    """Normalizes a catalog designation for duplicate matching regardless of formatting
    differences between sources, e.g. "NGC 6989" (one source's spacing) vs. "NGC6989"
    (another's) must compare equal."""
    return re.sub(r"\s+", "", catalog_name).upper()


def _safe_float(value: str) -> float | None:
    value = (value or "").strip()
    if not value:
        return None
    try:
        return float(value)
    except ValueError:
        return None


def _first_alias(alias_field: str) -> str | None:
    """Local catalog alias columns are '/'-separated mixes of common names and
    cross-catalog IDs (RESEARCH.md #8, B.6), e.g. 'Crab nebula/NGC1952/Sh2-244'. Take the
    first token as a best-effort common name only if it doesn't look like a catalog ID."""
    if not alias_field:
        return None
    first = alias_field.split("/")[0].strip()
    if not first:
        return None
    upper = first.upper()
    if upper.startswith(("NGC", "IC", "SH2", "M", "LDN", "B")) and any(
        c.isdigit() for c in upper
    ):
        return None
    return first


# Siril's bundled stars.csv stores Bayer designations with the Greek letter spelled out
# as a Latin transliteration (e.g. "ksi Cyg"), but Siril's own built-in annotation
# renders these with the actual Greek letter ("ξ Cyg") -- confirmed by a real
# side-by-side screenshot. No sirilpy API exposes this conversion (it's just how Siril
# renders the raw catalog value), so it's replicated here. Covers multiple real-world
# abbreviation conventions for the same letter where they differ -- confirmed by a real
# mismatch: Siril's own catalog uses "alf" (phonetic) for alpha, but VizieR's V/50
# bright-star catalog uses "Alp" (first-three-letters truncation) instead.
_BAYER_GREEK_LETTERS: dict[str, str] = {
    "alf": "α", "alp": "α", "bet": "β", "gam": "γ", "del": "δ", "eps": "ε",
    "zet": "ζ", "eta": "η", "the": "θ", "tet": "θ", "iot": "ι",
    "kap": "κ", "lam": "λ", "mu": "μ", "nu": "ν", "ksi": "ξ", "xi": "ξ",
    "omi": "ο", "pi": "π", "rho": "ρ", "sig": "σ", "tau": "τ",
    "ups": "υ", "phi": "φ", "chi": "χ", "psi": "ψ", "ome": "ω",
}


def bayer_designation_to_greek(name: str) -> str:
    """Converts a leading Latin-transliterated Bayer prefix (e.g. "ksi Cyg") into the
    actual Greek letter (e.g. "ξ Cyg"). Returns the name unchanged if it doesn't start
    with a recognized prefix (most bright-star entries aren't Bayer-lettered at all)."""
    prefix, _, rest = name.partition(" ")
    greek = _BAYER_GREEK_LETTERS.get(prefix.lower().rstrip("."))
    if greek is None or not rest:
        return name
    return f"{greek} {rest}"


class LocalCsvProvider(CatalogProvider):
    """Reads catalog CSVs directly from disk -- either Siril's bundled, read-only
    catalogs, or (via catalog_files=USER_CATALOG_FILES) Siril's own writable per-user
    catalogue directory (see that constant's docstring).

    catalogue_dir must be the directory returned by SirilInterface.get_siril_systemdatadir()
    or get_siril_userdatadir(), joined with "catalogue" (see RESEARCH.md #8, Option C, and
    SirilBridge.get_system_catalogue_dir()/get_user_catalogue_dir()). This class does not
    know about sirilpy at all — the caller resolves the path.
    """

    def __init__(self, catalogue_dir: Path, catalog_files: dict[str, str] | None = None):
        self.catalogue_dir = Path(catalogue_dir)
        self.catalog_files = catalog_files if catalog_files is not None else _LOCAL_CATALOG_FILES

    @property
    def available_catalogs(self) -> set[str]:
        return {
            name
            for name, filename in self.catalog_files.items()
            if (self.catalogue_dir / filename).is_file()
        }

    def query(
        self,
        wcs: SirilWcs,
        catalogs: set[str],
        mag_limit: float | None = None,
    ) -> list[Annotation]:
        results: list[Annotation] = []
        # `catalogs` is a set (unordered, and randomized per-process by Python's
        # default hash seed) -- iterate in a fixed, priority-based order so results are
        # reproducible across runs even before CompositeProvider's own dedup priority
        # logic kicks in.
        for catalog in sorted(catalogs, key=default_priority_for_catalog):
            filename = self.catalog_files.get(catalog)
            if not filename:
                continue
            path = self.catalogue_dir / filename
            if not path.is_file():
                logger.warning("Local catalog file not found: %s", path)
                continue
            results.extend(self._parse_file(path, catalog, wcs, mag_limit))
        return results

    def _parse_file(
        self,
        path: Path,
        catalog: str,
        wcs: SirilWcs,
        mag_limit: float | None,
    ) -> list[Annotation]:
        rows: list[dict[str, str]] = []
        with open(path, newline="", encoding="utf-8") as fh:
            reader = csv.DictReader(fh)
            rows = list(reader)
        if not rows:
            return []

        ra = np.array([_safe_float(r.get("ra", "")) or np.nan for r in rows])
        dec = np.array([_safe_float(r.get("dec", "")) or np.nan for r in rows])
        valid = ~np.isnan(ra) & ~np.isnan(dec)
        if not np.any(valid):
            return []

        margin_px = _QUERY_MARGIN_FRACTION * max(wcs.native_width, wcs.native_height)
        x, y = wcs.world_to_pixel(ra[valid], dec[valid])
        in_bounds = wcs.in_bounds(x, y, margin_px=margin_px)

        valid_indices = np.nonzero(valid)[0]
        annotations: list[Annotation] = []
        for local_i, global_i in enumerate(valid_indices):
            if not in_bounds[local_i]:
                continue
            row = rows[global_i]
            mag = _safe_float(row.get("mag", ""))
            if mag_limit is not None and mag is not None and mag > mag_limit:
                continue
            name = (row.get("name") or "").strip()
            if not name:
                continue
            if catalog == "bright_star":
                name = bayer_designation_to_greek(name)
            annotations.append(
                Annotation(
                    catalog=catalog,
                    catalog_name=name,
                    ra=float(ra[global_i]),
                    dec=float(dec[global_i]),
                    image_x=float(x[local_i]),
                    image_y=float(y[local_i]),
                    object_type=catalog,
                    common_name=_first_alias(row.get("alias", "")),
                    angular_size=_safe_float(row.get("diameter", "")),
                    magnitude=mag,
                    priority=default_priority_for_catalog(catalog),
                )
            )
        return annotations


def count_local_catalog_entries(catalogue_dir: Path, filename: str) -> int:
    """Row count (excluding header) of a local catalog CSV, or 0 if it doesn't exist.
    Used by main_window.py to decide the "user_dso" catalog's first-run default: on
    while Siril's own Annotate-tool catalogue is still a short, deliberately curated
    list, off once it's grown large enough that defaulting it on for every image would
    just be clutter (see MainWindow's own default-catalogs logic)."""
    path = Path(catalogue_dir) / filename
    if not path.is_file():
        return 0
    with open(path, newline="", encoding="utf-8") as fh:
        return sum(1 for _ in csv.DictReader(fh))


# VizieR catalog IDs mirroring siril-scripts/utility/Svenesis-AnnotateImage.py's proven
# query set (RESEARCH.md #8, Option D).
_VIZIER_CATALOGS: dict[str, str] = {
    "messier": "VII/118",  # NGC 2000.0 -- also covers ngc/ic
    "ngc": "VII/118",
    "ic": "VII/118",
    "sh2": "VII/20",
    "barnard": "VII/220A",
    "bright_star": "V/50",
}

# A catalog with a VizieR ID but no bundled Siril CSV (see _LOCAL_CATALOG_FILES) has no
# offline fallback at all -- unlike messier/ngc/ic/sh2/bright_star, which still return
# their local-file results even when VizieR is unreachable, this one goes fully silent.
# Derived rather than hand-maintained so the next VizieR-only catalog (vdB, Abell, ...)
# is automatically covered too: main_window.py excludes these from the first-run default
# catalog selection, and shows a status message instead of a misleading "0 objects" when
# one is toggled on with VizieR unavailable (see vizier_is_available() below).
ONLINE_ONLY_CATALOGS: frozenset[str] = frozenset(
    set(_VIZIER_CATALOGS) & (set(SUPPORTED_CATALOGS) - set(_LOCAL_CATALOG_FILES))
)

# NGC2000.0's (VII/118) "Type" column codes -- standard convention for this catalog.
_NGC2000_TYPE_NAMES: dict[str, str] = {
    "Gx": "galaxy", "OC": "open cluster", "Gb": "globular cluster",
    "Nb": "nebula", "Pl": "planetary nebula", "C+N": "cluster+nebula",
    "Kt": "knot", "***": "triple star", "D*": "double star", "*": "star",
    "8": "star cloud", "?": "uncertain", "-": "uncertain", "PD": "plate defect",
}

_MESSIER_DESC_RE = re.compile(r"=\s*M\s*(\d+)\b")


def _row_str(row, col: str) -> str:
    if col not in row.colnames:
        return ""
    value = row[col]
    if value is None:
        return ""
    text = str(value).strip()
    return "" if text in ("--", "nan", "masked", "N/A") else text


def _vii118_row_to_annotation(row, wcs: SirilWcs, mag_limit: float | None) -> "Annotation | None":
    """VII/118 (NGC 2000.0). Real, confirmed schema (live-queried against VizieR):
    Name is a bare number for NGC ("1924") or "I <number>" for IC ("I 420"); Messier
    cross-references, when present, appear only as free text in Desc (e.g. "= M42").
    RAB2000/DEB2000 are sexagesimal with no seconds ("05 27.9" / "-05 19")."""
    from astropy import units as u
    from astropy.coordinates import Angle

    name_raw = _row_str(row, "Name")
    if not name_raw:
        return None
    # IC entries are "I" + the number in this fixed-width field; NGC entries are a bare
    # number. The space between "I" and the number is itself part of the fixed-width
    # padding, so it's only present when the number is short enough to leave room for
    # it -- "I 420" (3 digits) but "I5067" (4 digits, no space at all). A real user's
    # screenshot showed "NGCI5067"/"NGCI5070" labels: matching only "I " (with a
    # required space) missed every 4-digit IC number, which then fell through to the
    # NGC branch and got "NGC" prepended to the still-"I"-prefixed raw name. Confirmed
    # by live-querying VizieR for exactly this field.
    ic_match = re.match(r"^I\s*(\d+)$", name_raw, re.IGNORECASE)
    if ic_match:
        catalog, catalog_name = "ic", f"IC{ic_match.group(1)}"
    else:
        catalog, catalog_name = "ngc", f"NGC{name_raw}"

    desc = _row_str(row, "Desc")
    messier_match = _MESSIER_DESC_RE.search(desc)
    if messier_match:
        catalog, catalog_name = "messier", f"M{messier_match.group(1)}"

    ra_str, dec_str = _row_str(row, "RAB2000"), _row_str(row, "DEB2000")
    if not ra_str or not dec_str:
        return None
    try:
        ra = Angle(ra_str, unit=u.hourangle).degree
        dec = Angle(dec_str, unit=u.deg).degree
    except Exception:
        return None

    mag = _safe_float(_row_str(row, "mag"))
    if mag_limit is not None and mag is not None and mag > mag_limit:
        return None

    x, y = wcs.world_to_pixel(ra, dec)
    if not wcs.in_bounds(np.array([x]), np.array([y]))[0]:
        return None

    type_code = _row_str(row, "Type")
    return Annotation(
        catalog=catalog,
        catalog_name=catalog_name,
        ra=ra,
        dec=dec,
        image_x=float(x),
        image_y=float(y),
        object_type=_NGC2000_TYPE_NAMES.get(type_code, "unknown"),
        magnitude=mag,
        angular_size=_safe_float(_row_str(row, "size")),
        priority=default_priority_for_catalog(catalog),
    )


def _v50_row_to_annotation(row, wcs: SirilWcs, mag_limit: float | None) -> "Annotation | None":
    """V/50 (Yale Bright Star Catalogue, 5th ed.). Real, confirmed schema: RAJ2000/
    DEJ2000 are full sexagesimal with seconds ("05 26 02.4" / "-05 31 06"). Name, when
    present, is a fixed-width "<Flamsteed><Bayer><Constellation>" combination with
    inconsistent/no spacing between the Flamsteed number and Bayer letter (e.g.
    "50Alp Cyg", "62Xi  Cyg", "43    Cyg" for Flamsteed-only) -- a different convention
    from Siril's own stars.csv, so bayer_designation_to_greek() doesn't apply directly;
    _format_v50_name() below does a best-effort job of the common cases."""
    from astropy import units as u
    from astropy.coordinates import Angle

    ra_str, dec_str = _row_str(row, "RAJ2000"), _row_str(row, "DEJ2000")
    if not ra_str or not dec_str:
        return None
    try:
        ra = Angle(ra_str, unit=u.hourangle).degree
        dec = Angle(dec_str, unit=u.deg).degree
    except Exception:
        return None

    mag = _safe_float(_row_str(row, "Vmag"))
    if mag_limit is not None and mag is not None and mag > mag_limit:
        return None

    name = _format_v50_name(row)
    if not name:
        return None

    x, y = wcs.world_to_pixel(ra, dec)
    if not wcs.in_bounds(np.array([x]), np.array([y]))[0]:
        return None

    return Annotation(
        catalog="bright_star",
        catalog_name=name,
        ra=ra,
        dec=dec,
        image_x=float(x),
        image_y=float(y),
        object_type="star",
        magnitude=mag,
        priority=default_priority_for_catalog("bright_star"),
        simbad_id=_v50_simbad_id(row),
    )


def _v50_simbad_id(row) -> str | None:
    """V/50's HD/HR numbers are confirmed live to always resolve on SIMBAD (unlike the
    reconstructed Bayer/Flamsteed Name string, which SIMBAD sometimes rejects or
    misresolves -- e.g. "b01 Cyg" collides with several unrelated catalog prefixes).
    Prefer HD (Henry Draper) since it's the more universally indexed of the two."""
    hd = _row_str(row, "HD")
    if hd:
        return f"HD {hd}"
    hr = _row_str(row, "HR")
    if hr:
        return f"HR {hr}"
    return None


def _format_v50_name(row) -> str | None:
    raw = _row_str(row, "Name")
    if raw:
        collapsed = re.sub(r"\s+", " ", raw).strip()
        match = re.match(r"^(\d*)\s*([A-Za-z]*)\s*([A-Za-z]{3})$", collapsed)
        if match:
            flamsteed, bayer, const = match.groups()
            if bayer:
                greek = bayer_designation_to_greek(f"{bayer.lower()} {const}")
                if greek != f"{bayer.lower()} {const}":
                    return greek
            if flamsteed:
                return f"{flamsteed} {const}"
        return collapsed
    hd = _row_str(row, "HD")
    if hd:
        return f"HD {hd}"
    hr = _row_str(row, "HR")
    if hr:
        return f"HR {hr}"
    return None


def _vii20_row_to_annotation(row, wcs: SirilWcs, mag_limit: float | None) -> "Annotation | None":
    """VII/20 (Sharpless 1959). Real, confirmed schema: coordinates are RA1900/DE1900 --
    equinox B1900, not J2000 -- so they need an explicit frame transform, not just a
    sexagesimal parse, or annotation placement would be systematically off."""
    from astropy import units as u
    from astropy.coordinates import SkyCoord

    sh2_num = _row_str(row, "Sh2")
    if not sh2_num:
        return None
    ra_str, dec_str = _row_str(row, "RA1900"), _row_str(row, "DE1900")
    if not ra_str or not dec_str:
        return None
    try:
        coord = SkyCoord(
            ra=ra_str, dec=dec_str, unit=(u.hourangle, u.deg), frame="fk4", equinox="B1900"
        ).transform_to("icrs")
        ra, dec = coord.ra.degree, coord.dec.degree
    except Exception:
        return None

    x, y = wcs.world_to_pixel(ra, dec)
    if not wcs.in_bounds(np.array([x]), np.array([y]))[0]:
        return None

    return Annotation(
        catalog="sh2",
        catalog_name=f"Sh2-{sh2_num}",
        ra=ra,
        dec=dec,
        image_x=float(x),
        image_y=float(y),
        object_type="nebula",
        angular_size=_safe_float(_row_str(row, "Diam")),
        priority=default_priority_for_catalog("sh2"),
    )


def _vii220a_row_to_annotation(row, wcs: SirilWcs, mag_limit: float | None) -> "Annotation | None":
    """VII/220A (Barnard 1919, dark nebulae). Real, confirmed schema (live-queried
    against VizieR): Barn is a bare number ("33"); native coordinates are RA1875/DE1875
    (equinox B1875, a third equinox alongside VII/118's B2000 and VII/20's B1900 --
    yet another reason every parser here insists on live-checking rather than assuming
    a shared convention), but VizieR also returns _RA.icrs/_DE.icrs pre-converted to
    ICRS, sparing the manual frame-transform VII/20's parser needs to do itself.
    No magnitude field (dark nebulae aren't point sources); Diam is in arcmin, same
    convention as VII/20's Diam."""
    from astropy import units as u
    from astropy.coordinates import Angle

    barn_num = _row_str(row, "Barn")
    if not barn_num:
        return None
    ra_str, dec_str = _row_str(row, "_RA.icrs"), _row_str(row, "_DE.icrs")
    if not ra_str or not dec_str:
        return None
    try:
        ra = Angle(ra_str, unit=u.hourangle).degree
        dec = Angle(dec_str, unit=u.deg).degree
    except Exception:
        return None

    x, y = wcs.world_to_pixel(ra, dec)
    if not wcs.in_bounds(np.array([x]), np.array([y]))[0]:
        return None

    return Annotation(
        catalog="barnard",
        catalog_name=f"B{barn_num}",
        ra=ra,
        dec=dec,
        image_x=float(x),
        image_y=float(y),
        object_type="dark nebula",
        angular_size=_safe_float(_row_str(row, "Diam")),
        priority=default_priority_for_catalog("barnard"),
    )


# One parser per queryable VizieR catalog ID -- replaces a previous generic column-name
# guesser (colnames.get("ra") or colnames.get("_raj2000") or ...) that silently returned
# zero rows for every one of these catalogs: real VizieR schemas use sexagesimal-string
# coordinates under catalog-specific column names (RAB2000/DEB2000 for VII/118,
# RAJ2000/DEJ2000 for V/50, RA1900/DE1900 for VII/20 -- note the different equinox too),
# none of which matched the guesser's assumed decimal "ra"/"dec"/"raj2000" columns.
# Confirmed via live queries against real VizieR data, not assumed from docs.
_VIZIER_ROW_PARSERS = {
    "VII/118": _vii118_row_to_annotation,
    "V/50": _v50_row_to_annotation,
    "VII/20": _vii20_row_to_annotation,
    "VII/220A": _vii220a_row_to_annotation,
}

# Circuit breaker: flips off after the first VizieR connectivity failure and stays off
# for the rest of the process, so an unreachable/slow VizieR doesn't hang every
# subsequent catalog fetch too.
_vizier_available = True


def vizier_is_available() -> bool:
    """Read-only accessor for the circuit breaker above -- main_window.py uses this to
    tell "zero results because VizieR is unreachable" apart from "zero results because
    there's genuinely nothing there", specifically for ONLINE_ONLY_CATALOGS entries
    (everything else still returns its local-file results either way, so the
    distinction doesn't matter for them)."""
    return _vizier_available


class VizierProvider(CatalogProvider):
    """Queries VizieR for objects in the field. Network-bound: callers should run this
    inside a worker thread (see gui/workers.py), never on the Qt main thread.

    Was previously "VizierSimbadProvider" and also resolved common names via SIMBAD's
    TAP service after each query. Removed: SIMBAD's TAP API proved unreliable enough in
    real use (both in testing here and confirmed independently by a user, whose network
    could reach the plain SIMBAD website but not the TAP API) that it wasn't worth the
    complexity or the confusing intermittent failures, and the only known mirror
    (simbad.harvard.edu) doesn't actually support the TAP protocol our query needs --
    confirmed live: its sim-tap/capabilities endpoint returns a hard HTTP 500. Common
    names now come only from local catalog alias data (e.g. Messier's alias column in
    Siril's bundled messier.csv), which needs no network at all."""

    def __init__(self, row_limit: int = 500):
        self.row_limit = row_limit

    @property
    def available_catalogs(self) -> set[str]:
        return set(_VIZIER_CATALOGS)

    def query(
        self,
        wcs: SirilWcs,
        catalogs: set[str],
        mag_limit: float | None = None,
    ) -> list[Annotation]:
        from astropy import units as u
        from astropy.coordinates import SkyCoord
        from astroquery.vizier import Vizier

        global _vizier_available
        if not _vizier_available:
            return []

        fov = wcs.field_of_view()
        center = SkyCoord(ra=fov.center_ra * u.deg, dec=fov.center_dec * u.deg)
        radius = max(fov.width_deg, fov.height_deg) / 2.0 * 1.05 * u.deg

        vizier_ids = {_VIZIER_CATALOGS[c] for c in catalogs if c in _VIZIER_CATALOGS}
        results: list[Annotation] = []
        for vizier_id in vizier_ids:
            # astroquery's Vizier defaults to a 60s timeout with no explicit value set
            # here previously -- a real, confirmed bug: with 3 catalog IDs potentially
            # queried in this loop, an unreachable/slow VizieR could hang for minutes.
            # 15s per catalog, and give up on VizieR entirely for the rest of this
            # session after the first failure (see _vizier_available below) -- this is
            # a network-dependent enrichment on top of LocalCsvProvider, not something
            # worth repeatedly hanging on if it's already failed once.
            v = Vizier(columns=["*"], row_limit=self.row_limit, timeout=15)
            try:
                table_list = _run_with_hard_timeout(
                    lambda v=v: v.query_region(center, radius=radius, catalog=vizier_id),
                    timeout_seconds=15,
                )
            except Exception as exc:
                _vizier_available = False
                logger.warning(
                    "VizieR query failed for %s (%s); will not retry this session: %s",
                    vizier_id, type(exc).__name__, exc,
                )
                return results
            if not table_list:
                continue
            parsed = self._rows_to_annotations(table_list[0], vizier_id, wcs, mag_limit)
            # VII/118 is one combined NGC/IC/Messier catalog -- querying it can't be
            # restricted server-side to just the messier/ngc/ic subset this caller
            # actually wants, and each row's *own* catalog (messier vs ngc/ic) is only
            # decided after parsing (a Messier cross-ref in its Desc field promotes an
            # NGC/IC row to "messier" -- see _vii118_row_to_annotation). Without this
            # filter, requesting only "messier" still returned every NGC/IC object VII/
            # 118 happens to have in the field too -- confirmed by a real screenshot:
            # NGC/IC markers kept appearing with only Messier checked in the Catalogs
            # menu. sh2/barnard/bright_star's own vizier ids are each 1:1 with a single
            # catalog key, so this is a no-op for them either way.
            results.extend(ann for ann in parsed if ann.catalog in catalogs)

        return results

    def _rows_to_annotations(self, table, vizier_id: str, wcs: SirilWcs, mag_limit):
        parser = _VIZIER_ROW_PARSERS.get(vizier_id)
        if parser is None:
            return []
        annotations: list[Annotation] = []
        for row in table:
            try:
                ann = parser(row, wcs, mag_limit)
            except Exception:
                logger.exception("Failed to parse a row from VizieR catalog %s", vizier_id)
                continue
            if ann is not None:
                annotations.append(ann)
        return annotations


# Position-based dedup must only merge two entries describing the same *kind* of
# object -- a bright star can legitimately sit at almost the exact cataloged position of
# a nebula it illuminates/is embedded in (e.g. the star inside Sh2-9) without being the
# same object. Confirmed by a real screenshot: Siril's own annotator marks that star
# individually, but ours silently dropped it as a "duplicate" of the Sh2-9 nebula entry
# since both fell within the 30" position threshold. Deep-sky catalogs are still deduped
# against each other by position (the same object legitimately gets cross-referenced,
# e.g. M42 == NGC1976), but a star catalog entry never counts as a positional duplicate
# of anything outside its own catalog.
_DEEP_SKY_CATALOGS = {"messier", "ngc", "ic", "sh2", "ldn", "barnard"}


def _same_dedup_class(catalog_a: str, catalog_b: str) -> bool:
    if catalog_a == catalog_b:
        return True
    return catalog_a in _DEEP_SKY_CATALOGS and catalog_b in _DEEP_SKY_CATALOGS


class CompositeProvider(CatalogProvider):
    """Merges results from multiple providers, de-duplicating by angular proximity."""

    def __init__(self, providers: list[CatalogProvider], dedupe_radius_arcsec: float = 30.0):
        self.providers = providers
        self.dedupe_radius_arcsec = dedupe_radius_arcsec

    @property
    def available_catalogs(self) -> set[str]:
        out: set[str] = set()
        for p in self.providers:
            out |= p.available_catalogs
        return out

    def query(
        self,
        wcs: SirilWcs,
        catalogs: set[str],
        mag_limit: float | None = None,
    ) -> list[Annotation]:
        merged: list[Annotation] = []
        for provider in self.providers:
            usable = catalogs & provider.available_catalogs
            if not usable:
                continue
            try:
                merged.extend(provider.query(wcs, usable, mag_limit))
            except Exception:
                logger.exception("Catalog provider %s failed", provider)
        results = self._dedupe(merged)
        self._disable_out_of_frame(results, wcs)
        return results

    def _disable_out_of_frame(self, annotations: list[Annotation], wcs: SirilWcs) -> None:
        """Individual providers query with a small FOV margin (catalogs.py's
        _QUERY_MARGIN_FRACTION) so a label for an object just off-frame can still be
        dragged into view deliberately -- but that means some results legitimately land
        outside the actual photographed frame (negative/over-max image_x/image_y).
        Confirmed by a real screenshot: those markers/labels were rendering in the
        blank space beside the image (and then again in exports, since export just
        composites at the same native coordinates) with no indication they weren't
        really part of the frame. Default those to disabled -- hidden from the canvas,
        the object list still lists them unchecked so a user who actually wants one
        (e.g. a neighboring bright object just off the edge) can still turn it on."""
        if not annotations:
            return
        xs = np.array([a.image_x for a in annotations])
        ys = np.array([a.image_y for a in annotations])
        in_frame = wcs.in_bounds(xs, ys, margin_px=0.0)
        for ann, inside in zip(annotations, in_frame):
            if not inside:
                ann.enabled = False

    def _dedupe(self, annotations: list[Annotation]) -> list[Annotation]:
        if not annotations:
            return annotations
        kept: list[Annotation] = []
        threshold_deg = self.dedupe_radius_arcsec / 3600.0
        for ann in annotations:
            duplicate = False
            for existing in kept:
                same_position = (
                    _same_dedup_class(ann.catalog, existing.catalog)
                    and abs(ann.ra - existing.ra) < threshold_deg
                    and abs(ann.dec - existing.dec) < threshold_deg
                )
                # Same catalog + same designation is also a duplicate, independent of
                # position -- needed because VII/118's own coordinates are low
                # precision (RAB2000/DEB2000 give only ~0.1min RA / 1' Dec, i.e. up to
                # ~30-60" of rounding error), so the *same* physical object sourced
                # from VII/118 vs. Siril's own precise local CSV can legitimately fall
                # outside the position-only threshold. Confirmed by a real screenshot
                # showing NGC6989/NGC6996/NGC6997 each rendered twice.
                same_designation = ann.catalog == existing.catalog and _normalize_designation(
                    ann.catalog_name
                ) == _normalize_designation(existing.catalog_name)
                if same_position or same_designation:
                    duplicate = True
                    # Which catalog "wins" the display name must be a deterministic
                    # priority decision, not whichever result happened to arrive first --
                    # a real bug was confirmed where the same physical object (M31) showed
                    # as "M31" or "NGC224" across different runs, because LocalCsvProvider
                    # iterated its requested catalogs as a `set` (unordered, and
                    # additionally randomized per-process by Python's default hash seed)
                    # and this method never reconsidered its choice once a "duplicate" was
                    # found. default_priority_for_catalog ranks messier above ngc/ic, so
                    # the more specific/well-known designation always wins regardless of
                    # provider order.
                    if default_priority_for_catalog(ann.catalog) < default_priority_for_catalog(existing.catalog):
                        ann.common_name = ann.common_name or existing.common_name
                        ann.magnitude = ann.magnitude if ann.magnitude is not None else existing.magnitude
                        ann.angular_size = ann.angular_size if ann.angular_size is not None else existing.angular_size
                        kept[kept.index(existing)] = ann
                    else:
                        # Prefer the richer record (has common_name/object_type/magnitude)
                        # but never touch existing.ra/dec/image_x/image_y here -- keeping
                        # whichever result arrived first is deliberate (see
                        # _catalog_provider's comment on provider order: Local arrives
                        # first specifically so its finer position wins this tie).
                        if ann.common_name and not existing.common_name:
                            existing.common_name = ann.common_name
                        if ann.magnitude is not None and existing.magnitude is None:
                            existing.magnitude = ann.magnitude
                        if ann.angular_size is not None and existing.angular_size is None:
                            existing.angular_size = ann.angular_size
                        # LocalCsvProvider has no real object-type data and sets this to
                        # the catalog name itself as a placeholder (e.g. "ngc") -- VII/118
                        # (via VizierProvider) does carry a real NGC2000.0 type code, so
                        # let it fill that placeholder in without touching position.
                        existing_type_is_placeholder = existing.object_type in (None, "unknown", existing.catalog)
                        ann_type_is_real = ann.object_type not in (None, "unknown", ann.catalog)
                        if ann_type_is_real and existing_type_is_placeholder:
                            existing.object_type = ann.object_type
                    break
            if not duplicate:
                kept.append(ann)
        return kept
