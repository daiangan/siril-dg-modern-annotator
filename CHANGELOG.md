# Changelog

All notable changes to Siril Modern Annotator are documented here.

## [0.5.0] - 2026-09-14

### Added
- Topological Code Inliner in `build/bundle.py`: flattens all package modules topologically into a standard, top-to-bottom single Python script without dynamic `_MODULE_SOURCES` dictionaries, `sys.meta_path` loaders, or runtime `exec()`.
- Automated line-length bounding enforcement in build and test suites (< 250 chars).
- Automated bundled `MainWindow` initialization test validating global namespaces and asset decoding.

### Changed
- Drastically reduced embedded icon size from 402 KB to 11.2 KB (97.2% reduction) using optimized 160x160 quantization with alpha transparency.
- Wrapped embedded base64 icon data into 76-character chunked lines, resolving text editor hangs in Siril's built-in script editor and Kate.
- Standardized stylesheet font declaration to `font-family: "Verdana"`, eliminating generic font alias scanning warnings on startup.

### Fixed
- Suppressed benign Astropy/NumPy 2.x `do_format (vectorized)` scalar angle evaluation RuntimeWarning during catalog queries.
- Improved Linux / Wayland desktop compatibility by defaulting `QT_QPA_PLATFORM` to `xcb`, applying Fusion style, and forcing window activation.
- Implemented clean IPC disconnection on application exit and window closure to release Siril bridge resources safely.

## [0.4.3] - 2026-09-10

### Added

- Initial MVP: Siril connection via `sirilpy`, plate-solve/WCS verification, image
  preview matching Siril's on-screen autostretch, catalog object fetch (VizieR/SIMBAD
  primary, local Siril CSV catalogs as offline fallback), interactive PyQt6 canvas with
  zoom/pan/fit, draggable labels with connector lines, automatic collision-avoidance
  label placement ("Auto Arrange"), object selection panel with search/filter, global and
  per-object marker/label styling with built-in presets (Minimal Modern, Scientific,
  Outreach, Social Media, Print), undo/redo, keyboard shortcuts, full-resolution
  JPEG/PNG/8-bit-TIFF/16-bit-TIFF export independent of preview resolution, and
  annotation layout save/reload as a JSON sidecar.
- `RESEARCH.md` and `ARCHITECTURE.md` documenting the technical investigation and design
  that preceded implementation.
- Single-file bundler (`build/bundle.py`) for distribution via the official
  `siril-scripts` repository's single-file-script requirement.
