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

from PyQt6.QtCore import QPointF, Qt
from PyQt6.QtGui import QColor, QPainter, QPolygonF
from PyQt6.QtWidgets import QDoubleSpinBox, QSpinBox, QStyle, QStyleOptionSpinBox

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
