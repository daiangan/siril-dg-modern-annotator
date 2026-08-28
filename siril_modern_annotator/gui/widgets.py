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
    this until it looks right").

    Exposes the same value()/setValue()/setRange()/valueChanged surface as
    DarkDoubleSpinBox so StyleEditorWidget's existing load()/marker_style()/
    _connect_signals() code (which was written against that surface) needed no special
    casing to use this instead -- see _connect_signals()'s generic
    getattr(w, "valueChanged", None) widget-signal detection.

    The underlying QSlider always spans a fixed, fine-grained internal range
    (_STEPS) mapped to [minimum, maximum] -- NOT minimum..maximum directly as raw
    integer slider units. A first version did use raw units directly, and for Radius
    X/Y (2..5000) that meant ~17 native pixels per screen pixel of drag: comfortable
    for reaching the extreme end for a huge galaxy, but far too twitchy for the small
    adjustments needed to actually fit an oval to one -- confirmed by a real report.
    curve="quadratic" concentrates precision at the low end of the range (where most
    objects' actual size falls) while still reaching the high end within one drag,
    since drag distance now maps to position along the curve, not directly to value."""

    valueChanged = pyqtSignal(float)

    _STEPS = 2000

    def __init__(self, minimum: float, maximum: float, suffix: str = "", curve: str = "linear", parent=None):
        super().__init__(parent)
        self._minimum = minimum
        self._maximum = maximum
        self._curve = curve
        self._suffix = suffix
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.slider = QSlider(Qt.Orientation.Horizontal)
        self.slider.setRange(0, self._STEPS)
        self.value_label = QLabel()
        self.value_label.setMinimumWidth(52)
        self.value_label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        layout.addWidget(self.slider, 1)
        layout.addWidget(self.value_label)
        self.slider.valueChanged.connect(self._on_slider_position_changed)
        self._update_label(self.value())

    def _position_to_value(self, position: int) -> float:
        t = position / self._STEPS
        if self._curve == "quadratic":
            t = t * t
        return self._minimum + (self._maximum - self._minimum) * t

    def _value_to_position(self, value: float) -> int:
        span = self._maximum - self._minimum
        t = (value - self._minimum) / span if span else 0.0
        t = min(1.0, max(0.0, t))
        if self._curve == "quadratic":
            t = t**0.5
        return round(t * self._STEPS)

    def _on_slider_position_changed(self, position: int) -> None:
        value = self._position_to_value(position)
        self._update_label(value)
        self.valueChanged.emit(value)

    def _update_label(self, value: float) -> None:
        self.value_label.setText(f"{round(value)}{self._suffix}")

    def value(self) -> float:
        return self._position_to_value(self.slider.value())

    def setValue(self, value: float) -> None:
        self.slider.setValue(self._value_to_position(value))

    def setRange(self, minimum: float, maximum: float) -> None:
        current = self.value()
        self._minimum, self._maximum = minimum, maximum
        self.setValue(current)
