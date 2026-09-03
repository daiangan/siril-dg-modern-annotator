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
from dataclasses import dataclass
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
    "messier": "M - Messier Catalogue",
    "ngc": "NGC - New General Catalogue",
    "ic": "IC - Index Catalogue",
    "ldn": "LDN - Lynds Catalogue of Dark Nebulae",
    "sh2": "Sh2 - Sharpless Catalogue",
    "bright_star": "Star Catalogue",
    # Unlike every catalog above, this one has no bundled Siril CSV (see
    # _LOCAL_CATALOG_FILES) -- VII/220A is VizieR-only, so it's also in
    # ONLINE_ONLY_CATALOGS below, which keeps it off by default and drives the
    # "needs an internet connection" status message in main_window.py.
    "barnard": "B - Barnard Catalogue of Dark Nebulae",
    # Also VizieR-only (VII/9, no bundled Siril CSV) -- same reasoning as barnard above.
    "lbn": "LBN - Lynds Catalogue of Bright Nebulae",
    # Also VizieR-only (VII/216) -- the southern-hemisphere counterpart to Sharpless
    # (Sh2 is mostly northern-sky), so this fills a real gap for objects like Carina/
    # Vela that have no Sh2 designation at all.
    "rcw": "RCW - Rodgers, Campbell & Whiteoak Catalogue",
    # Also VizieR-only (VII/21). Unlike every other catalog here, each row is
    # positioned at the *illuminating star*, not a nebula centroid -- see
    # _vii21_row_to_annotation's own docstring.
    "vdb": "vdB - van den Bergh Catalogue of Reflection Nebulae",
    # Not VizieR-only like the others above -- GumProvider reads bundled Python data
    # (gum_positions.py), fully offline, same as messier/ngc/etc.'s local CSVs. See
    # that module's docstring: not on VizieR at all, so this is a static snapshot of
    # Kevin Jardine's Integrated HII Regions catalog (galaxymap.org), per issue #10.
    "gum": "Gum - Gum Catalogue of HII Regions",
    # Also VizieR-only (VII/192). Every row carries a real NGC/UGC/MCG cross-reference
    # used as simbad_id -- confirmed live that bare "Arp <n>" is unreliable on SIMBAD
    # for at least some numbers (a genuine collision with a different Arp catalog).
    "arp": "Arp - Arp's Atlas of Peculiar Galaxies",
    # Also VizieR-only (VII/213). Deliberately excluded from _DEEP_SKY_CATALOGS' cross-
    # catalog dedup -- see _vii213_row_to_annotation's own docstring/vdB's precedent:
    # a compact group and one of its member galaxies are conceptually different
    # objects even when positionally close.
    "hickson": "HCG - Hickson Compact Groups of Galaxies",
    # Also VizieR-only (VII/272).
    "snr": "SNR - Green's Catalogue of Galactic Supernova Remnants",
    # Also VizieR-only (V/163, HASH), filtered to just its Abell-numbered rows -- no
    # standalone Abell planetary nebula catalog was found on VizieR (unlike every
    # other catalog here). Confirmed live that bare "Abell <n>" is unreliable on
    # SIMBAD (collides with Abell's own, much larger galaxy-cluster catalog) --
    # simbad_id uses the real historical identifier ("PN A66 <n>") instead.
    "abell": "Abell - Abell Catalogue of Planetary Nebulae",
    # Also VizieR-only (III/215). Unlike every catalog above, these are point-source
    # stars, not extended objects -- same category as bright_star (V/50), not the
    # deep-sky catalogs, so deliberately excluded from _DEEP_SKY_CATALOGS below (see
    # _iii215_row_to_annotation's own docstring).
    "wr": "WR - Catalogue of Galactic Wolf-Rayet Stars",
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
    "lbn": "#D9A066",  # soft terracotta/amber
    "rcw": "#D98C9E",  # dusty rose-red
    "vdb": "#8EC6E0",  # soft sky blue -- reflection nebulae scatter blue light
    "gum": "#E8A87C",  # soft peach/apricot
    "arp": "#B5B87A",  # muted olive-green
    "hickson": "#C2A98A",  # soft tan/sand
    "snr": "#DB8570",  # soft brick-red/orange
    "abell": "#C9A0DC",  # soft orchid/lilac
    "wr": "#B8E0D8",  # pale ice-blue/aqua -- evoking these hot blue-white stars
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


# ----------------------------------------------------------- Sh2CorrectedPositionProvider ----
# Explicitly experimental, per GitHub issue #10 and explicit user request -- see
# sh2_corrected_positions.py's own module docstring for the full rationale and the
# confirmed ~15-16 arcmin position errors this addresses. Self-contained on purpose so
# it's trivially reversible: this class, its one import below, and its one registration
# line in gui/main_window.py's _catalog_provider are the entire change.


