"""QGraphicsItem representations of an Annotation: marker, draggable label, connector.

All items live directly in the QGraphicsScene's coordinate system, which ImageView
constructs to equal *native image pixel space* (image_view.py's pixmap item is scaled up
to native resolution). That means marker radius / font size / stroke width — all defined
in native pixel units, the same units export/exporter.py composites at — need no manual
preview<->native conversion here; Qt's own view transform handles the on-screen zoom.
This is what guarantees interactive-canvas and full-resolution-export parity
(ARCHITECTURE.md #8).

Geometry (bounding boxes, connector routing) comes from annotation.renderer — this
module only turns that geometry into QPainter calls.
"""

from __future__ import annotations

from typing import Callable

from PyQt6.QtCore import QPoint, QPointF, QRectF, Qt, pyqtSignal
from PyQt6.QtGui import QColor, QFont, QPainter, QPainterPath, QPainterPathStroker, QPen, QTransform
from PyQt6.QtWidgets import (
    QGraphicsItem,
    QGraphicsObject,
    QGraphicsPathItem,
    QStyleOptionGraphicsItem,
    QWidget,
)

from ..annotation.models import Annotation, MarkerShape, StylePreset
from ..annotation.renderer import (
    compute_connector_points,
    compute_label_geometry,
    compute_marker_geometry,
    resolve_connector_color,
)

_SELECTION_COLOR = QColor("#ffb454")


def _qfont(label_style) -> QFont:
    font = QFont(label_style.font_family)
    font.setPointSizeF(max(1.0, label_style.font_size))
    font.setBold(label_style.bold)
    font.setItalic(label_style.italic)
    return font


def qt_text_measurer(label_style_provider):
    """Returns a TextMeasurer callable backed by QFontMetricsF, for pixel-accurate
    auto-arrange results in the GUI (annotation.layout's default heuristic is used only
    when Qt isn't available, e.g. in headless tests).

    Uses horizontalAdvance()/height() (the metrics QPainter.drawText itself lays text
    out with), not boundingRect() (tight ink bounds, which can start at a non-zero x
    for glyphs with left bearing and can under-measure the box drawText needs) -- a real
    screenshot showed the last character(s) of a label clipped off because the
    computed box was narrower than what drawText actually needed to render into. A
    small fixed margin is added on top as a safety cushion against any residual
    kerning/hinting differences between measurement and paint.
    """
    from PyQt6.QtGui import QFontMetricsF

    _SAFETY_MARGIN_PX = 3.0

    def measure(text: str, style) -> tuple[float, float]:
        # Multi-line support: custom display names/notes can span several lines (a
        # "bigger tooltip"-style description). QPainter.drawText into a QRectF already
        # respects embedded "\n" as hard line breaks, so no drawing change is needed --
        # only the measured box has to be wide/tall enough for every line.
        metrics = QFontMetricsF(_qfont(style))
        lines = text.split("\n") or [""]
        width = max((metrics.horizontalAdvance(line) for line in lines), default=0.0) + _SAFETY_MARGIN_PX
        height = metrics.height() * len(lines) + _SAFETY_MARGIN_PX
        return width + 2 * style.padding, height + 2 * style.padding

    return measure


