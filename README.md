# DG Modern Annotator

A modern, interactive PyQt6 annotation tool for [Siril](https://siril.org), for turning a
plate-solved astrophotography image into a polished, presentation-quality annotated
image — with draggable labels, automatic collision avoidance, per-catalog colors, full
typography/marker styling, and full-resolution JPEG/PNG/TIFF export.

**[Website, screenshots, and gallery →](https://daiangan.github.io/siril-dg-modern-annotator/)**

See [RESEARCH.md](RESEARCH.md) for the technical investigation behind this project (the
`sirilpy` API, prior art in `siril-scripts`, and the catalog/WCS strategy), and
[ARCHITECTURE.md](ARCHITECTURE.md) for the module layout, data flow, and design
decisions.

## Features

- Automatic object detection for the plated-solved field (VizieR, with Siril's own
  bundled catalogs as an offline fallback) — Messier, NGC, IC, Sharpless, LDN, and
  bright stars, each toggleable and with its own default color.
- Draggable labels with automatic collision-avoidance placement ("Auto Arrange"),
  connector lines, and full typography/marker/background styling — global or per object.
- Persists your last-used catalog selection, global style, and per-catalog colors
  across sessions, so a new image starts from where you left off rather than a reset
  default.
- Save/reload an annotation layout as a JSON sidecar without touching the source image.
- Full-resolution JPEG / PNG / 8-bit TIFF / 16-bit TIFF export, independent of the
  on-screen preview resolution.

## Requirements

- Siril **1.4.4** or newer, with a plate-solved (astrometrically solved) image loaded.
- Everything else (PyQt6, astropy, astroquery, Pillow, tifffile) is installed
  automatically into Siril's own Python venv the first time the script runs, via
  `sirilpy.ensure_installed()` — no manual `pip install` needed inside Siril.

## Installing into Siril

Copy `dist/DG_Modern_Annotator.py` (built via `python build/bundle.py`, see below) into
your Siril scripts directory (Preferences → Scripts in Siril, or the directory
configured there), then relaunch Siril. It will appear in the **Scripts** menu as
**DG Modern Annotator**.

## Using it

1. Open a plate-solved FITS image in Siril.
2. Run **DG Modern Annotator** from the Scripts menu.
3. Objects in the field are fetched automatically and laid out with automatic collision
   avoidance.
4. Toggle objects/catalogs in the **Objects** tab, drag labels around, adjust styling in
   the **Style** tab (including per-catalog colors), click **Auto Arrange Labels**
   whenever you want, and **Export** when you're happy with the composition.
5. **Save Layout** writes a `<name>.annotations.json` sidecar you can reload later to
   keep editing — the source FITS file is never modified.

## Development

This repo is developed as a normal, modular, importable Python package
(`siril_modern_annotator/`) — it is *not* distributed in that form (see
[ARCHITECTURE.md](ARCHITECTURE.md#distribution) for why single-file is a hard
requirement for the official `siril-scripts` repository).

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements-dev.txt
```

### Running the tests

```bash
python -m pytest
```

Tests cover everything that doesn't require a live Siril connection or a display: WCS
coordinate math, auto-arrange collision avoidance, export resolution scaling, project
file save/reload, and local catalog CSV parsing. GUI code is exercised via a headless
(`QT_QPA_PLATFORM=offscreen`) smoke test against a mock Siril bridge during development,
since there's no way to unit-test real Siril connectivity outside Siril itself.

### Building the single-file distribution

```bash
python build/bundle.py
```

Writes `dist/DG_Modern_Annotator.py` — a single, self-contained file with every module's
source embedded and resolved at runtime via a small `sys.meta_path` loader, so Siril's
"single-file scripts" packaging requirement doesn't force flattening the source tree by
hand. Regenerate this after every change; don't hand-edit the output.

## What this is not

This project does not replace Siril's own astrometry, plate solving, or catalog
infrastructure — it's a composition and rendering layer on top of an already
plate-solved image.

## Author

Daian Gan ([daian@ganmedia.com](mailto:daian@ganmedia.com)) — [daiangan.com](https://daiangan.com)

If this tool has been useful to you, consider [buying me a coffee](https://www.paypal.com/donate/?hosted_button_id=QKSMSHKZWW7GA) — also available as a button in the app's own toolbar.

## License

MIT License — see [LICENSE](LICENSE).