class Sh2CorrectedPositionProvider(CatalogProvider):
    """Supplies only ra/dec for existing Sh2 designations, from
    sh2_corrected_positions.CORRECTED_SH2_POSITIONS. Meant to be registered *first* in
    CompositeProvider's provider list (see gui/main_window.py's _catalog_provider) so
    its position wins the same-designation dedup tie over Siril's own sh2.csv and
    VII/20 (see CompositeProvider._dedupe's same_designation path) -- catalog_name/
    angular_size/etc. still get backfilled from those other sources via the existing
    merge logic, completely unaffected by this change."""

    @property
    def available_catalogs(self) -> set[str]:
        return {"sh2"}

    def query(
        self,
        wcs: SirilWcs,
        catalogs: set[str],
        mag_limit: float | None = None,
    ) -> list[Annotation]:
        from .sh2_corrected_positions import CORRECTED_SH2_POSITIONS

        if "sh2" not in catalogs:
            return []
        margin_px = _QUERY_MARGIN_FRACTION * max(wcs.native_width, wcs.native_height)
        results: list[Annotation] = []
        for num, (ra, dec) in CORRECTED_SH2_POSITIONS.items():
            x, y = wcs.world_to_pixel(ra, dec)
            if not wcs.in_bounds(np.array([x]), np.array([y]), margin_px=margin_px)[0]:
                continue
            results.append(
                Annotation(
                    catalog="sh2",
                    catalog_name=f"Sh2-{num}",
                    ra=ra,
                    dec=dec,
                    image_x=float(x),
                    image_y=float(y),
                    object_type="nebula",
                    priority=default_priority_for_catalog("sh2"),
                )
            )
        return results


class RcwCorrectedPositionProvider(CatalogProvider):
    """Supplies only ra/dec for existing RCW designations, from
    rcw_corrected_positions.CORRECTED_RCW_POSITIONS. Mirrors Sh2CorrectedPositionProvider
    exactly (same source, same "positions only" scope, same reasoning) -- see that
    class's and rcw_corrected_positions.py's own docstrings. Meant to be registered
    *first* in CompositeProvider's provider list (see gui/main_window.py's
    _catalog_provider) so its position wins the same-designation dedup tie over
    VizierProvider's VII/216 -- catalog_name/angular_size/etc. still get backfilled
    from VII/216 via the existing merge logic, completely unaffected by this change."""

    @property
    def available_catalogs(self) -> set[str]:
        return {"rcw"}

    def query(
        self,
        wcs: SirilWcs,
        catalogs: set[str],
        mag_limit: float | None = None,
    ) -> list[Annotation]:
        from .rcw_corrected_positions import CORRECTED_RCW_POSITIONS

        if "rcw" not in catalogs:
            return []
        margin_px = _QUERY_MARGIN_FRACTION * max(wcs.native_width, wcs.native_height)
        results: list[Annotation] = []
        for num, (ra, dec) in CORRECTED_RCW_POSITIONS.items():
            x, y = wcs.world_to_pixel(ra, dec)
            if not wcs.in_bounds(np.array([x]), np.array([y]), margin_px=margin_px)[0]:
                continue
            results.append(
                Annotation(
                    catalog="rcw",
                    catalog_name=f"RCW {num}",
                    ra=ra,
                    dec=dec,
                    image_x=float(x),
                    image_y=float(y),
                    object_type="nebula",
                    priority=default_priority_for_catalog("rcw"),
                )
            )
        return results


# ----------------------------------------------------------------------- GumProvider ----
# Per GitHub issue #10, same source as Sh2CorrectedPositionProvider above -- Kevin
# Jardine's Integrated HII Regions catalog isn't on VizieR, so this is bundled as a
# static, versioned snapshot of its Gum-tagged rows rather than fetched at runtime. See
# gum_positions.py's own docstring for the confirmed RCW overlap and the known SIMBAD-
# link limitation for letter-suffixed names.


class GumProvider(CatalogProvider):
    """Fully offline, like LocalCsvProvider -- reads gum_positions.GUM_OBJECTS, which is
    plain bundled Python data, never a network call. catalog_name is already the exact
    display form ("Gum 74b", "Gum nebula", ...) straight from the source data, unlike
    Sh2CorrectedPositionProvider's reconstructed "Sh2-{num}"."""

    @property
    def available_catalogs(self) -> set[str]:
        return {"gum"}

    def query(
        self,
        wcs: SirilWcs,
        catalogs: set[str],
        mag_limit: float | None = None,
    ) -> list[Annotation]:
        from .gum_positions import GUM_OBJECTS

        if "gum" not in catalogs:
            return []
        margin_px = _QUERY_MARGIN_FRACTION * max(wcs.native_width, wcs.native_height)
        results: list[Annotation] = []
        for name, (ra, dec, angular_size) in GUM_OBJECTS.items():
            x, y = wcs.world_to_pixel(ra, dec)
            if not wcs.in_bounds(np.array([x]), np.array([y]), margin_px=margin_px)[0]:
                continue
            results.append(
                Annotation(
                    catalog="gum",
                    catalog_name=name,
                    ra=ra,
                    dec=dec,
                    image_x=float(x),
                    image_y=float(y),
                    object_type="nebula",
                    angular_size=angular_size,
                    priority=default_priority_for_catalog("gum"),
                )
            )
        return results


