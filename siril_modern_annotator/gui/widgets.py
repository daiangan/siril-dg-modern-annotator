"""Spin boxes whose up/down arrow glyphs are hand-painted via QPainter, not left to
Qt's stylesheet engine.

Real bug this fixes: theme_dark.qss's ::up-button/::down-button rules were needed to
give the increment/decrement buttons real clickable geometry (Qt stops relying on the
native style to lay out those subcontrols once any QSS touches a QAbstractSpinBox at
all -- confirmed by a real report where every spin arrow in the Style tab did nothing on
click). But the natural next step -- styling ::up-arrow/::down-arrow with the classic
"zero-size box, visible only via its borders" CSS triangle technique -- does not render
correctly in Qt's QSS engine: confirmed by direct rendering that it paints nothing at
all, and a follow-up variant (non-zero width/height) paints solid bars, never a
triangle, on every combination tried. Once ::up-button/::down-button are QSS-styled,
Qt's own native arrow fallback stops drawing anything there either, so leaving the
arrow subcontrols undefined isn't an option. Painting the glyph ourselves on top of the
normal paintEvent, positioned via the real subcontrol rects Qt itself computes, is the
only combination that reliably worked.
"""

from __future__ import annotations

from PyQt6.QtCore import QPointF, Qt, pyqtSignal
from PyQt6.QtGui import QColor, QPainter, QPolygonF
from PyQt6.QtWidgets import (
    QDoubleSpinBox,
    QHBoxLayout,
    QLabel,
    QSlider,
    QSpinBox,
    QStyle,
    QStyleOptionSpinBox,
    QWidget,
)

_ARROW_COLOR = QColor("#e6e6e6")
_ARROW_COLOR_DISABLED = QColor("#5a5c61")
_ARROW_HALF_WIDTH = 3.0
_ARROW_HEIGHT = 3.0


def _paint_spin_arrows(spin_box) -> None:
    painter = QPainter(spin_box)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(_ARROW_COLOR if spin_box.isEnabled() else _ARROW_COLOR_DISABLED)

    opt = QStyleOptionSpinBox()
    spin_box.initStyleOption(opt)
    style = spin_box.style()
    up_rect = style.subControlRect(QStyle.ComplexControl.CC_SpinBox, opt, QStyle.SubControl.SC_SpinBoxUp, spin_box)
    down_rect = style.subControlRect(
        QStyle.ComplexControl.CC_SpinBox, opt, QStyle.SubControl.SC_SpinBoxDown, spin_box
    )

    for rect, pointing_up in ((up_rect, True), (down_rect, False)):
        if rect.isEmpty():
            continue
        cx, cy = rect.center().x(), rect.center().y()
        if pointing_up:
            triangle = QPolygonF([
                QPointF(cx - _ARROW_HALF_WIDTH, cy + _ARROW_HEIGHT / 2),
                QPointF(cx + _ARROW_HALF_WIDTH, cy + _ARROW_HEIGHT / 2),
                QPointF(cx, cy - _ARROW_HEIGHT / 2),
            ])
        else:
            triangle = QPolygonF([
                QPointF(cx - _ARROW_HALF_WIDTH, cy - _ARROW_HEIGHT / 2),
                QPointF(cx + _ARROW_HALF_WIDTH, cy - _ARROW_HEIGHT / 2),
                QPointF(cx, cy + _ARROW_HEIGHT / 2),
            ])
        painter.drawPolygon(triangle)
    painter.end()


class DarkSpinBox(QSpinBox):
    def paintEvent(self, event) -> None:
        super().paintEvent(event)
        _paint_spin_arrows(self)


class DarkDoubleSpinBox(QDoubleSpinBox):
    def paintEvent(self, event) -> None:
        super().paintEvent(event)
        _paint_spin_arrows(self)


class LabeledSlider(QWidget):
    """A horizontal slider paired with a live value label -- used for the Ellipse
    marker's Radius X / Radius Y / Rotation controls (per user report: clicking a
    spinbox's tiny increment arrows repeatedly to visually fit an oval around a real
    galaxy was uncomfortable; dragging a slider is the natural interaction for "adjust
    this until it looks right"). Whole-unit (integer) precision only -- sub-pixel
    radius or sub-degree rotation precision was never needed by the spinboxes this
    replaces either (rotation's old step was 5.0), and QSlider itself is integer-only.

    Exposes the same value()/setValue()/setRange()/valueChanged surface as
    DarkDoubleSpinBox so StyleEditorWidget's existing load()/marker_style()/
    _connect_signals() code (which was written against that surface) needed no special
    casing to use this instead -- see _connect_signals()'s generic
    getattr(w, "valueChanged", None) widget-signal detection."""

    valueChanged = pyqtSignal(float)

    def __init__(self, minimum: float, maximum: float, suffix: str = "", parent=None):
        super().__init__(parent)
        self._suffix = suffix
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.slider = QSlider(Qt.Orientation.Horizontal)
        self.slider.setRange(round(minimum), round(maximum))
        self.value_label = QLabel()
        self.value_label.setMinimumWidth(52)
        self.value_label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        layout.addWidget(self.slider, 1)
        layout.addWidget(self.value_label)
        self.slider.valueChanged.connect(self._on_slider_value_changed)
        self._update_label(self.slider.value())

    def _on_slider_value_changed(self, value: int) -> None:
        self._update_label(value)
        self.valueChanged.emit(float(value))

    def _update_label(self, value: int) -> None:
        self.value_label.setText(f"{value}{self._suffix}")

    def value(self) -> float:
        return float(self.slider.value())

    def setValue(self, value: float) -> None:
        self.slider.setValue(round(value))

    def setRange(self, minimum: float, maximum: float) -> None:
        self.slider.setRange(round(minimum), round(maximum))
