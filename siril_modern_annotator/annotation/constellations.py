"""Constellation stick-figure lines and name labels.

Unlike every catalog in catalogs.py, this isn't Annotation/marker data at all -- it's
raw line-segment geometry, rendered as an image-level overlay alongside the RA/Dec grid
and compass (see gui/overlay_item.py's ConstellationLinesItem and annotation/models.py's
ConstellationStyle), not through CatalogProvider/CompositeProvider. So this module only
loads the raw catalog rows; annotation/renderer.py's compute_constellation_geometry
turns them into frame-relative pixel geometry.

Real, confirmed schema (both files live in Siril's own bundled catalogue dir, same
SirilInterface.get_siril_systemdatadir()/catalogue/ directory as messier.csv/etc. --
see LocalCsvProvider):
  - constellations.csv: ra,dec,ra1,dec1 -- one line segment per row (763 total across
    all 88 constellations), plain J2000 decimal degrees, same convention as every other
    bundled CSV here (unlike VizieR's per-catalog equinox quirks in catalogs.py).
  - constellationsnames.csv: name,alias,ra,dec -- one row per constellation (88 total);
    alias is the 3-letter IAU abbreviation (not used here); ra/dec is where its name
    label should anchor.
"""

from __future__ import annotations

import csv
import logging
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)

_LINES_FILENAME = "constellations.csv"
_NAMES_FILENAME = "constellationsnames.csv"


@dataclass(frozen=True)
class ConstellationLine:
    ra0: float
    dec0: float
    ra1: float
    dec1: float


@dataclass(frozen=True)
class ConstellationName:
    name: str
    ra: float
    dec: float


def _safe_float(value: str | None) -> float | None:
    try:
        return float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None


def load_constellation_lines(catalogue_dir: Path) -> list[ConstellationLine]:
    """Empty list (not an exception) when the file is missing -- same "goes quietly
    dark rather than crashing" convention as LocalCsvProvider._parse_file, so an older
    Siril install without this data simply shows no constellation lines."""
    path = Path(catalogue_dir) / _LINES_FILENAME
    if not path.is_file():
        logger.warning("Constellation lines file not found: %s", path)
        return []
    lines: list[ConstellationLine] = []
    with open(path, newline="", encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            ra0, dec0 = _safe_float(row.get("ra")), _safe_float(row.get("dec"))
            ra1, dec1 = _safe_float(row.get("ra1")), _safe_float(row.get("dec1"))
            if ra0 is None or dec0 is None or ra1 is None or dec1 is None:
                continue
            lines.append(ConstellationLine(ra0, dec0, ra1, dec1))
    return lines


def load_constellation_names(catalogue_dir: Path) -> list[ConstellationName]:
    path = Path(catalogue_dir) / _NAMES_FILENAME
    if not path.is_file():
        logger.warning("Constellation names file not found: %s", path)
        return []
    names: list[ConstellationName] = []
    with open(path, newline="", encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            name = (row.get("name") or "").strip()
            ra, dec = _safe_float(row.get("ra")), _safe_float(row.get("dec"))
            if not name or ra is None or dec is None:
                continue
            names.append(ConstellationName(name, ra, dec))
    return names