# VizieR catalog IDs mirroring siril-scripts/utility/Svenesis-AnnotateImage.py's proven
# query set (RESEARCH.md #8, Option D).
_VIZIER_CATALOGS: dict[str, str] = {
    "messier": "VII/118",  # NGC 2000.0 -- also covers ngc/ic
    "ngc": "VII/118",
    "ic": "VII/118",
    "sh2": "VII/20",
    "barnard": "VII/220A",
    "bright_star": "V/50",
    "lbn": "VII/9",
    "rcw": "VII/216",
    "vdb": "VII/21",
    "arp": "VII/192",
    "hickson": "VII/213",
    "snr": "VII/272",
    "abell": "V/163",
    "wr": "III/215",
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


def _vii9_row_to_annotation(row, wcs: SirilWcs, mag_limit: float | None) -> "Annotation | None":
    """VII/9 (Lynds' Catalogue of Bright Nebulae, 1965). Real, confirmed schema
    (live-queried against VizieR): Seq is the catalog's own bare number (the "LBN NNN"
    designation); native RA1950/DE1950 are minute-precision only (no seconds) and B1950,
    but -- like VII/220A's Barnard -- VizieR also returns _RA.icrs/_DE.icrs pre-converted
    to ICRS at full (arcsec) precision, sparing both the manual B1950 frame transform and
    the precision loss. No magnitude field (LBN catalogs nebulae, not point sources);
    Diam1 is the major-axis angular size in arcmin (Diam2 is the minor axis -- Diam1
    alone matches the single-value angular_size convention already used for VII/20/
    VII/220A)."""
    from astropy import units as u
    from astropy.coordinates import Angle

    lbn_num = _row_str(row, "Seq")
    if not lbn_num:
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
        catalog="lbn",
        catalog_name=f"LBN {lbn_num}",
        ra=ra,
        dec=dec,
        image_x=float(x),
        image_y=float(y),
        object_type="nebula",
        angular_size=_safe_float(_row_str(row, "Diam1")),
        priority=default_priority_for_catalog("lbn"),
    )


def _vii216_row_to_annotation(row, wcs: SirilWcs, mag_limit: float | None) -> "Annotation | None":
    """VII/216 (Rodgers, Campbell & Whiteoak 1960 -- H-alpha emission regions in the
    southern Milky Way). Real, confirmed schema: RCW is the catalog's own bare number;
    native RAB1950/DEB1950 are minute-precision only and B1950, same situation as VII/9
    and VII/220A, so this uses VizieR's own pre-converted _RA.icrs/_DE.icrs instead, same
    pattern as those two. No magnitude field (emission regions aren't point sources);
    MajAxis is the major-axis angular size in arcmin (MinAxis is the minor axis --
    MajAxis alone matches the single-value angular_size convention already used for
    VII/9/VII/20/VII/220A). "RCW <n>" confirmed live to resolve correctly on SIMBAD
    (e.g. RCW 53 -> NGC 3372, the Carina Nebula) with no identifier fixup needed."""
    from astropy import units as u
    from astropy.coordinates import Angle

    rcw_num = _row_str(row, "RCW")
    if not rcw_num:
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
        catalog="rcw",
        catalog_name=f"RCW {rcw_num}",
        ra=ra,
        dec=dec,
        image_x=float(x),
        image_y=float(y),
        object_type="nebula",
        angular_size=_safe_float(_row_str(row, "MajAxis")),
        priority=default_priority_for_catalog("rcw"),
    )


def _vii21_row_to_annotation(row, wcs: SirilWcs, mag_limit: float | None) -> "Annotation | None":
    """VII/21 (van den Bergh 1966 -- Catalogue of Reflection Nebulae). Real, confirmed
    schema: VdB is the catalog's own bare number; _RA/_DE are already plain J2000
    decimal degrees (no sexagesimal parse or equinox transform needed at all, simpler
    than every other VizieR parser here). Unlike every other nebula catalog in this
    module, each row's own position is the *illuminating star*'s (HD/SpType/Vmag are
    that star's data, not the nebula's) -- van den Bergh catalogued these nebulae by
    the star that lights them, so there's no separate nebula centroid or angular size
    to read here at all. Per explicit user decision, that different physical anchor
    means vdB is deliberately left out of _DEEP_SKY_CATALOGS' cross-catalog dedup
    (unlike RCW/Sh2/Barnard/LBN): its position rarely lines up with an NGC/IC
    centroid for the same region closely enough to safely merge. "vdB <n>" confirmed
    live to resolve correctly on SIMBAD with no identifier fixup needed."""
    vdb_num = _row_str(row, "VdB")
    if not vdb_num:
        return None
    ra, dec = _safe_float(_row_str(row, "_RA")), _safe_float(_row_str(row, "_DE"))
    if ra is None or dec is None:
        return None

    x, y = wcs.world_to_pixel(ra, dec)
    if not wcs.in_bounds(np.array([x]), np.array([y]))[0]:
        return None

    return Annotation(
        catalog="vdb",
        catalog_name=f"vdB {vdb_num}",
        ra=ra,
        dec=dec,
        image_x=float(x),
        image_y=float(y),
        object_type="reflection nebula",
        priority=default_priority_for_catalog("vdb"),
    )


