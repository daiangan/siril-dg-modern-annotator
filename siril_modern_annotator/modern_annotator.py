#!/usr/bin/env python3
"""Entry point launched by Siril's Scripts menu.

Order matters here, per RESEARCH.md #1/#10: sirilpy is bundled by Siril itself and
provides ensure_installed(), which is how we install our *own* dependencies (PyQt6,
astropy, astroquery, Pillow, tifffile). So we must import sirilpy and connect first,
call ensure_installed() for everything else, and only *then* import anything that
depends on those packages (including our own gui/ package, which imports PyQt6 at
module load time).
"""

from __future__ import annotations

import logging
import sys

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger("siril_modern_annotator")

_STARTUP_BANNER_LINES = [
    "================================================",
    "  DG Modern Annotator",
    "  Author: Daian Gan",
    "  Email:  daian@ganmedia.com",
    "  Web:    https://daiangan.com",
    "================================================",
]


def main() -> int:
    try:
        import sirilpy as s
    except ImportError:
        print(
            "This script must be run from within Siril (Scripts menu), which provides "
            "the sirilpy module automatically.",
            file=sys.stderr,
        )
        return 1

    s.ensure_installed(
        "PyQt6", "astropy", "astroquery", "Pillow", "tifffile",
        version_constraints=[">=6.4", ">=5.3", ">=0.4", ">=10.0", ">=2021.7"],
    )

    from .siril_bridge.interface import SirilBridge, SirilBridgeError

    bridge = SirilBridge()
    try:
        bridge.connect()
    except SirilBridgeError as exc:
        logger.error("Failed to connect to Siril: %s", exc)
        print(f"Siril Modern Annotator: {exc}", file=sys.stderr)
        return 1

    try:
        for line in _STARTUP_BANNER_LINES:
            bridge.log(line)
    except Exception:
        # Purely cosmetic (a credit banner) -- must never block the app from opening
        # over something as minor as a sirilpy logging API mismatch.
        logger.debug("Could not write the startup banner to Siril's log.", exc_info=True)

    # Deferred until after ensure_installed() has run.
    from PyQt6.QtGui import QIcon, QPixmap
    from PyQt6.QtWidgets import QApplication

    from .gui.main_window import MainWindow
    from .resources import load_app_icon_png_bytes, load_dark_stylesheet

    app = QApplication(sys.argv)
    try:
        app.setStyleSheet(load_dark_stylesheet())
    except OSError:
        logger.warning("Dark theme stylesheet not found; using Qt default styling.")

    try:
        pixmap = QPixmap()
        pixmap.loadFromData(load_app_icon_png_bytes())
        app.setWindowIcon(QIcon(pixmap))
    except OSError:
        # Purely cosmetic (Dock/taskbar icon) -- must never block the app over a
        # missing icon.png.
        logger.debug("App icon not found; using the default Python icon.", exc_info=True)

    window = MainWindow(bridge)
    window.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
