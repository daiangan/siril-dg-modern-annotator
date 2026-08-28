# ARCHITECTURE.md — Siril Modern Annotator

This design follows directly from `RESEARCH.md`. It covers modules, data flow,
coordinate systems, the catalog provider abstraction, the annotation model, GUI/render/
export architecture, threading, persistence, and technical risk. Naming uses the working
project name "Siril Modern Annotator" but avoids hardcoding it into logic (see §Naming).

---

## 1. Guiding Constraints From Research

- **Single-file distribution is mandatory** for the official `siril-scripts` repo
  (RESEARCH.md §5). We develop as a normal multi-module Python package and add a build
  step that inlines everything into one distributable `.py`. Development and
  distribution are therefore two different artifacts of the same source tree.
- **No sirilpy catalog API** — catalog objects come from `astroquery` (VizieR)
  primarily, local Siril CSVs as offline fallback (RESEARCH.md §8). SIMBAD common-name
  enrichment was also part of this originally but was **removed** after real-world use
  (2026-08-26): its TAP service, and its only known mirror, proved too unreliable to
  keep — see `VizierProvider`'s docstring in `annotation/catalogs.py`. Common names now
  come only from local catalog alias data (e.g. Messier's `alias` column).
- **No sirilpy WCS object** — we build our own `astropy.wcs.WCS` from
  `parse_fits_header()` output (RESEARCH.md §4).
- **`sirilpy` calls are main-thread-only** by policy (no confirmed thread-safety).
  Network/CPU-heavy work moves to `QThread` workers that never touch `siril.*` or Qt
  widgets directly (RESEARCH.md §5, §Threading Model below).
- **Non-destructive**: the loaded FITS image is never mutated. We only *read* pixel
  data/metadata via sirilpy; all annotation state lives in our own in-memory model and
  on-disk JSON sidecar.
- **Export must be resolution-independent from preview** (RESEARCH.md §11): a dedicated
  renderer re-runs against native pixel coordinates, never the GUI's scaled preview.

---

## 2. Module Layout (development tree)

```text
siril_modern_annotator/
│
├── modern_annotator.py          # entry point: SirilInterface connect + QApplication bootstrap
│
├── annotation/
│   ├── models.py                 # Annotation, Catalog, Style dataclasses (Qt-independent)
│   ├── catalogs.py                # CatalogProvider ABC + VizierProvider, LocalCsvProvider
│   ├── wcs.py                     # SirilWcs: header -> astropy.wcs.WCS, pixel<->sky helpers
│   ├── layout.py                  # collision avoidance / auto-arrange algorithm (pure data)
│   └── renderer.py                # shared draw routines used by both preview and export
│
├── siril_bridge/
│   └── interface.py                # thin wrapper around sirilpy.SirilInterface (main-thread only)
│
├── gui/
│   ├── main_window.py             # QMainWindow, panel layout, menu/toolbar, undo stack owner
│   ├── image_view.py               # QGraphicsView/Scene subclass: zoom/pan/fit
│   ├── annotation_item.py          # QGraphicsItem subclasses: marker, label, connector
│   ├── object_panel.py             # QTableView + filter/search over the object list
│   ├── style_panel.py              # global + per-object style controls
│   └── workers.py                  # QThread worker classes (catalog fetch, export, auto-arrange)
│
├── export/
│   └── exporter.py                 # full-resolution compositing + PNG/JPEG/TIFF writers
│
├── persistence/
│   ├── project.py                  # .annotations.json read/write
│   └── presets.py                  # QSettings-backed style presets
│
├── resources/                      # qss stylesheet, bundled fallback data
├── tests/                          # non-GUI unit tests (see §12)
├── build/
│   └── bundle.py                   # inlines package into single-file .py for siril-scripts distribution
│
├── RESEARCH.md
├── ARCHITECTURE.md
├── README.md
├── CHANGELOG.md
└── LICENSE
```

---

## 3. Data Flow

```text
                                Siril (running instance)
                                        │
                              SirilInterface.connect()
                                        │
                     ┌──────────────────┴───────────────────┐
                     │                                       │
           siril_bridge.interface               siril_bridge.interface
          (pixel data, preview,                  (FITS header, FKeywords,
           get_image_pixeldata)                   pltsolvd flag)
                     │                                       │
                     └──────────────────┬───────────────────┘
                                        │
                              annotation.wcs.SirilWcs
                          (astropy.wcs.WCS from header;
                           sky <-> native-pixel transforms)
                                        │
                     ┌──────────────────┴───────────────────┐
                     │                                       │
          annotation.catalogs.CatalogProvider        image preview array
        (VizierProvider primary,                  (preview=True)
         LocalCsvProvider fallback)                           │
                     │                                       │
                     └──────────────────┬───────────────────┘
                                        │
                              annotation.models.Annotation[]
                            (native image_x/image_y, RA/Dec,
                             label_x/label_y, style, flags)
                                        │
                              annotation.layout (auto-arrange,
                                  collision avoidance)
                                        │
                     ┌──────────────────┴───────────────────┐
                     │                                       │
            gui.image_view + annotation_item        export.exporter
          (QGraphicsScene, preview-scaled,          (native resolution,
           interactive drag/drop, live edit)         Pillow/tifffile/numpy)
                     │                                       │
              interactive editing                    PNG / JPEG / TIFF file
```

The **same** `annotation.renderer` draw routines are called from both
`gui/annotation_item.py` (for the interactive Qt scene) and `export/exporter.py` (for the
full-resolution raster). Only the coordinate scale and output surface differ — this is
what guarantees preview/export visual parity (brief §19, §22).

---

## 4. Coordinate Systems

Three coordinate spaces are kept strictly separate, matching the brief's requirement
(§7) exactly:

| Space | Meaning | Owned by |
|---|---|---|
| **Sky** | RA/Dec (deg, ICRS) | `Annotation.ra`, `Annotation.dec` — source of truth |
| **Native image** | X/Y in the original FITS pixel grid (e.g. 6248×4176) | `Annotation.image_x`, `.image_y`, `.label_x`, `.label_y` — persisted, used for export |
| **Preview** | X/Y inside the current, possibly-zoomed `QGraphicsView` | Computed on the fly by `gui/image_view.py`; never stored |

`annotation.wcs.SirilWcs` is the only place that converts Sky → Native image
(`world_to_pixel`, vectorized via `astropy.wcs.WCS.wcs_world2pix`). `gui/image_view.py`
is the only place that converts Native image → Preview (a single affine scale+offset
derived from the current `QGraphicsView` transform and image `QPixmap` placement). No
other module performs coordinate math — this directly satisfies the brief's "no
duplicated coordinate transformation logic" rule (§34) and the "annotations must remain
scientifically correct regardless of zoom level" requirement (§7).

`export/exporter.py` never touches Preview space at all: it re-derives a Native → Output
affine transform (`output_px = native_px * (output_width / native_width)`) directly from
`Annotation.image_x/image_y`/`label_x/label_y`, independent of whatever zoom level the
GUI happened to be at when exported.

---

## 5. Annotation Model

```python
@dataclass
class Annotation:
    id: str
    catalog: str                 # e.g. "messier", "ngc", "simbad"
    catalog_name: str            # e.g. "NGC 7000"
    common_name: str | None      # e.g. "North America Nebula"
    object_type: str             # normalized type string
    ra: float
    dec: float
    image_x: float                # native pixel space
    image_y: float
    angular_size: float | None    # arcmin, if known
    magnitude: float | None
    enabled: bool = True
    label_x: float | None = None  # native pixel space; None = auto-placed
    label_y: float | None = None
    manually_positioned: bool = False
    priority: int = 0             # lower = more important; drives layout & default visibility
    marker_style: MarkerStyle | None = None   # None = inherit global style
    label_style: LabelStyle | None = None     # None = inherit global style
    connector_enabled: bool = True
    locked: bool = False          # excluded from Auto Arrange
```

`MarkerStyle` / `LabelStyle` are separate frozen-ish dataclasses (color, font, outline,
background, radius, etc.) so a preset is just "a `LabelStyle` + `MarkerStyle` pair,"
reusable both as the global default and as a per-object override (brief §17–18).

This module has **zero Qt imports** — verified by keeping `annotation/` free of any
`PyQt6` import, enforced by a simple import-lint test in `tests/`.

---

## 6. Catalog Provider Abstraction

```python
class CatalogProvider(ABC):
    @abstractmethod
    def query(self, wcs: SirilWcs, fov_deg: tuple[float, float],
              catalogs: set[str], mag_limit: float | None) -> list[Annotation]: ...
```

- `VizierProvider` — default; mirrors `Svenesis-AnnotateImage.py`'s query set
  (VII/118 Messier/NGC/IC, VII/20 Sharpless, V/50 bright stars — per-catalog row
  parsers matching each catalog's real, live-confirmed schema, since a generic
  column-name guesser silently returned zero rows for all of them). Runs inside a
  `QThread` worker (network I/O), with a hard wall-clock timeout and a per-session
  circuit breaker (gives up retrying after the first failure) since astroquery's own
  timeout settings did not reliably bound a real hang. Object type comes from VII/118's
  own Type codes when available.
- `LocalCsvProvider` — fallback and, for common names, the *only* source (SIMBAD
  common-name enrichment was removed — see §1 above); reads
  `get_siril_systemdatadir()/catalogue/*.csv` directly. No network required.
- `CompositeProvider` — merges results from multiple providers, de-duplicating by
  angular proximity + normalized designation match (position alone isn't enough: VII/118's
  own coordinates are low-precision, so the same object from VizieR vs. the local CSV can
  legitimately fall outside a position-only threshold), preferring the higher-priority
  catalog's fields when both a local and online source resolve the same object.

Catalog selection (checkboxes in `gui/object_panel.py`) maps to a `set[str]` passed into
`query()` — adding a catalog (e.g. Caldwell, Abell) later means adding a provider
implementation, not touching GUI or rendering code (brief §13's extensibility
requirement).

---

## 7. GUI Architecture

`gui/main_window.py` is a `QMainWindow` matching the brief's panel layout (§4):

- Toolbar: connect status, zoom controls, Auto Arrange, Export.
- Left dock: tabbed `object_panel` (catalog list/filter/search table) and `style_panel`
  (global + selected-object style controls).
- Center: `image_view` (the `QGraphicsView`/`QGraphicsScene` canvas).
- Status bar: cursor RA/Dec + native pixel coords, zoom %, selected object info.

Dark theme by default via a bundled `.qss` stylesheet (`resources/theme_dark.qss`),
loaded once at startup — satisfies brief §4's "avoid default-looking Qt controls" note.

`gui/annotation_item.py` defines `MarkerItem` and `LabelItem` (both `QGraphicsItem`
subclasses) plus a `ConnectorItem` that redraws whenever the label moves. Dragging a
`LabelItem` only updates `Annotation.label_x/label_y` (native space, converted back from
the drag's preview-space delta) — the marker/object position is immutable from GUI drag
interactions, per brief §10. All mutations go through `QUndoCommand` subclasses pushed
onto a single `QUndoStack` owned by `main_window.py` (brief §25).

Selection sync (brief §12) is bidirectional via a shared `SelectionModel`
(`QItemSelectionModel`-based) that both `object_panel`'s table view and `image_view`'s
scene listen to and update.

---

## 8. Rendering Architecture

`annotation/renderer.py` exposes pure functions operating on `(Annotation, scale_factor,
DrawContext)` — no Qt, no PIL, just geometry + a small drawing-backend protocol with two
implementations:

- `QtDrawBackend` — draws into the `QGraphicsScene` (used interactively).
- `RasterDrawBackend` — draws into a Pillow `ImageDraw` canvas at full/target resolution
  (used by the exporter).

This satisfies the brief's suggestion (§22) to keep the export renderer independent of
the preview renderer while guaranteeing they produce visually identical output — same
marker-style/label-style logic, same connector-line geometry, only the backend and scale
factor differ.

---

## 9. Export Architecture

`export/exporter.py`:

1. Resolve target output size (`Original`, a `%` scale, or custom W/H maintaining aspect
   ratio — brief §23).
2. Build a `RasterDrawBackend` sized to the target resolution.
3. Composite: base image (re-fetched at full native resolution via
   `get_image_pixeldata()`, stretched per the user's chosen preview mode, resized only if
   the target size differs from native) + every enabled `Annotation`'s marker/label/
   connector, scaled by `target_width / native_width`.
4. Write via Pillow (JPEG/PNG/8-bit TIFF) or `tifffile` (16-bit TIFF), embedding the
   source ICC profile (RESEARCH.md §9) and DPI metadata when requested (brief §23, with
   the explicit disclaimer that DPI is metadata only and does not add pixel detail).
5. Runs inside a `QThread` worker (`gui/workers.py::ExportWorker`) with progress signals
   (`Reading image...`, `Rendering full-resolution image...`, `Writing TIFF...` — brief
   §31) — this is the most memory- and time-intensive operation in the app (brief §36),
   so it never blocks the UI thread and only allocates the full-resolution buffer for its
   own duration.

FITS export of baked-in annotations is **not implemented** — the brief explicitly asks us
to skip this if it would violate FITS conventions (§21), and baking rendered graphics
into scientific FITS data does exactly that.

---

## 10. Threading Model

| Work | Where it runs | Notes |
|---|---|---|
| All `siril.*` calls (connect, get_image*, get_image_keywords, pix2radec) | **Main thread only** | No confirmed thread-safety (RESEARCH.md §5) |
| Catalog queries (astroquery/VizieR, local CSV read) | `QThread` worker | Pure network/file I/O + parsing, no Qt widget or sirilpy access |
| Auto-arrange / collision avoidance | `QThread` worker | Pure data transform over `Annotation[]`, safe off-thread |
| Full-resolution export/compositing/file write | `QThread` worker | Largest memory footprint; isolated so a failure doesn't crash the GUI |
| All Qt widget updates | Main thread only | Workers communicate results via `pyqtSignal`, never touch widgets directly |

Workers receive plain data (numpy arrays, dataclass lists) as constructor args and emit
plain data back — never a `SirilInterface` reference — enforcing the main-thread-only
policy structurally, not just by convention.

---

## 11. Persistence Model

- **Project/layout file**: `<image_stem>.annotations.json` (brief §27) — source image
  dimensions + identifier (for mismatch detection on reload), catalog configuration,
  the `Annotation` list (positions, visibility, custom names, style overrides), global
  style, export settings. No pixel data. Written/read by `persistence/project.py` via
  `dataclasses.asdict` / a small versioned schema (`schema_version` field from day one,
  so future migrations are possible without breaking old files).
- **Style presets**: `QSettings` (brief §17), keyed by preset name, storing serialized
  `LabelStyle`/`MarkerStyle` pairs. Built-in presets (Minimal Modern, Scientific,
  Outreach, Social Media, Print) ship as code-defined defaults, not `QSettings` entries,
  so they can't be corrupted by a bad settings file; user-created presets layer on top.

---

## 12. Testing Strategy

Non-GUI, per brief §35:

- `tests/test_wcs.py` — known RA/Dec → expected native pixel position, using a
  synthetic FITS header with a known WCS solution (avoids network/Siril dependency).
- `tests/test_scaling.py` — native → preview and native → export-target scaling round
  trips.
- `tests/test_layout.py` — collision-avoidance candidate scoring never selects an
  overlapping bounding box when a non-overlapping candidate exists.
- `tests/test_project_io.py` — save/reload a project file preserves every field.
- `tests/test_catalogs.py` — `LocalCsvProvider` parses the real bundled CSV schema
  correctly (fixture files mirroring `messier.csv`/`ngc.csv` structure from RESEARCH.md
  §8); `VizierProvider` tested against recorded/mocked responses, not live network.
- `tests/test_no_qt_in_model.py` — import-lint guard: `annotation/` and `export/` must
  not import `PyQt6`.

---

## 13. Technical Risk Analysis (brief §48)

| Risk | Mitigation |
|---|---|
| No sirilpy catalog API | Hybrid `CatalogProvider` (astroquery primary, local CSV fallback) — RESEARCH.md §8 |
| `SirilInterface` thread-safety unconfirmed | Structural main-thread-only policy; workers never hold a `SirilInterface` reference |
| Siril 1.4 vs 1.5 API drift | `siril.cmd("requires","1.4.0")` + `check_module_version()` gates at startup; fail with a clear dialog, not a crash |
| Reproducing Siril's on-screen stretch exactly | Use `get_image_pixeldata(preview=True)` (Siril's own autostretch) as default; independent Linear/Auto/Asinh control as explicit fallback |
| Preserving ICC profiles | Passthrough embed via Pillow/tifffile from `get_image_iccprofile()`; no profile *conversion* in MVP |
| Large image memory usage | Preview uses `preview=True` downscaled/8-bit data; full-resolution buffer allocated only inside the export worker, released after write |
| Preview vs export typography drift | Single shared `annotation/renderer.py` draw routines behind a backend protocol (Qt vs Raster) — see §8 |
| Qt dependency install across Siril packaging (Flatpak/macOS/Windows) | Rely entirely on `ensure_installed()` per RESEARCH.md §10 rules (`>=` only, known-good list); no manual venv/pip logic of our own |
| FITS orientation conventions | Native image space is always defined identically to `FFit.data`/`get_image_pixeldata()`'s own axis order — never re-derived independently |
| Catalog aliases / duplicate objects across providers | `CompositeProvider` de-dupes by angular proximity + name match before handing annotations to the GUI |
| Single-file distribution requirement | `build/bundle.py` inlines the package into one `.py` for siril-scripts submission; development stays modular |

---

## 14. Naming

The working name "Siril Modern Annotator" appears only in `resources/` (window title
string, About dialog) and the packaging metadata in `build/bundle.py`. No module, class,
or file name encodes the product name, so renaming later is a one-string change.

---

## 15. What Phase 3 (Skeleton) Validates

Before any catalog/WCS/annotation logic is written, the skeleton phase builds and
verifies exactly this chain, per the brief's closing instruction:

**Siril → sirilpy → PyQt6 GUI → currently loaded image**

Concretely: `modern_annotator.py` connects via `SirilInterface`, checks
`is_image_loaded()` and `get_image_keywords().pltsolvd`, opens a minimal `QMainWindow`
with an `image_view` showing `get_image_pixeldata(preview=True)`, and does nothing else —
no catalogs, no WCS math, no export. This is the first thing to run *inside Siril itself*
(Scripts menu) to confirm the launch mechanism and image access work in practice, before
building on top of assumptions that only look correct on paper.