def _vii192_row_to_annotation(row, wcs: SirilWcs, mag_limit: float | None) -> "Annotation | None":
    """VII/192 (Webb 1996 update of Arp's 1966 Atlas of Peculiar Galaxies). Real,
    confirmed schema: the "arpord" sub-table (query_region returns two tables for this
    ID; astroquery/table_list[0] picks arpord first, confirmed live) has RAJ2000/
    DEJ2000 already J2000 (RA has seconds, Dec is only DD MM.M -- no seconds -- but
    Angle parses both fine), Size already arcmin, and -- unlike every other catalog
    here -- a real cross-reference Name for all 338 rows (confirmed live: zero blanks),
    always an NGC/UGC/MCG-style designation.

    That Name is used as simbad_id rather than leaving SIMBAD lookup to "Arp <n>"
    itself: confirmed live that bare "Arp 1" resolves on SIMBAD to an unrelated
    globular cluster (a genuine naming collision with a different Arp catalog), while
    "Arp 220"/"Arp 273" resolve correctly -- inconsistent enough across the catalog
    that the real cross-reference name is the only reliable identifier throughout."""
    from astropy import units as u
    from astropy.coordinates import Angle

    arp_num = _row_str(row, "Arp")
    if not arp_num:
        return None
    ra_str, dec_str = _row_str(row, "RAJ2000"), _row_str(row, "DEJ2000")
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
        catalog="arp",
        catalog_name=f"Arp {arp_num}",
        ra=ra,
        dec=dec,
        image_x=float(x),
        image_y=float(y),
        object_type="galaxy",
        angular_size=_safe_float(_row_str(row, "Size")),
        simbad_id=_row_str(row, "Name") or None,
        priority=default_priority_for_catalog("arp"),
    )


def _vii213_row_to_annotation(row, wcs: SirilWcs, mag_limit: float | None) -> "Annotation | None":
    """VII/213 (Hickson's Compact Groups of Galaxies, 1982-1994 update)'s "groups"
    sub-table. Real, confirmed schema: HCG is the catalog's own bare number (1-100);
    native RA1950/DE1950 are B1950, but VizieR also returns _RA.icrs/_DE.icrs pre-
    converted, same pattern as Barnard/LBN/RCW. AngSize is arcmin. "HCG <n>" confirmed
    live to resolve correctly on SIMBAD (e.g. HCG 92 -> Stephan's Quintet) with no
    identifier fixup needed."""
    from astropy import units as u
    from astropy.coordinates import Angle

    hcg_num = _row_str(row, "HCG")
    if not hcg_num:
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
        catalog="hickson",
        catalog_name=f"HCG {hcg_num}",
        ra=ra,
        dec=dec,
        image_x=float(x),
        image_y=float(y),
        object_type="galaxy group",
        angular_size=_safe_float(_row_str(row, "AngSize")),
        priority=default_priority_for_catalog("hickson"),
    )


def _vii272_row_to_annotation(row, wcs: SirilWcs, mag_limit: float | None) -> "Annotation | None":
    """VII/272 (Green 2014 -- A Catalogue of Galactic Supernova Remnants). Real,
    confirmed schema: SNR is the catalog's own designation in Galactic-coordinate form
    ("GXXX.X+YY.Y") -- RAJ2000/DEJ2000 are already J2000 (mixed precision: RA carries
    seconds, Dec sometimes only whole arcmin, both parse fine via Angle). Dmaj is the
    major-axis angular size in arcmin (Dmin, the minor axis, is often masked/missing
    for a roughly circular remnant -- Dmaj alone matches the single-value angular_size
    convention already used elsewhere in this module). Confirmed live on SIMBAD: the
    bare designation ("G000.0+00.0") resolves to the unrelated Galactic Center region
    marker, not the remnant -- the catalog's own "SNR " prefix ("SNR G000.0+00.0") is
    required and confirmed live to resolve correctly (to Sgr A East, the actual SNR)."""
    from astropy import units as u
    from astropy.coordinates import Angle

    designation = _row_str(row, "SNR")
    if not designation:
        return None
    ra_str, dec_str = _row_str(row, "RAJ2000"), _row_str(row, "DEJ2000")
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
        catalog="snr",
        catalog_name=f"SNR {designation}",
        ra=ra,
        dec=dec,
        image_x=float(x),
        image_y=float(y),
        object_type="supernova remnant",
        angular_size=_safe_float(_row_str(row, "Dmaj")),
        common_name=_row_str(row, "Names") or None,
        priority=default_priority_for_catalog("snr"),
    )


