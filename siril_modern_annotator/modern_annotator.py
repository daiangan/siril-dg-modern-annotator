"""Entry point for DG Modern Annotator launched from Siril's Scripts menu."""

from __future__ import annotations

import logging
import os
import platform
import sys
import warnings

# Suppress benign Astropy Angle.to_string / NumPy vectorize RuntimeWarning (false positive on scalar angles)
warnings.filterwarnings(
    "ignore",
    message=r".*do_format \(vectorized\).*",
    category=RuntimeWarning,
)

from PyQt6.QtGui import QIcon, QPixmap
from PyQt6.QtWidgets import QApplication

from .gui.main_window import MainWindow
from .resources import load_app_icon_png_bytes, load_dark_stylesheet
from .siril_bridge.interface import SirilBridge, SirilBridgeError

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
        version_constraints=[">=6.4", ">=5.3", ">=0.4.8", ">=10.1", ">=2021.7"],
    )

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
        logger.debug("Could not write the startup banner to Siril's log.", exc_info=True)

    app = QApplication(sys.argv)
    app.setStyle("Fusion")
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
    window.raise_()
    window.activateWindow()
    try:
        return app.exec()
    finally:
        bridge.disconnect()


if __name__ == "__main__":
    sys.exit(main())
