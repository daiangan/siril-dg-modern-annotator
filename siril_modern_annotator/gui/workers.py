"""QThread workers for anything network- or CPU-bound enough to block the GUI
(brief #30-31; pattern taken from siril-scripts/core/Siril_Catalog_Installer.py and
VeraLux/VeraLux_Alchemy.py — see RESEARCH.md #5).

Workers receive plain data in their constructors and emit plain data back via
pyqtSignal — never a SirilBridge/SirilInterface reference, and never a QWidget touched
from run(). This structurally enforces the main-thread-only sirilpy policy
(ARCHITECTURE.md #10).
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
from PyQt6.QtCore import QThread, pyqtSignal

from ..annotation.catalogs import CatalogProvider
from ..annotation.models import Annotation, OverlaySettings, StylePreset
from ..annotation.wcs import SirilWcs
from ..persistence.project import ExportSettings


class CatalogFetchWorker(QThread):
    progress = pyqtSignal(str)
    succeeded = pyqtSignal(list)  # list[Annotation]
    failed = pyqtSignal(str)

    def __init__(
        self,
        provider: CatalogProvider,
        wcs: SirilWcs,
        catalogs: set[str],
        mag_limit: float | None,
        parent=None,
    ):
        super().__init__(parent)
        self._provider = provider
        self._wcs = wcs
        self._catalogs = catalogs
        self._mag_limit = mag_limit

    def run(self) -> None:
        try:
            self.progress.emit("Querying catalogs...")
            results = self._provider.query(self._wcs, self._catalogs, self._mag_limit)
            self.progress.emit(f"Found {len(results)} objects in field.")
            self.succeeded.emit(results)
        except Exception as exc:  # noqa: BLE001 - surface any failure to the GUI, never crash silently
            self.failed.emit(str(exc))


class ExportWorker(QThread):
    progress = pyqtSignal(str)
    succeeded = pyqtSignal(str)  # output path
    failed = pyqtSignal(str)

    def __init__(
        self,
        output_path: Path,
        pixel_data: np.ndarray,
        annotations: list[Annotation],
        global_style: StylePreset,
        settings: ExportSettings,
        arcsec_per_px: float | None,
        icc_profile: bytes | None,
        parent=None,
        catalog_colors: dict[str, str] | None = None,
        wcs: SirilWcs | None = None,
        overlay_settings: OverlaySettings | None = None,
    ):
        super().__init__(parent)
        self._output_path = output_path
        self._pixel_data = pixel_data
        self._annotations = annotations
        self._global_style = global_style
        self._settings = settings
        self._arcsec_per_px = arcsec_per_px
        self._icc_profile = icc_profile
        self._catalog_colors = catalog_colors
        self._wcs = wcs
        self._overlay_settings = overlay_settings

    def run(self) -> None:
        from ..export.exporter import export_image

        try:
            result = export_image(
                self._output_path,
                self._pixel_data,
                self._annotations,
                self._global_style,
                self._settings,
                arcsec_per_px=self._arcsec_per_px,
                icc_profile=self._icc_profile,
                progress=self.progress.emit,
                catalog_colors=self._catalog_colors,
                wcs=self._wcs,
                overlay_settings=self._overlay_settings,
            )
            self.succeeded.emit(str(result))
        except Exception as exc:  # noqa: BLE001
            self.failed.emit(str(exc))