_ABELL_NAME_RE = re.compile(r"^Abell\s+(\d+)$")


def _v163_row_to_annotation(row, wcs: SirilWcs, mag_limit: float | None) -> "Annotation | None":
    """V/163 (HASH -- Hong Kong/AAO/Strasbourg Halpha Planetary Nebula Database,
    Parker+ 2016), filtered to just its Abell-numbered rows. No standalone VizieR
    catalog for Abell's 1966 planetary nebula list was found (unlike every other
    catalog here) -- HASH is the modern, comprehensive PN database that happens to
    carry "Abell <n>" as a row's own Name whenever that's the object's traditional
    designation (confirmed live, e.g. Abell 39 at RAJ2000=246.89056/DEJ2000=27.90929),
    so this queries the whole HASH catalog per field (same as every other catalog
    query here) and discards every row whose Name isn't "Abell <n>" -- matching the
    established pattern for VII/118's own post-parse Messier/NGC/IC split.

    RAJ2000/DEJ2000 are already plain J2000 decimal degrees (no sexagesimal parse
    needed at all, like vdB/Gum). MajDiam is in *arcsec*, unlike every other angular-
    size column in this module (all arcmin) -- confirmed live via VizieR column units
    metadata, converted here to stay consistent with the rest of the app.

    Confirmed live on SIMBAD: bare "Abell <n>" is unreliable for the *whole* catalog,
    not just isolated numbers -- it systematically collides with Abell's own, much
    larger galaxy-cluster catalog (e.g. "Abell 39"/"Abell 41" both resolve to an
    unrelated ACO galaxy cluster). The historical catalog identifier SIMBAD actually
    uses for these planetary nebulae is "PN A66 <n>" (confirmed live to resolve
    correctly for several numbers), reconstructed here as simbad_id."""
    name = _row_str(row, "Name")
    match = _ABELL_NAME_RE.match(name)
    if not match:
        return None
    abell_num = match.group(1)
    ra, dec = _safe_float(_row_str(row, "RAJ2000")), _safe_float(_row_str(row, "DEJ2000"))
    if ra is None or dec is None:
        return None

    x, y = wcs.world_to_pixel(ra, dec)
    if not wcs.in_bounds(np.array([x]), np.array([y]))[0]:
        return None

    maj_diam_arcsec = _safe_float(_row_str(row, "MajDiam"))
    return Annotation(
        catalog="abell",
        catalog_name=f"Abell {abell_num}",
        ra=ra,
        dec=dec,
        image_x=float(x),
        image_y=float(y),
        object_type="planetary nebula",
        angular_size=maj_diam_arcsec / 60.0 if maj_diam_arcsec is not None else None,
        simbad_id=f"PN A66 {abell_num}",
        priority=default_priority_for_catalog("abell"),
    )


