"""QGraphicsItem representations of the image-level overlays: the RA/Dec grid and the
compass. Unlike annotation_item.py's MarkerItem/LabelItem/ConnectorItem (one instance
per Annotation), each of these has exactly one instance per loaded image.

Same coordinate convention as annotation_item.py: native image pixel space, so no
manual preview<->native conversion is needed here either. Geometry comes from
annotation.renderer's compute_grid_geometry/compute_compass_geometry -- this module
only turns that geometry into QPainter calls, mirroring export/exporter.py's Pillow
calls off the exact same functions (ARCHITECTURE.md #8).
"""

from __future__ import annotations

from PyQt6.QtCore import QPoint, QPointF, QRectF, Qt, pyqtSignal
from PyQt6.QtGui import QColor, QFont, QPainter, QPainterPath, QPen
from PyQt6.QtWidgets import QGraphicsItem, QGraphicsObject, QStyleOptionGraphicsItem, QWidget

from ..annotation.models import CompassStyle, GridStyle
from ..annotation.renderer import compute_compass_geometry, compute_grid_geometry
from ..annotation.wcs import SirilWcs

_LABEL_FONT_FAMILY = "Verdana"  # ships on both macOS and Windows -- see theme_dark.qss


class GridItem(QGraphicsItem):
    """Non-interactive: never accepts mouse input, so it can never intercept a click
    meant for a marker/label/the compass underneath or on top of it (belt-and-braces
    alongside the low z-value below -- see MarkerItem.shape()'s docstring for the kind
    of "large item's mostly-empty area steals a click" bug this sidesteps outright by
    construction rather than needing a precise hit-test)."""

    def __init__(self, wcs: SirilWcs, style: GridStyle):
        super().__init__()
        self.wcs = wcs
        self.style = style
        self.setAcceptedMouseButtons(Qt.MouseButton.NoButton)
        self.setZValue(1)

    def _geometry(self):
        return compute_grid_geometry(self.wcs, self.style)

    def boundingRect(self) -> QRectF:
        return QRectF(0.0, 0.0, float(self.wcs.native_width), float(self.wcs.native_height))

    def paint(self, painter: QPainter, option: QStyleOptionGraphicsItem, widget: QWidget | None = None):
        style = self.style
        if not style.enabled:
            return
        geo = self._geometry()
        color = QColor(style.color)
        color.setAlphaF(style.opacity)
        pen = QPen(color, style.line_width)
        painter.setPen(pen)
        for line in geo.lines:
            path = QPainterPath()
            path.moveTo(*line[0])
            for x, y in line[1:]:
                path.lineTo(x, y)
            painter.drawPath(path)

        if geo.labels:
            font = QFont(_LABEL_FONT_FAMILY)
            font.setPointSizeF(max(1.0, style.label_font_size))
            painter.setFont(font)
            for label in geo.labels:
                painter.drawText(QPointF(label.x + 4, label.y - 4), label.text)


class CompassItem(QGraphicsObject):
    """Draggable (per user request: fixed bottom-right by default, but movable) --
    mirrors MarkerItem's local-origin-drawing + setPos() convention from
    annotation_item.py exactly, including the moved signal on release."""

    moved = pyqtSignal(float, float)
    context_menu_requested = pyqtSignal(QPoint)  # screen position to pop the menu at

    def __init__(self, wcs: SirilWcs, style: CompassStyle):
        super().__init__()
        self.wcs = wcs
        self.style = style
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsMovable, True)
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemSendsGeometryChanges, True)
        self.setAcceptedMouseButtons(Qt.MouseButton.LeftButton | Qt.MouseButton.RightButton)
        self.setAcceptHoverEvents(True)
        self.setCursor(Qt.CursorShape.OpenHandCursor)
        self.setZValue(30)  # above markers (10) and labels (20) -- always reachable
        self._sync_pos_from_model()

    def _geometry(self):
        return compute_compass_geometry(self.wcs, self.style)

    def _sync_pos_from_model(self) -> None:
        geo = self._geometry()
        self.prepareGeometryChange()
        if geo is not None:
            self.setPos(*geo.anchor)

    def boundingRect(self) -> QRectF:
        geo = self._geometry()
        if geo is None:
            return QRectF()
        arrow_len = max(
            abs(geo.north_end[0] - geo.anchor[0]), abs(geo.north_end[1] - geo.anchor[1]),
            abs(geo.east_end[0] - geo.anchor[0]), abs(geo.east_end[1] - geo.anchor[1]),
        )
        pad = geo.style.line_width + geo.style.label_font_size + 8.0
        r = arrow_len + pad
        return QRectF(-r, -r, 2 * r, 2 * r)

    def paint(self, painter: QPainter, option: QStyleOptionGraphicsItem, widget: QWidget | None = None):
        geo = self._geometry()
        if geo is None:
            return
        style = geo.style
        pen = QPen(QColor(style.color), style.line_width)
        painter.setPen(pen)
        ax, ay = geo.anchor
        nx, ny = geo.north_end[0] - ax, geo.north_end[1] - ay
        ex, ey = geo.east_end[0] - ax, geo.east_end[1] - ay
        painter.drawLine(QPointF(0.0, 0.0), QPointF(nx, ny))
        painter.drawLine(QPointF(0.0, 0.0), QPointF(ex, ey))

        font = QFont(_LABEL_FONT_FAMILY)
        font.setPointSizeF(max(1.0, style.label_font_size))
        painter.setFont(font)
        painter.drawText(QPointF(nx, ny), "N")
        painter.drawText(QPointF(ex, ey), "E")

    def mousePressEvent(self, event):
        self.setCursor(Qt.CursorShape.ClosedHandCursor)
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event):
        self.setCursor(Qt.CursorShape.OpenHandCursor)
        super().mouseReleaseEvent(event)
        pos = self.pos()
        self.moved.emit(pos.x(), pos.y())

    def contextMenuEvent(self, event) -> None:
        self.context_menu_requested.emit(event.screenPos())
        event.accept()
