# Changelog

All notable changes to Siril Modern Annotator are documented here.

## [Unreleased]

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