def _iii215_row_to_annotation(row, wcs: SirilWcs, mag_limit: float | None) -> "Annotation | None":
    """III/215 (van der Hucht 2001 -- 7th Catalogue of Galactic Wolf-Rayet Stars),
    "table13" (the position sub-table). Real, confirmed schema: WR is the catalog's
    own designation (a bare number, sometimes letter-suffixed like "7a" for a star
    found near an already-numbered one); RAJ2000/DEJ2000 are already J2000 with full
    seconds precision (no sexagesimal-precision issues or equinox transform needed at
    all). Name is an occasional HD/HR cross-reference (often blank); Aname is an
    alternate name, almost always populated (usually a HIP number) -- used as
    common_name, preferring Name when present since it's the more recognizable
    designation when available.

    Unlike every nebula/galaxy catalog in this module, these are point-source stars
    (like the existing bright_star catalog, V/50) -- no angular_size field exists or
    applies, and this deliberately stays out of _DEEP_SKY_CATALOGS' cross-catalog
    dedup for the same reason bright_star does: a star can legitimately sit at (or
    very near) an extended object's cataloged position without being that object --
    several WR stars are themselves the illuminating star of a nebula they're imaged
    alongside (e.g. WR136 = the Crescent Nebula/NGC 6888's central star), and merging
    would hide the star as a distinct, individually meaningful marker.

    "WR <n>" (including letter-suffixed designations) confirmed live to resolve
    correctly and unambiguously on SIMBAD -- no identifier fixup needed."""
    wr_num = _row_str(row, "WR")
    if not wr_num:
        return None
    ra_str, dec_str = _row_str(row, "RAJ2000"), _row_str(row, "DEJ2000")
    if not ra_str or not dec_str:
        return None
    try:
        from astropy import units as u
        from astropy.coordinates import Angle

        ra = Angle(ra_str, unit=u.hourangle).degree
        dec = Angle(dec_str, unit=u.deg).degree
    except Exception:
        return None

    x, y = wcs.world_to_pixel(ra, dec)
    if not wcs.in_bounds(np.array([x]), np.array([y]))[0]:
        return None

    common_name = _row_str(row, "Name") or _row_str(row, "Aname") or None
    return Annotation(
        catalog="wr",
        catalog_name=f"WR {wr_num}",
        ra=ra,
        dec=dec,
        image_x=float(x),
        image_y=float(y),
        object_type="Wolf-Rayet star",
        common_name=common_name,
        priority=default_priority_for_catalog("wr"),
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
    "VII/9": _vii9_row_to_annotation,
    "VII/216": _vii216_row_to_annotation,
    "VII/21": _vii21_row_to_annotation,
    "VII/192": _vii192_row_to_annotation,
    "VII/213": _vii213_row_to_annotation,
    "VII/272": _vii272_row_to_annotation,
    "V/163": _v163_row_to_annotation,
    "III/215": _iii215_row_to_annotation,
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


# --------------------------------------------------------- galaxy shape enrichment ----
# Per explicit user request/analysis of GitHub issue #9: auto-orient a galaxy's marker
# as an ellipse matching its real shape, instead of a plain circle, using real isophote
# diameter/axis-ratio/position-angle data -- neither exists on VII/118 (Messier/NGC/IC's
# own source, confirmed live: its only size field is a single scalar, no axis ratio or
# position angle at all) nor on any nebula/cluster catalog in this module, so this is
# deliberately galaxy-only. Two sources, tried in order:
#   - J/ApJS/269/3/sga2020 (Siena Galaxy Atlas 2020, Moustakas+ 2023): the modern,
#     preferred source (deeper mu=26 mag/arcsec^2 isophote -> a fuller envelope than the
#     older D25 data below) -- but confirmed live it does NOT cover every galaxy: M31
#     itself returns zero rows even at a 1 degree search radius, almost certainly
#     because its automated ellipse-fitting pipeline can't handle a galaxy that large.
#   - VII/237 (HyperLeda/PGC2003, Paturel+ 2003): fallback for whatever SGA2020 doesn't
#     have (confirmed live: does cover M31-scale objects), using the older, shallower
#     D25 isophote -- a looser fit, but still far better than a plain circle.
# Neither is a queryable catalog in its own right (not in _VIZIER_CATALOGS/
# SUPPORTED_CATALOGS) -- both are enrichment-only, applied on top of already-parsed
# messier/ngc/ic galaxy annotations by VizierProvider._enrich_galaxy_shapes.


@dataclass(frozen=True)
class _GalaxyShape:
    ra: float
    dec: float
    major_arcmin: float
    minor_arcmin: float
    pa_deg: float  # position angle, measured east of north, per both sources' convention


def _sga2020_row_to_shape(row) -> "_GalaxyShape | None":
    """SGA2020's own schema, confirmed live via VizieR column metadata: D26 is already
    the major-axis diameter in arcmin (mu=26 mag/arcsec^2 r-band isophote), b/a is
    already a plain linear minor/major axis ratio (not logged), RAJ2000/DEJ2000 are
    plain decimal degrees -- no unit conversion or sexagesimal parsing needed at all."""
    ra, dec = _safe_float(_row_str(row, "RAJ2000")), _safe_float(_row_str(row, "DEJ2000"))
    major = _safe_float(_row_str(row, "D26"))
    axis_ratio = _safe_float(_row_str(row, "b/a"))
    pa = _safe_float(_row_str(row, "PA"))
    if None in (ra, dec, major, axis_ratio, pa):
        return None
    return _GalaxyShape(ra=ra, dec=dec, major_arcmin=major, minor_arcmin=major * axis_ratio, pa_deg=pa)


def _vii237_row_to_shape(row) -> "_GalaxyShape | None":
    """VII/237 (HyperLeda)'s own schema, confirmed live via VizieR column metadata:
    logD25 is log10(D25) in units of 0.1 arcmin (standard RC3/LEDA convention -- D25_
    arcmin = 10**logD25 / 10), logR25 is log10(major/minor axis ratio) (so minor =
    major / 10**logR25). PA is confirmed live to be the *B1950* position angle (its own
    column description says so) -- precession rotates this by at most about a degree
    over the ~75-year span to J2000, negligible next to the visual approximation of
    using a simple ellipse at all, so it's used as-is rather than precessed. Despite the
    "J2000" column names, RAJ2000/DEJ2000 are confirmed live to be sexagesimal strings
    ("00 42 41.8"), not decimal degrees -- unlike SGA2020 above."""
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
    log_d25 = _safe_float(_row_str(row, "logD25"))
    log_r25 = _safe_float(_row_str(row, "logR25"))
    pa = _safe_float(_row_str(row, "PA"))
    if log_d25 is None or log_r25 is None or pa is None:
        return None
    major = (10.0**log_d25) / 10.0
    minor = major / (10.0**log_r25)
    return _GalaxyShape(ra=ra, dec=dec, major_arcmin=major, minor_arcmin=minor, pa_deg=pa)


# (vizier_id, row_parser), tried in order -- SGA2020 first (better/deeper isophote),
# VII/237 only as a fallback for what SGA2020 doesn't cover (see module comment above).
_GALAXY_SHAPE_SOURCES = (
    ("J/ApJS/269/3/sga2020", _sga2020_row_to_shape),
    ("VII/237", _vii237_row_to_shape),
)

# Wider than CompositeProvider's own 30" dedupe radius -- galaxy annotations here come
# from VII/118, whose own RAB2000/DEB2000 are low precision (~0.1min RA / 1' Dec, i.e.
# up to ~30-60" of rounding error, the same imprecision already documented on
# _DEEP_SKY_CATALOGS' same-designation dedup fallback above); confirmed by a real case
# while building this feature -- M31's VII/118 position and its VII/237/HyperLeda shape
# position differed by ~35", which a 30" radius would have missed entirely, silently
# leaving M31 (the whole reason for the HyperLeda fallback in the first place) without
# a shape. Still tight enough, relative to typical inter-galaxy separation in an
# uncrowded field, to avoid mismatching to a different nearby background galaxy.
_GALAXY_SHAPE_MATCH_RADIUS_ARCSEC = 90.0


def _position_angle_to_screen_rotation_deg(wcs: SirilWcs, ra: float, dec: float, pa_deg: float) -> float:
    """Converts a sky position angle (degrees, measured east of north) at (ra, dec)
    into the equivalent MarkerStyle.rotation_deg (screen-space, matching how
    QPainter.rotate() -- see gui/annotation_item.py's ellipse drawing -- rotates an
    ellipse whose un-rotated radius_x lies along the screen's horizontal axis).

    Same projection technique as compute_compass_geometry's own north/east arrows
    (annotation/renderer.py) rather than a fixed formula: a flat "PA -> screen angle"
    offset would silently break for any image whose WCS carries real rotation or an
    axis flip, which a plate-solved astrophotography frame can absolutely have. Instead
    this projects a real point a small angular distance away *in the PA direction* (the
    same great-circle offset math the compass uses for its own north/east vectors) and
    measures the actual resulting on-screen angle -- correct for any WCS orientation."""
    delta_deg = 0.01  # small enough to be locally linear, same value as the compass's own _COMPASS_ANGULAR_DELTA_DEG
    cos_dec = max(np.cos(np.radians(dec)), 1e-6)
    pa_rad = np.radians(pa_deg)
    target_ra = ra + delta_deg * np.sin(pa_rad) / cos_dec
    target_dec = dec + delta_deg * np.cos(pa_rad)
    ax, ay = wcs.world_to_pixel(ra, dec)
    tx, ty = wcs.world_to_pixel(target_ra, target_dec)
    return float(np.degrees(np.arctan2(ty - ay, tx - ax)))


def _nearest_galaxy_shape(ra: float, dec: float, shapes: list[_GalaxyShape]) -> "_GalaxyShape | None":
    if not shapes:
        return None
    threshold_deg = _GALAXY_SHAPE_MATCH_RADIUS_ARCSEC / 3600.0
    best: _GalaxyShape | None = None
    best_dist = threshold_deg
    for shape in shapes:
        dist = max(abs(shape.ra - ra), abs(shape.dec - dec))  # cheap box distance, adequate at this small a scale
        if dist <= best_dist:
            best, best_dist = shape, dist
    return best


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

        self._enrich_galaxy_shapes(results, wcs)
        return results

    def _enrich_galaxy_shapes(self, annotations: list[Annotation], wcs: SirilWcs) -> None:
        """Mutates matching galaxy Annotations in place, setting their
        galaxy_major_axis_arcmin/galaxy_minor_axis_arcmin/
        galaxy_position_angle_screen_deg fields from SGA2020/HyperLeda (see the galaxy-
        shape-enrichment section above) instead of leaving them at None. Deliberately
        plain catalog *data*, not a marker_style override (see Annotation's own
        docstring for why) -- compute_marker_geometry decides at render time whether/
        how to turn this into an oriented ellipse, the same way it already does for
        angular_size-driven circle scaling. Best-effort and strictly additive: any
        failure here (network, malformed data, nothing found) just leaves those
        objects rendering as plain circles, exactly as they already do today."""
        from astropy import units as u
        from astropy.coordinates import SkyCoord
        from astroquery.vizier import Vizier

        global _vizier_available
        if not _vizier_available:
            return
        galaxies = [
            a for a in annotations
            if a.catalog in ("messier", "ngc", "ic") and a.object_type == "galaxy"
        ]
        if not galaxies:
            return

        fov = wcs.field_of_view()
        center = SkyCoord(ra=fov.center_ra * u.deg, dec=fov.center_dec * u.deg)
        radius = max(fov.width_deg, fov.height_deg) / 2.0 * 1.05 * u.deg

        remaining = list(galaxies)
        for vizier_id, row_parser in _GALAXY_SHAPE_SOURCES:
            if not remaining:
                break
            v = Vizier(columns=["*"], row_limit=self.row_limit, timeout=15)
            try:
                table_list = _run_with_hard_timeout(
                    lambda v=v, vizier_id=vizier_id: v.query_region(center, radius=radius, catalog=vizier_id),
                    timeout_seconds=15,
                )
            except Exception as exc:
                # Enrichment-only: unlike the main catalog loop above, a failure here
                # never flips the circuit breaker or aborts the base catalog fetch --
                # those objects just keep rendering as plain circles, same as today.
                logger.warning(
                    "Galaxy-shape enrichment query failed for %s (%s): %s",
                    vizier_id, type(exc).__name__, exc,
                )
                continue
            if not table_list:
                continue
            shapes: list[_GalaxyShape] = []
            for row in table_list[0]:
                try:
                    shape = row_parser(row)
                except Exception:
                    logger.exception("Failed to parse a galaxy-shape row from %s", vizier_id)
                    continue
                if shape is not None:
                    shapes.append(shape)

            still_remaining = []
            for ann in remaining:
                shape = _nearest_galaxy_shape(ann.ra, ann.dec, shapes)
                if shape is None:
                    still_remaining.append(ann)
                    continue
                ann.galaxy_major_axis_arcmin = shape.major_arcmin
                ann.galaxy_minor_axis_arcmin = shape.minor_arcmin
                ann.galaxy_position_angle_screen_deg = _position_angle_to_screen_rotation_deg(
                    wcs, ann.ra, ann.dec, shape.pa_deg
                )
            remaining = still_remaining

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
_DEEP_SKY_CATALOGS = {
    "messier", "ngc", "ic", "sh2", "ldn", "barnard", "lbn", "rcw", "gum", "arp", "snr", "abell",
}


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
        self._apply_common_names(results)
        return results

    def _apply_common_names(self, annotations: list[Annotation]) -> None:
        """Backfills a popular name (e.g. "Crescent Nebula" for NGC6888) from
        common_names.COMMON_NAMES whenever nothing upstream already set one -- see that
        module's own docstring for the full source and data-cleaning methodology.
        Non-clobbering, same precedent as every other cross-provider enrichment field
        in _dedupe: a provider's own common_name (e.g. Siril's messier.csv "alias"
        column) always wins over this lookup."""
        from .common_names import COMMON_NAMES

        for ann in annotations:
            if ann.common_name is None:
                ann.common_name = COMMON_NAMES.get(ann.catalog_name)

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
                        ann.simbad_id = ann.simbad_id or existing.simbad_id
                        # Same fix as simbad_id above, for the same reason: galaxy
                        # shape enrichment (see VizierProvider._enrich_galaxy_shapes)
                        # only ever runs on VizierProvider's own freshly-parsed
                        # results, before CompositeProvider ever sees them -- without
                        # carrying these three fields across the merge, a galaxy that
                        # also has a LocalCsvProvider entry (e.g. any Messier object,
                        # via messier.csv) would silently lose its fitted-ellipse data
                        # here even though VizierProvider correctly attached it.
                        if ann.galaxy_major_axis_arcmin is None:
                            ann.galaxy_major_axis_arcmin = existing.galaxy_major_axis_arcmin
                            ann.galaxy_minor_axis_arcmin = existing.galaxy_minor_axis_arcmin
                            ann.galaxy_position_angle_screen_deg = existing.galaxy_position_angle_screen_deg
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
                        # Fixes a real report: Siril's own bundled stars.csv (read by
                        # LocalCsvProvider, which wins bright_star's display name/
                        # position) names some stars with a bare lowercase Bayer letter
                        # ("b01 Cyg") that SIMBAD can't resolve on its own -- but VizieR's
                        # V/50 cross-references the same star (matched here by position)
                        # to a reliable HD/HR number, so pull it in as this object's
                        # simbad_id without touching its display name.
                        if ann.simbad_id and not existing.simbad_id:
                            existing.simbad_id = ann.simbad_id
                        # This is the branch that actually fires for a Messier galaxy
                        # in real use (LocalCsvProvider wins the tie -- see the
                        # comment above this else -- so `existing` is the local-CSV
                        # entry that survives): pull the shape data VizierProvider
                        # attached to `ann` across, same reasoning as the mirror-image
                        # fix in the `if` branch above.
                        if ann.galaxy_major_axis_arcmin is not None and existing.galaxy_major_axis_arcmin is None:
                            existing.galaxy_major_axis_arcmin = ann.galaxy_major_axis_arcmin
                            existing.galaxy_minor_axis_arcmin = ann.galaxy_minor_axis_arcmin
                            existing.galaxy_position_angle_screen_deg = ann.galaxy_position_angle_screen_deg
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
