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
from PyQt6.QtGui import QColor, QFont, QFontMetricsF, QPainter, QPainterPath, QPen
from PyQt6.QtWidgets import QGraphicsItem, QGraphicsObject, QStyleOptionGraphicsItem, QWidget

from ..annotation.models import CompassStyle, DecLabelPosition, GridStyle, InfoBoxStyle, RaLabelPosition
from ..annotation.renderer import compute_compass_geometry, compute_grid_geometry, compute_info_box_geometry
from ..annotation.wcs import SirilWcs

_LABEL_FONT_FAMILY = "Verdana"  # ships on both macOS and Windows -- see theme_dark.qss


def _grid_label_draw_point(label, style: GridStyle, metrics: QFontMetricsF) -> QPointF:
    """QPainter.drawText(point, text) places point at the text's baseline-left corner
    -- unlike Pillow's anchor= (used identically for this same label in
    export/exporter.py's _draw_grid), Qt has no built-in anchor modes, so this computes
    the equivalent baseline position by hand: text grows *inward* from label.(x, y)
    (already inset from the frame edge by renderer.compute_grid_geometry) rather than
    being centered on or overhanging past it."""
    width = metrics.horizontalAdvance(label.text)
    if label.axis == "ra":
        x = label.x - width / 2.0
        y = label.y + metrics.ascent() if style.ra_label_position is RaLabelPosition.TOP else label.y - metrics.descent()
    else:
        x = label.x - width if style.dec_label_position is DecLabelPosition.RIGHT else label.x
        y = label.y + (metrics.ascent() - metrics.descent()) / 2.0
    return QPointF(x, y)


class GridItem(QGraphicsItem):
    """Non-interactive: never accepts mouse input, so it can never intercept a click
    meant for a marker/label/the compass underneath or on top of it. setAcceptedMouse
    Buttons(NoButton) below only stops normal Qt event *delivery* -- it does nothing
    for a raw hit-test query like QGraphicsView.itemAt(), which is exactly what
    ImageView.mousePressEvent uses to decide whether a click landed on "empty space"
    (deselect everything) or a real item. Confirmed real regression: since
    boundingRect() spans the *entire image frame* and shape() defaults to that same
    full rect (same class of bug MarkerItem.shape() already fixes for markers, just
    egregious here since this item's honest bounding box really is the whole image),
    every click anywhere in the frame hit this item first via itemAt(), so clicking
    empty space silently stopped deselecting the current object at all. shape()
    returning an always-empty path is what actually makes this item invisible to any
    hit-test, not the NoButton flag."""

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

    def shape(self) -> QPainterPath:
        return QPainterPath()

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
            metrics = QFontMetricsF(font)
            for label in geo.labels:
                painter.drawText(_grid_label_draw_point(label, style, metrics), label.text)


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


class InfoBoxItem(QGraphicsObject):
    """Technical-details text box (camera/telescope/filter/etc.) -- draggable, corner-
    anchored. Mirrors annotation_item.py's LabelItem almost exactly (local-origin
    bbox + setPos() convention, rounded-rect background, moved signal on release):
    both are "a background box with text a user can drag," this is just image-level
    instead of per-object."""

    moved = pyqtSignal(float, float)
    context_menu_requested = pyqtSignal(QPoint)

    def __init__(self, style: InfoBoxStyle, image_width: float, image_height: float, text_measurer=None):
        super().__init__()
        self.style = style
        self.image_width = image_width
        self.image_height = image_height
        self.text_measurer = text_measurer
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsMovable, True)
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemSendsGeometryChanges, True)
        self.setAcceptHoverEvents(True)
        self.setCursor(Qt.CursorShape.OpenHandCursor)
        self.setZValue(25)  # above labels (20), below the compass (30)
        self._sync_pos_from_model()

    def _geometry(self):
        return compute_info_box_geometry(
            self.style.text, self.style, self.image_width, self.image_height, self.text_measurer
        )

    def _sync_pos_from_model(self) -> None:
        geo = self._geometry()
        self.prepareGeometryChange()
        if geo is not None:
            self.setPos(geo.bbox.x0, geo.bbox.y0)

    def boundingRect(self) -> QRectF:
        geo = self._geometry()
        if geo is None:
            return QRectF()
        return QRectF(0.0, 0.0, geo.bbox.x1 - geo.bbox.x0, geo.bbox.y1 - geo.bbox.y0)

    def paint(self, painter: QPainter, option: QStyleOptionGraphicsItem, widget: QWidget | None = None):
        geo = self._geometry()
        if geo is None:
            return
        style = geo.style
        w = geo.bbox.x1 - geo.bbox.x0
        h = geo.bbox.y1 - geo.bbox.y0
        rect = QRectF(0.0, 0.0, w, h)

        bg = QColor(style.background_color)
        bg.setAlphaF(style.background_opacity)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(bg)
        painter.drawRoundedRect(rect, style.border_radius, style.border_radius)

        font = QFont(_LABEL_FONT_FAMILY)
        font.setPointSizeF(max(1.0, style.font_size))
        painter.setFont(font)
        painter.setPen(QColor(style.text_color))
        painter.drawText(
            rect.adjusted(style.padding, style.padding, -style.padding, -style.padding),
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
            geo.text,
        )

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
