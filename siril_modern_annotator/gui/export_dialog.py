"""Export settings dialog (brief #21-23): format, resolution mode, quality, DPI."""

from __future__ import annotations

from PyQt6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QLabel,
    QStackedWidget,
    QWidget,
)

from ..persistence.project import ExportSettings
from .widgets import DarkSpinBox

_FORMATS = [
    ("JPEG", "jpeg"),
    ("16-bit TIFF", "tiff16"),
    ("8-bit TIFF", "tiff8"),
    ("PNG", "png"),
]

_RESOLUTION_MODES = [
    ("Original resolution", "original"),
    ("Scale (%)", "scale"),
    ("Custom width/height", "custom"),
]


class ExportDialog(QDialog):
    def __init__(self, native_width: int, native_height: int, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Export Annotated Image")
        self.native_width = native_width
        self.native_height = native_height

        layout = QFormLayout(self)
        layout.addRow(QLabel(f"Source resolution: {native_width} × {native_height}"))

        self.format_combo = QComboBox()
        for label, value in _FORMATS:
            self.format_combo.addItem(label, value)
        layout.addRow("Format", self.format_combo)

        self.resolution_mode = QComboBox()
        for label, value in _RESOLUTION_MODES:
            self.resolution_mode.addItem(label, value)
        self.resolution_mode.currentIndexChanged.connect(self._on_mode_changed)
        layout.addRow("Resolution", self.resolution_mode)

        self.stack = QStackedWidget()
        self.stack.addWidget(QWidget())  # original: nothing to configure

        scale_widget = QWidget()
        scale_form = QFormLayout(scale_widget)
        self.scale_spin = DarkSpinBox()
        self.scale_spin.setRange(5, 400)
        self.scale_spin.setValue(100)
        self.scale_spin.setSuffix(" %")
        scale_form.addRow("Scale", self.scale_spin)
        self.stack.addWidget(scale_widget)

        custom_widget = QWidget()
        custom_form = QFormLayout(custom_widget)
        self.width_spin = DarkSpinBox()
        self.width_spin.setRange(16, 40000)
        self.width_spin.setValue(native_width)
        self.height_spin = DarkSpinBox()
        self.height_spin.setRange(16, 40000)
        self.height_spin.setValue(native_height)
        custom_form.addRow("Width", self.width_spin)
        custom_form.addRow("Height", self.height_spin)
        self.stack.addWidget(custom_widget)

        layout.addRow(self.stack)

        self.jpeg_quality = DarkSpinBox()
        self.jpeg_quality.setRange(1, 100)
        self.jpeg_quality.setValue(92)
        layout.addRow("JPEG quality", self.jpeg_quality)

        self.dpi_spin = DarkSpinBox()
        self.dpi_spin.setRange(1, 1200)
        self.dpi_spin.setValue(300)
        layout.addRow("DPI (metadata only — does not add pixel detail)", self.dpi_spin)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addRow(buttons)

    def _on_mode_changed(self, index: int) -> None:
        self.stack.setCurrentIndex(index)

    def export_settings(self) -> ExportSettings:
        return ExportSettings(
            format=self.format_combo.currentData(),
            resolution_mode=self.resolution_mode.currentData(),
            scale_percent=float(self.scale_spin.value()),
            custom_width=self.width_spin.value() if self.resolution_mode.currentData() == "custom" else None,
            custom_height=self.height_spin.value() if self.resolution_mode.currentData() == "custom" else None,
            jpeg_quality=self.jpeg_quality.value(),
            dpi=self.dpi_spin.value(),
        )