class MarkerItem(QGraphicsObject):
    moved = pyqtSignal(float, float)  # emits new native (x, y) marker center when drag finishes
    clicked = pyqtSignal()
    double_clicked = pyqtSignal()
    context_menu_requested = pyqtSignal(QPoint)  # screen position to pop the menu at

    def __init__(
        self,
        annotation: Annotation,
        global_style: StylePreset,
        arcsec_per_px: float | None,
        max_radius_px: float | None = None,
        catalog_colors: dict[str, str] | None = None,
    ):
        super().__init__()
        self.annotation = annotation
        self.global_style = global_style
        self.arcsec_per_px = arcsec_per_px
        self.max_radius_px = max_radius_px
        # Shared reference (not copied) with main_window's own dict -- a catalog color
        # edit mutates that dict in place, so every item picks it up on its next
        # repaint without main_window needing to walk every item to reassign it.
        self.catalog_colors = catalog_colors
        self.selected_ = False
        # RightButton is needed alongside LeftButton so a right-click reliably reaches
        # this item for contextMenuEvent (brief: right-click a marker to hide it) --
        # QGraphicsItem gates context-menu delivery on the same accepted-buttons set
        # used for mouse press/release.
        self.setAcceptedMouseButtons(Qt.MouseButton.LeftButton | Qt.MouseButton.RightButton)
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsMovable, True)
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsSelectable, True)
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemSendsGeometryChanges, True)
        self.setAcceptHoverEvents(True)
        self.setCursor(Qt.CursorShape.OpenHandCursor)
        self.setZValue(10)
        self._sync_pos_from_model()

    def _geometry(self):
        return compute_marker_geometry(
            self.annotation, self.global_style, self.arcsec_per_px, self.max_radius_px,
            self.catalog_colors,
        )

    def _sync_pos_from_model(self) -> None:
        """Repositions this item to the annotation's current effective marker position
        (image_x/image_y, or the marker_x/marker_y override) -- paint()/boundingRect()
        below draw at local (0, 0), matching LabelItem's convention, so a drag just
        needs Qt's own pos() to become the new override; see MainWindow._refresh_annotation."""
        geo = self._geometry()
        self.prepareGeometryChange()
        self.setPos(geo.x, geo.y)

    def boundingRect(self) -> QRectF:
        geo = self._geometry()
        pad = geo.style.stroke_width + 2
        r = geo.radius
        return QRectF(-r - pad, -r - pad, 2 * (r + pad), 2 * (r + pad))

    def shape(self) -> QPainterPath:
        """Without this, Qt's default shape() is just boundingRect() as a filled
        rectangle -- meaning the *entire* empty interior of an outline-only circle or
        ellipse (this item never fills its interior, see paint() below) is clickable,
        not just the drawn ring. Confirmed real report: a large marker's mostly-empty
        bounding square was intercepting clicks meant for a smaller marker nearby (or
        even fully inside it, like NGC 206/M32 sitting inside M31's own marker) --
        made worse by Ellipse, whose bounding square is sized off max(radius_x,
        radius_y) regardless of how thin the actual oval is. This traces a path along
        what's actually drawn and stroke-expands it by a generous click tolerance, so
        only *near the visible line* is clickable, matching what a user would expect
        from looking at the marker."""
        geo = self._geometry()
        style = geo.style
        r = geo.radius
        if style.shape is MarkerShape.NONE:
            return QPainterPath()
        if style.shape is MarkerShape.DOT:
            path = QPainterPath()
            rad = max(1.5, style.stroke_width) + 4.0  # small filled dot: no ring-only stroking needed
            path.addEllipse(QPointF(0.0, 0.0), rad, rad)
            return path

        path = QPainterPath()
        if style.shape is MarkerShape.CIRCLE or style.shape is MarkerShape.RETICLE:
            path.addEllipse(QPointF(0.0, 0.0), r, r)
        elif style.shape is MarkerShape.CROSSHAIR:
            path.moveTo(-r, 0.0)
            path.lineTo(r, 0.0)
            path.moveTo(0.0, -r)
            path.lineTo(0.0, r)
        elif style.shape is MarkerShape.BRACKETS:
            arm = r * 0.5
            corners = [(-r, -r), (r, -r), (r, r), (-r, r)]
            directions = [((1, 0), (0, 1)), ((-1, 0), (0, 1)), ((-1, 0), (0, -1)), ((1, 0), (0, -1))]
            for (ox, oy), ((hx, hy), (vx, vy)) in zip(corners, directions):
                path.moveTo(ox, oy)
                path.lineTo(ox + hx * arm, oy + hy * arm)
                path.moveTo(ox, oy)
                path.lineTo(ox + vx * arm, oy + vy * arm)
        elif style.shape is MarkerShape.ELLIPSE:
            local = QPainterPath()
            local.addEllipse(QPointF(0.0, 0.0), geo.radius_x, geo.radius_y)
            path = QTransform().rotate(geo.rotation_deg).map(local)

        stroker = QPainterPathStroker()
        stroker.setWidth(max(style.stroke_width + 10.0, 12.0))
        return stroker.createStroke(path)

    def paint(self, painter: QPainter, option: QStyleOptionGraphicsItem, widget: QWidget | None = None):
        geo = self._geometry()
        style = geo.style
        if style.shape is MarkerShape.NONE:
            return
        color = QColor(style.color)
        color.setAlphaF(style.opacity)
        pen = QPen(color, style.stroke_width)
        painter.setPen(pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        cx, cy, r = 0.0, 0.0, geo.radius

        if style.shape is MarkerShape.CIRCLE:
            painter.drawEllipse(QPointF(cx, cy), r, r)
        elif style.shape is MarkerShape.DOT:
            painter.setBrush(color)
            painter.drawEllipse(QPointF(cx, cy), max(1.5, style.stroke_width), max(1.5, style.stroke_width))
        elif style.shape is MarkerShape.CROSSHAIR:
            painter.drawLine(QPointF(cx - r, cy), QPointF(cx + r, cy))
            painter.drawLine(QPointF(cx, cy - r), QPointF(cx, cy + r))
        elif style.shape is MarkerShape.RETICLE:
            painter.drawEllipse(QPointF(cx, cy), r, r)
            gap = r * 0.35
            for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                painter.drawLine(
                    QPointF(cx + dx * (r + 2), cy + dy * (r + 2)),
                    QPointF(cx + dx * (r + gap), cy + dy * (r + gap)),
                )
        elif style.shape is MarkerShape.BRACKETS:
            arm = r * 0.5
            corners = [(-r, -r), (r, -r), (r, r), (-r, r)]
            directions = [((1, 0), (0, 1)), ((-1, 0), (0, 1)), ((-1, 0), (0, -1)), ((1, 0), (0, -1))]
            for (ox, oy), ((hx, hy), (vx, vy)) in zip(corners, directions):
                px, py = cx + ox, cy + oy
                painter.drawLine(QPointF(px, py), QPointF(px + hx * arm, py + hy * arm))
                painter.drawLine(QPointF(px, py), QPointF(px + vx * arm, py + vy * arm))
        elif style.shape is MarkerShape.ELLIPSE:
            painter.save()
            painter.rotate(geo.rotation_deg)
            painter.drawEllipse(QPointF(0.0, 0.0), geo.radius_x, geo.radius_y)
            painter.restore()

        if self.selected_:
            sel_pen = QPen(_SELECTION_COLOR, max(1.0, style.stroke_width))
            sel_pen.setStyle(Qt.PenStyle.DashLine)
            painter.setPen(sel_pen)
            painter.setBrush(Qt.BrushStyle.NoBrush)
            if style.shape is MarkerShape.ELLIPSE:
                painter.save()
                painter.rotate(geo.rotation_deg)
                painter.drawEllipse(QPointF(0.0, 0.0), geo.radius_x + 5, geo.radius_y + 5)
                painter.restore()
            else:
                painter.drawEllipse(QPointF(cx, cy), r + 5, r + 5)

    def mousePressEvent(self, event):
        self.clicked.emit()
        self.setCursor(Qt.CursorShape.ClosedHandCursor)
        super().mousePressEvent(event)

    def mouseDoubleClickEvent(self, event) -> None:
        self.double_clicked.emit()
        super().mouseDoubleClickEvent(event)

    def contextMenuEvent(self, event) -> None:
        self.context_menu_requested.emit(event.screenPos())
        event.accept()

    def mouseReleaseEvent(self, event):
        self.setCursor(Qt.CursorShape.OpenHandCursor)
        super().mouseReleaseEvent(event)
        pos = self.pos()
        self.moved.emit(pos.x(), pos.y())

    def set_selected_visual(self, selected: bool) -> None:
        self.selected_ = selected
        self.update()


class LabelItem(QGraphicsObject):
    moved = pyqtSignal(float, float)  # emits new native (x, y) top-left when drag finishes
    clicked = pyqtSignal()
    double_clicked = pyqtSignal()
    context_menu_requested = pyqtSignal(QPoint)  # screen position to pop the menu at

    def __init__(
        self,
        annotation: Annotation,
        global_style: StylePreset,
        text_measurer: Callable | None = None,
        catalog_colors: dict[str, str] | None = None,
    ):
        super().__init__()
        self.annotation = annotation
        self.global_style = global_style
        self.text_measurer = text_measurer
        self.catalog_colors = catalog_colors
        self.selected_ = False
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsMovable, True)
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsSelectable, True)
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemSendsGeometryChanges, True)
        self.setAcceptHoverEvents(True)
        self.setCursor(Qt.CursorShape.OpenHandCursor)
        self.setZValue(20)
        self._sync_pos_from_model()

    def _geometry(self):
        return compute_label_geometry(
            self.annotation, self.global_style, self.text_measurer, self.catalog_colors
        )

    def _sync_pos_from_model(self) -> None:
        geo = self._geometry()
        self.prepareGeometryChange()
        self.setPos(geo.bbox.x0, geo.bbox.y0)

    def boundingRect(self) -> QRectF:
        geo = self._geometry()
        w = geo.bbox.x1 - geo.bbox.x0
        h = geo.bbox.y1 - geo.bbox.y0
        return QRectF(0, 0, w, h)

    def paint(self, painter: QPainter, option: QStyleOptionGraphicsItem, widget: QWidget | None = None):
        geo = self._geometry()
        style = geo.style
        w = geo.bbox.x1 - geo.bbox.x0
        h = geo.bbox.y1 - geo.bbox.y0
        rect = QRectF(0, 0, w, h)

        from ..annotation.models import BackgroundMode

        if style.background_mode is not BackgroundMode.NONE:
            bg = QColor(style.background_color)
            bg.setAlphaF(
                style.background_opacity if style.background_mode is BackgroundMode.TRANSLUCENT else 1.0
            )
            painter.setBrush(bg)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.drawRoundedRect(rect, style.corner_radius, style.corner_radius)

        font = _qfont(style)
        painter.setFont(font)
        text_color = QColor(style.text_color)

        if style.shadow:
            shadow_color = QColor(0, 0, 0, 160)
            painter.setPen(shadow_color)
            painter.drawText(
                rect.adjusted(style.padding + 1, style.padding + 1, -style.padding, -style.padding),
                Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                geo.text,
            )
        if style.outline:
            outline_color = QColor(style.outline_color)
            painter.setPen(outline_color)
            for dx, dy in ((-1, 0), (1, 0), (0, -1), (0, 1)):
                painter.drawText(
                    rect.adjusted(
                        style.padding + dx, style.padding + dy, -style.padding + dx, -style.padding + dy
                    ),
                    Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                    geo.text,
                )

        painter.setPen(text_color)
        painter.drawText(
            rect.adjusted(style.padding, style.padding, -style.padding, -style.padding),
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
            geo.text,
        )

        if self.selected_:
            sel_pen = QPen(_SELECTION_COLOR, 1.2)
            sel_pen.setStyle(Qt.PenStyle.DashLine)
            painter.setPen(sel_pen)
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawRoundedRect(rect.adjusted(-2, -2, 2, 2), style.corner_radius, style.corner_radius)

    def mousePressEvent(self, event):
        self.clicked.emit()
        self.setCursor(Qt.CursorShape.ClosedHandCursor)
        super().mousePressEvent(event)

    def mouseDoubleClickEvent(self, event) -> None:
        self.double_clicked.emit()
        super().mouseDoubleClickEvent(event)

    def contextMenuEvent(self, event) -> None:
        self.context_menu_requested.emit(event.screenPos())
        event.accept()

    def mouseReleaseEvent(self, event):
        self.setCursor(Qt.CursorShape.OpenHandCursor)
        super().mouseReleaseEvent(event)
        pos = self.pos()
        self.moved.emit(pos.x(), pos.y())

    def set_selected_visual(self, selected: bool) -> None:
        self.selected_ = selected
        self.update()


class ConnectorItem(QGraphicsPathItem):
    def __init__(
        self,
        annotation: Annotation,
        global_style: StylePreset,
        catalog_colors: dict[str, str] | None = None,
    ):
        super().__init__()
        self.annotation = annotation
        self.global_style = global_style
        self.catalog_colors = catalog_colors
        self.setZValue(5)

    def update_path(self, marker_geo, label_geo) -> None:
        connector_style = self.annotation.effective_connector_style(self.global_style)
        points = compute_connector_points(self.annotation, marker_geo, label_geo, connector_style)
        if not points:
            self.setPath(QPainterPath())
            self.setVisible(False)
            return
        self.setVisible(True)
        path = QPainterPath()
        path.moveTo(QPointF(*points[0]))
        if len(points) == 3:
            # CURVED: (start, control, end) -> quadratic bezier.
            path.quadTo(QPointF(*points[1]), QPointF(*points[2]))
        else:
            for p in points[1:]:
                path.lineTo(QPointF(*p))
        self.setPath(path)
        color = QColor(resolve_connector_color(self.annotation, self.global_style, self.catalog_colors))
        color.setAlphaF(0.85)
        connector_width = self.annotation.effective_connector_width(self.global_style)
        self.setPen(QPen(color, connector_width))
