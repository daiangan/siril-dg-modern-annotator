"""Interactive image canvas: QGraphicsView/QGraphicsScene with zoom/pan/fit (brief #8).

This is the *only* place that converts native image pixel coordinates to preview
(on-screen) coordinates and back, via the view's own transform — annotation items ask
this view to do that conversion rather than computing it themselves
(ARCHITECTURE.md #4).
"""

from __future__ import annotations

from PyQt6.QtCore import QRectF, Qt, pyqtSignal
from PyQt6.QtGui import QPainter, QPixmap, QTransform
from PyQt6.QtWidgets import QGraphicsPixmapItem, QGraphicsScene, QGraphicsView

_ZOOM_FACTOR_BASE = 1.25
_MIN_SCALE = 0.02
_MAX_SCALE = 40.0


class ImageView(QGraphicsView):
    zoom_changed = pyqtSignal(float)
    cursor_native_pos = pyqtSignal(float, float)
    background_clicked = pyqtSignal()  # click landed on empty space / the base image, not an item

    def __init__(self, parent=None):
        super().__init__(parent)
        self.scene_ = QGraphicsScene(self)
        self.setScene(self.scene_)
        self.setRenderHints(
            QPainter.RenderHint.Antialiasing | QPainter.RenderHint.SmoothPixmapTransform
        )
        self.setViewportUpdateMode(QGraphicsView.ViewportUpdateMode.SmartViewportUpdate)
        self.setTransformationAnchor(QGraphicsView.ViewportAnchor.AnchorUnderMouse)
        self.setResizeAnchor(QGraphicsView.ViewportAnchor.AnchorUnderMouse)
        self.setDragMode(QGraphicsView.DragMode.ScrollHandDrag)
        self.setMouseTracking(True)
        self.setBackgroundBrush(Qt.GlobalColor.black)

        self._pixmap_item: QGraphicsPixmapItem | None = None
        self._native_width = 0
        self._native_height = 0

    def set_base_image(self, pixmap: QPixmap, native_width: int, native_height: int) -> None:
        """pixmap may be a downscaled preview; native_width/height is always the true
        source resolution. All annotation coordinates are expressed in native space and
        scaled into this pixmap's coordinate system, so preview resolution never affects
        annotation placement accuracy.

        Replaces only the pixmap item, never clearing the whole scene -- switching the
        preview stretch mode calls this again after annotation items already exist, and
        a full scene.clear() would silently orphan them (real bug: markers/labels
        disappeared when re-loading the base image after catalog objects were fetched)."""
        self._native_width = native_width
        self._native_height = native_height
        scale_x = native_width / pixmap.width() if pixmap.width() else 1.0
        scale_y = native_height / pixmap.height() if pixmap.height() else 1.0
        if self._pixmap_item is None:
            self._pixmap_item = QGraphicsPixmapItem(pixmap)
            self._pixmap_item.setTransformationMode(Qt.TransformationMode.SmoothTransformation)
            self._pixmap_item.setZValue(-1)  # always behind markers/labels/connectors
            self.scene_.addItem(self._pixmap_item)
        else:
            self._pixmap_item.setPixmap(pixmap)
            self._pixmap_item.setTransform(QTransform())  # reset before reapplying scale
        self._pixmap_item.setTransform(self._pixmap_item.transform().scale(scale_x, scale_y))
        self.scene_.setSceneRect(QRectF(0, 0, native_width, native_height))

    @property
    def native_size(self) -> tuple[int, int]:
        return self._native_width, self._native_height

    def native_to_scene(self, x: float, y: float) -> tuple[float, float]:
        """Native image pixels map 1:1 onto scene coordinates by construction (the
        pixmap item is scaled up to native size above), so this is the identity — kept
        as an explicit named conversion point rather than relying on callers assuming it."""
        return x, y

    def scene_to_native(self, x: float, y: float) -> tuple[float, float]:
        return x, y

    # --- zoom / pan -----------------------------------------------------------------

    def wheelEvent(self, event):
        factor = _ZOOM_FACTOR_BASE if event.angleDelta().y() > 0 else 1 / _ZOOM_FACTOR_BASE
        new_scale = self.transform().m11() * factor
        if new_scale < _MIN_SCALE or new_scale > _MAX_SCALE:
            return
        self.scale(factor, factor)
        self.zoom_changed.emit(self.transform().m11())

    def mouseMoveEvent(self, event):
        super().mouseMoveEvent(event)
        pos = self.mapToScene(event.pos())
        self.cursor_native_pos.emit(pos.x(), pos.y())

    def mousePressEvent(self, event):
        # Per user request: clicking empty space (or the base image itself, not a
        # marker/label) deselects the current object -- there was previously no way to
        # get back to seeing every object without one held in the "selected" visual
        # state. MarkerItem/LabelItem consume clicks themselves (their own
        # mousePressEvent emits `clicked`); this only fires for whatever's left, i.e.
        # nothing under the cursor or the background pixmap.
        item = self.itemAt(event.pos())
        if item is None or item is self._pixmap_item:
            self.background_clicked.emit()
        super().mousePressEvent(event)

    def fit_to_window(self) -> None:
        if self._pixmap_item is None:
            return
        self.fitInView(self._pixmap_item, Qt.AspectRatioMode.KeepAspectRatio)
        self.zoom_changed.emit(self.transform().m11())

    def zoom_100(self) -> None:
        self.resetTransform()
        self.zoom_changed.emit(1.0)

    def reset_zoom(self) -> None:
        self.fit_to_window()
