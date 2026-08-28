"""Main application window (brief #4 layout, #41 MVP feature set).

Owns: the annotation list, the active global StylePreset, the undo stack, and every
signal wire-up between image_view / object_panel / style_panel / the graphics items.
This is the only module allowed to call SirilBridge methods (aside from the entry
point), and it only ever does so on the main thread.
"""

from __future__ import annotations

import logging
from pathlib import Path

import numpy as np
from PyQt6.QtCore import Qt, QTimer, QUrl
from PyQt6.QtGui import QAction, QDesktopServices, QImage, QKeySequence, QPixmap, QShortcut, QUndoStack
from PyQt6.QtWidgets import (
    QComboBox,
    QDockWidget,
    QFileDialog,
    QLabel,
    QMainWindow,
    QMenu,
    QMessageBox,
    QProgressDialog,
    QPushButton,
    QSizePolicy,
    QStatusBar,
    QTabWidget,
    QToolBar,
    QToolButton,
    QWidget,
)

from ..annotation.catalogs import (
    DEFAULT_CATALOG_COLORS,
    SUPPORTED_CATALOGS,
    CompositeProvider,
    LocalCsvProvider,
    VizierProvider,
)
from ..annotation.layout import auto_arrange
from ..annotation.models import Annotation, StylePreset
from ..annotation.pixel_utils import correct_fits_row_order, to_hwc_uint8
from ..annotation.renderer import (
    compute_connector_points,
    compute_label_geometry,
    compute_marker_geometry,
    default_max_marker_radius_px,
)
from ..annotation.wcs import NotPlateSolvedError, SirilWcs
from ..persistence import last_used as last_used_store
from ..persistence import presets as preset_store
from ..persistence.project import CatalogConfig, ExportSettings, ProjectData, load, project_path_for_image, save
from ..siril_bridge.interface import ImageInfo, NoImageLoadedError, SirilBridge, SirilBridgeError
from .annotation_item import ConnectorItem, LabelItem, MarkerItem, qt_text_measurer
from .commands import AnnotationFieldsCommand, AutoArrangeCommand, GlobalStyleChangeCommand, MoveLabelCommand, ToggleVisibilityCommand
from .export_dialog import ExportDialog
from .image_view import ImageView
from .object_panel import ObjectPanel
from .style_panel import StylePanel
from .workers import CatalogFetchWorker, ExportWorker

logger = logging.getLogger(__name__)


class CheckableMenu(QMenu):
    """A QMenu that stays open when a checkable action inside it is clicked, so toggling
    several checkboxes doesn't require reopening the menu after every single click
    (confirmed as real friction with the stock QMenu behavior, which closes on every
    action activation regardless of checkability)."""

    def mouseReleaseEvent(self, event) -> None:
        action = self.activeAction()
        if action is not None and action.isCheckable():
            action.trigger()
            event.accept()
            return
        super().mouseReleaseEvent(event)


# Every supported catalog is on by default, matching Siril's own catalog picker (which
# ships with all its catalogs checked) -- confirmed as a real gap when a user compared
# our default selection against Siril's own and found LDN missing even though our
# LocalCsvProvider already supports it; it just wasn't in this set.
_DEFAULT_CATALOGS = set(SUPPORTED_CATALOGS)

DONATE_URL = "https://www.paypal.com/donate/?hosted_button_id=QKSMSHKZWW7GA"


class MainWindow(QMainWindow):
    def __init__(self, bridge: SirilBridge):
        super().__init__()
        self.setWindowTitle("Siril Modern Annotator")
        self.resize(1440, 900)

        self.bridge = bridge
        self.undo_stack = QUndoStack(self)
        self.annotations: list[Annotation] = []
        # Restore the user's last-used general settings (brief: persist across sessions
        # and across different images, distinct from per-image Save/Load Layout project
        # files) so a fresh image starts from what they set up last time instead of
        # hardcoded defaults. `_has_saved_style` gates whether `_load_current_image`
        # still applies the resolution-scaled default preset (only on a first-ever run).
        saved_style = last_used_store.load_last_used_style()
        self._has_saved_style = saved_style is not None
        self.global_style_holder: list[StylePreset] = [saved_style or preset_store.default_preset()]
        self.wcs: SirilWcs | None = None
        self.image_info: ImageInfo | None = None
        self.arcsec_per_px: float | None = None
        self.icc_profile: bytes | None = None
        self.source_identifier: str = ""
        saved_catalogs = last_used_store.load_last_used_catalogs()
        self.active_catalogs: set[str] = saved_catalogs if saved_catalogs is not None else set(_DEFAULT_CATALOGS)
        # Per-catalog marker/connector color (brief #13-adjacent request). A single
        # dict, mutated in place (never reassigned wholesale) on every edit -- every
        # MarkerItem/ConnectorItem is handed this exact same object at construction, so
        # a color change is visible to all of them on their next repaint with no need
        # to walk the scene reassigning references, the way global_style has to.
        # Start from the shipped defaults, then overlay whatever was saved -- so a
        # catalog the user never touched (or one added in a later version, after their
        # last save) still gets a sensible default color instead of silently falling
        # back to the plain global marker/connector color forever.
        self.catalog_colors: dict[str, str] = dict(DEFAULT_CATALOG_COLORS)
        saved_catalog_colors = last_used_store.load_last_used_catalog_colors()
        if saved_catalog_colors is not None:
            self.catalog_colors.update(saved_catalog_colors)

        self.marker_items: dict[str, MarkerItem] = {}
        self.label_items: dict[str, LabelItem] = {}
        self.connector_items: dict[str, ConnectorItem] = {}
        self.selected_id: str | None = None

        self._pending_object_cmd: AnnotationFieldsCommand | None = None
        self._pending_object_target: str | None = None

        self._catalog_worker: CatalogFetchWorker | None = None
        self._export_worker: ExportWorker | None = None
        self._progress_dialog: QProgressDialog | None = None

        self._build_ui()
        self._build_shortcuts()
        self._load_current_image()

    # ------------------------------------------------------------------ UI setup ----

    def _build_ui(self) -> None:
        self.image_view = ImageView()
        self.setCentralWidget(self.image_view)
        self.image_view.zoom_changed.connect(self._on_zoom_changed)
        self.image_view.cursor_native_pos.connect(self._on_cursor_moved)
        self.image_view.background_clicked.connect(self._on_background_clicked)

        self.object_panel = ObjectPanel()
        self.object_panel.selection_changed.connect(self.select_annotation)
        self.object_panel.visibility_changed.connect(self._on_table_visibility_changed)
        self.object_panel.select_all_requested.connect(lambda ids: self._bulk_visibility(ids, True))
        self.object_panel.deselect_all_requested.connect(lambda ids: self._bulk_visibility(ids, False))
        self.object_panel.reset_requested.connect(self._reset_layout)

        self.style_panel = StylePanel()
        self.style_panel.set_global_style(self.global_style_holder[0])
        self.style_panel.set_catalog_colors(self.catalog_colors)
        self.style_panel.global_style_changed.connect(self._on_global_style_edited)
        self.style_panel.object_style_changed.connect(self._on_object_style_edited)
        self.style_panel.object_meta_changed.connect(self._on_object_meta_edited)
        self.style_panel.reset_style_requested.connect(self._on_reset_global_style)
        self.style_panel.catalog_color_changed.connect(self._on_catalog_color_changed)

        self.dock_tabs = QTabWidget()
        self.dock_tabs.addTab(self.object_panel, "Objects")
        self.dock_tabs.addTab(self.style_panel, "Style")
        dock = QDockWidget("Annotation Controls", self)
        dock.setWidget(self.dock_tabs)
        dock.setFeatures(QDockWidget.DockWidgetFeature.NoDockWidgetFeatures)
        self.addDockWidget(Qt.DockWidgetArea.LeftDockWidgetArea, dock)

        self._build_toolbar()
        self._build_status_bar()

    def _build_toolbar(self) -> None:
        toolbar = QToolBar("Main")
        toolbar.setMovable(False)
        self.addToolBar(toolbar)

        self.connection_label = QLabel("Connecting to Siril…")
        toolbar.addWidget(self.connection_label)
        toolbar.addSeparator()

        fit_btn = QPushButton("Fit")
        fit_btn.clicked.connect(self.image_view.fit_to_window)
        zoom_100_btn = QPushButton("100%")
        zoom_100_btn.clicked.connect(self.image_view.zoom_100)
        toolbar.addWidget(fit_btn)
        toolbar.addWidget(zoom_100_btn)
        toolbar.addSeparator()

        # brief #19-20: preview stretch control. We always defaulted to Siril's own
        # preview=True autostretch with no way to turn it off -- a real gap, since that
        # autostretch may not be what the user wants or expects to see. "Auto Stretch"
        # uses Siril's own get_image_pixeldata(preview=True); "Linear" uses the raw
        # (non-preview) pixel data through our own simple linear normalization
        # (annotation.pixel_utils), i.e. no stretch curve applied at all.
        toolbar.addWidget(QLabel("Preview:"))
        self.preview_mode_combo = QComboBox()
        self.preview_mode_combo.addItem("Linear", "linear")
        self.preview_mode_combo.addItem("Auto Stretch", "auto")
        # Deliberately not part of last-used persistence (unlike catalogs/style below):
        # this is a one-off viewing choice for comparing against Siril's own preview,
        # not a general preference the user wants to carry into their next session --
        # always starts at the "Linear" default.
        self.preview_mode_combo.currentIndexChanged.connect(self._on_preview_mode_changed)
        toolbar.addWidget(self.preview_mode_combo)
        toolbar.addSeparator()

        # brief #13: catalog toggle UI, matching Siril's own catalog picker (checkboxes
        # per catalog). This never existed before -- the active catalog set was a
        # hardcoded constant with no way to change it from the GUI at all. Uses
        # QToolButton (InstantPopup) rather than QPushButton.setMenu(), which rendered
        # its dropdown arrow visibly misaligned from the button text with our dark
        # stylesheet (confirmed by a real screenshot) -- QToolButton's menu-indicator is
        # a standard, separately styleable sub-control instead. The menu itself is a
        # CheckableMenu so toggling several catalogs doesn't require reopening the menu
        # after every single click (confirmed as real friction: default QMenu behavior
        # closes on every action activation).
        catalogs_btn = QToolButton()
        # A plain unicode arrow appended to the text, with the native menu-indicator
        # disabled in the stylesheet, rather than relying on QToolButton's built-in
        # indicator subcontrol -- a real screenshot showed that indicator rendering
        # directly on top of the button text with our stylesheet (Windows' native
        # style interacts with subcontrol-position/-origin QSS inconsistently), and a
        # character in the text itself can't overlap anything since it's laid out as
        # part of the normal text flow.
        catalogs_btn.setText("Catalogs ▾")
        catalogs_btn.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        catalogs_btn.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextOnly)
        self.catalogs_menu = CheckableMenu(self)
        self.catalog_actions: dict[str, QAction] = {}
        for key, label in SUPPORTED_CATALOGS.items():
            action = QAction(label, self, checkable=True)
            action.setChecked(key in self.active_catalogs)
            action.toggled.connect(lambda checked, k=key: self._on_catalog_toggled(k, checked))
            self.catalogs_menu.addAction(action)
            self.catalog_actions[key] = action
        catalogs_btn.setMenu(self.catalogs_menu)
        toolbar.addWidget(catalogs_btn)
        toolbar.addSeparator()

        auto_arrange_btn = QPushButton("Auto Arrange Labels")
        auto_arrange_btn.clicked.connect(self.run_auto_arrange)
        toolbar.addWidget(auto_arrange_btn)
        toolbar.addSeparator()

        save_btn = QPushButton("Save Layout")
        save_btn.clicked.connect(self.save_project)
        load_btn = QPushButton("Load Layout")
        load_btn.clicked.connect(self.load_project)
        toolbar.addWidget(save_btn)
        toolbar.addWidget(load_btn)
        toolbar.addSeparator()

        # Per user request: ordered last (Save Layout / Load Layout / Export), with a
        # distinct accent color so the primary "finish and save your work" action still
        # stands out from the neutral gray toolbar buttons around it.
        export_btn = QPushButton("Export")
        export_btn.clicked.connect(self.open_export_dialog)
        export_btn.setStyleSheet(
            "QPushButton {"
            "  background-color: #2f7dd1; border: 1px solid #4a90e2;"
            "  color: #ffffff; font-weight: 600; padding: 5px 14px;"
            "}"
            "QPushButton:hover { background-color: #3f8ae0; }"
            "QPushButton:pressed { background-color: #2569b8; }"
        )
        toolbar.addWidget(export_btn)

        close_btn = QPushButton("Close")
        close_btn.clicked.connect(self.close)
        toolbar.addWidget(close_btn)

        # Per user request: pinned to the toolbar's far right, separated from the
        # working buttons via an expanding spacer, so it never gets mistaken for part
        # of the normal editing workflow. Opens the donation link in the user's own
        # default browser (QDesktopServices), not anything embedded in this app.
        spacer = QWidget()
        spacer.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        toolbar.addWidget(spacer)

        donate_btn = QPushButton("☕ Buy Me a Coffee")
        donate_btn.setToolTip("Support this project — opens PayPal in your browser")
        donate_btn.clicked.connect(lambda: QDesktopServices.openUrl(QUrl(DONATE_URL)))
        donate_btn.setStyleSheet(
            "QPushButton {"
            "  background-color: #a9682f; border: 1px solid #c9853f;"
            "  color: #ffffff; font-weight: 600; padding: 5px 14px;"
            "}"
            "QPushButton:hover { background-color: #bd7638; }"
            "QPushButton:pressed { background-color: #8f5626; }"
        )
        toolbar.addWidget(donate_btn)

    def _build_status_bar(self) -> None:
        bar = QStatusBar()
        self.setStatusBar(bar)
        self.coord_label = QLabel("")
        self.zoom_label = QLabel("Zoom: 100%")
        self.selection_label = QLabel("")
        bar.addWidget(self.coord_label)
        bar.addPermanentWidget(self.selection_label)
        bar.addPermanentWidget(self.zoom_label)

    def _build_shortcuts(self) -> None:
        QShortcut(QKeySequence("Ctrl+S"), self, activated=self.save_project)
        QShortcut(QKeySequence("Ctrl+E"), self, activated=self.open_export_dialog)
        QShortcut(QKeySequence("Ctrl+Z"), self, activated=self.undo_stack.undo)
        QShortcut(QKeySequence("Ctrl+Shift+Z"), self, activated=self.undo_stack.redo)
        QShortcut(QKeySequence(Qt.Key.Key_Delete), self, activated=self._hide_selected)
        QShortcut(QKeySequence("F"), self, activated=self.image_view.fit_to_window)
        QShortcut(QKeySequence("1"), self, activated=self.image_view.zoom_100)
        QShortcut(QKeySequence("A"), self, activated=self.run_auto_arrange)

    # ------------------------------------------------------------- image loading ----

    def _load_current_image(self) -> None:
        try:
            if not self.bridge.is_image_loaded():
                QMessageBox.warning(self, "No Image", "No image is currently loaded in Siril.")
                return
            self.image_info = self.bridge.get_image_info()
            if not self.image_info.plate_solved:
                QMessageBox.warning(
                    self, "Not Plate Solved",
                    "The loaded image has no astrometric solution. Plate solve it in "
                    "Siril first, then relaunch Siril Modern Annotator.",
                )
                return
            header = self.bridge.get_wcs_header_dict()
            self.wcs = SirilWcs.from_header_dict(header, self.image_info.width, self.image_info.height)
            self.arcsec_per_px = self.wcs.pixel_scale_arcsec_per_px()
            self.icc_profile = self.bridge.get_image_icc_profile()
            # Prefer the actual loaded filename over the FITS OBJECT keyword -- per
            # user request, exports should be named after "the original image name",
            # and OBJECT is frequently blank/generic rather than reflecting the real
            # file on disk. get_loaded_image_filename() is a best-effort call (see its
            # own docstring) that returns None if unavailable, so this still falls back
            # to the previous OBJECT-based behavior exactly as before when it can't.
            loaded_filename = self.bridge.get_loaded_image_filename()
            self.source_identifier = (
                Path(loaded_filename).stem if loaded_filename else (self.image_info.object_name or "untitled")
            )

            # Marker/label geometry is native-pixel-space (ARCHITECTURE.md #4), so a
            # flat default size looks fine at one resolution and is nearly invisible at
            # another -- confirmed by real-world testing. Scale the starting style to
            # this image's actual resolution instead of using a fixed constant. Skip
            # this when the user already has a persisted last-used style, though: most
            # markers scale off each object's angular size at render time regardless
            # (renderer.py's size_from_angular_size), and per user request the starting
            # style should be "what I used last time", not a re-derived default.
            if not self._has_saved_style:
                self.global_style_holder[0] = preset_store.default_preset_for_image(
                    self.image_info.width, self.image_info.height
                )
            self.style_panel.set_global_style(self.global_style_holder[0])

            self.refresh_preview_image()
            self.connection_label.setText(
                f"Connected — {self.image_info.width}×{self.image_info.height}"
                f" — {self.arcsec_per_px:.2f}\"/px"
            )
            self.setWindowTitle(f"Siril Modern Annotator — {self.source_identifier}")
            self._start_catalog_fetch(self.active_catalogs)
        except (NoImageLoadedError, NotPlateSolvedError, SirilBridgeError) as exc:
            QMessageBox.critical(self, "Siril Modern Annotator", str(exc))
        except Exception:
            logger.exception("Failed to load current image")
            QMessageBox.critical(self, "Siril Modern Annotator", "Unexpected error loading the image; see log.")

    def refresh_preview_image(self, fit: bool = True) -> None:
        """Fetches pixel data per the current preview-mode selection and updates the
        canvas's base image. Called on initial load and whenever the user switches
        Auto Stretch <-> Linear (brief #19-20) -- annotation items are untouched
        (image_view.set_base_image no longer clears the scene)."""
        mode = self.preview_mode_combo.currentData() if hasattr(self, "preview_mode_combo") else "auto"
        try:
            if mode == "linear":
                pixel_data = self.bridge.get_full_pixeldata()
            else:
                pixel_data = self.bridge.get_preview_pixeldata()
        except Exception as exc:
            logger.exception("Failed to fetch preview pixel data (mode=%s)", mode)
            QMessageBox.warning(self, "Preview Failed", f"Could not load image preview:\n{exc}")
            return
        self._set_preview_image(pixel_data, fit=fit)

    def _on_preview_mode_changed(self) -> None:
        if self.image_info is not None:
            self.refresh_preview_image(fit=False)

    def _set_preview_image(self, preview: np.ndarray, fit: bool = True) -> None:
        data = correct_fits_row_order(to_hwc_uint8(preview))
        h, w = data.shape[0], data.shape[1]
        qimage = QImage(data.data, w, h, 3 * w, QImage.Format.Format_RGB888)
        pixmap = QPixmap.fromImage(qimage.copy())
        self.image_view.set_base_image(pixmap, self.image_info.width, self.image_info.height)
        if fit:
            # Deferred, not called synchronously here: on the very first load, this
            # runs from MainWindow.__init__, which itself runs *before*
            # modern_annotator.py's window.show() -- fitInView() at that point sees
            # the QGraphicsView's placeholder pre-layout size, not its real on-screen
            # size, and computes the wrong zoom. QTimer.singleShot(0, ...) defers the
            # call until the event loop actually starts (after the window is shown and
            # laid out), which is the standard Qt fix for "fit to window looks wrong on
            # first show" -- confirmed as a real user-reported bug, not a hypothetical.
            QTimer.singleShot(0, self.image_view.fit_to_window)

    # ----------------------------------------------------------- catalog fetching ----

    def _catalog_provider(self) -> CompositeProvider:
        return CompositeProvider(
            [VizierProvider(), LocalCsvProvider(self.bridge.get_system_catalogue_dir())]
        )

    def _start_catalog_fetch(self, catalogs: set[str]) -> None:
        if self.wcs is None:
            return
        self._catalog_worker = CatalogFetchWorker(self._catalog_provider(), self.wcs, catalogs, mag_limit=None)
        self._catalog_worker.progress.connect(lambda msg: self.connection_label.setText(msg))
        self._catalog_worker.succeeded.connect(self._on_catalog_results)
        self._catalog_worker.failed.connect(self._on_catalog_failed)
        self._catalog_worker.start()

    def _on_catalog_results(self, annotations: list[Annotation]) -> None:
        self.annotations = annotations
        auto_arrange(
            self.annotations, self.global_style_holder[0],
            self.image_info.width, self.image_info.height,
            text_measurer=qt_text_measurer(None),
            marker_radius_fn=self._marker_radius_fn(),
        )
        self._rebuild_scene()
        self.object_panel.set_annotations(self.annotations)
        self.object_panel.set_name_display_mode(self.global_style_holder[0].label_style.name_display)
        self.connection_label.setText(f"{len(self.annotations)} objects — {self.source_identifier}")

    def _on_catalog_toggled(self, catalog: str, checked: bool) -> None:
        if checked:
            self.active_catalogs.add(catalog)
            self._fetch_additional_catalog(catalog)
        else:
            self.active_catalogs.discard(catalog)
            self._remove_catalog_objects(catalog)
        last_used_store.save_last_used_catalogs(self.active_catalogs)

    def _on_catalog_color_changed(self, catalog: str, color: str) -> None:
        # Mutates catalog_colors in place (not a reassignment) -- every MarkerItem/
        # LabelItem/ConnectorItem was handed this exact dict at construction (see
        # _add_scene_items_for), so they all pick up the new color on their next
        # repaint without needing a full _refresh_all-style walk to reassign a
        # reference, the way global_style changes do.
        self.catalog_colors[catalog] = color
        last_used_store.save_last_used_catalog_colors(self.catalog_colors)
        for ann in self.annotations:
            if ann.catalog != catalog:
                continue
            marker = self.marker_items.get(ann.id)
            if marker is not None:
                marker.prepareGeometryChange()
                marker.update()
            label = self.label_items.get(ann.id)
            if label is not None:
                # Only repaints -- background color doesn't affect the label's box
                # size (font metrics/padding do), so no prepareGeometryChange needed.
                label.update()
            self._update_connector(ann)
        self.image_view.scene_.update()

    def _fetch_additional_catalog(self, catalog: str) -> None:
        if self.wcs is None:
            return
        worker = CatalogFetchWorker(self._catalog_provider(), self.wcs, {catalog}, mag_limit=None)
        worker.progress.connect(lambda msg: self.connection_label.setText(msg))
        worker.succeeded.connect(lambda results, cat=catalog: self._on_additional_catalog_results(cat, results))
        worker.failed.connect(self._on_catalog_failed)
        self._catalog_worker = worker  # keep a reference so it isn't garbage-collected mid-flight
        worker.start()

    def _on_additional_catalog_results(self, catalog: str, results: list[Annotation]) -> None:
        # De-dupe against what's already loaded (angular proximity, same rule as
        # CompositeProvider) so re-enabling a catalog doesn't duplicate objects another
        # already-active catalog also happens to carry (e.g. Messier + NGC overlap).
        threshold_deg = 30.0 / 3600.0
        existing = self.annotations
        new_ones = [
            r for r in results
            if not any(abs(r.ra - e.ra) < threshold_deg and abs(r.dec - e.dec) < threshold_deg for e in existing)
        ]
        if not new_ones:
            self.connection_label.setText(f"{len(self.annotations)} objects — {self.source_identifier}")
            return
        self.annotations.extend(new_ones)
        for ann in new_ones:
            self._add_scene_items_for(ann)
        auto_arrange(
            self.annotations, self.global_style_holder[0],
            self.image_info.width, self.image_info.height,
            text_measurer=qt_text_measurer(None),
            marker_radius_fn=self._marker_radius_fn(),
        )
        self._refresh_all()
        self.object_panel.set_annotations(self.annotations)
        self.connection_label.setText(f"{len(self.annotations)} objects — {self.source_identifier}")

    def _remove_catalog_objects(self, catalog: str) -> None:
        removed_ids = {a.id for a in self.annotations if a.catalog == catalog}
        if not removed_ids:
            return
        self.annotations = [a for a in self.annotations if a.id not in removed_ids]
        for annotation_id in removed_ids:
            for d in (self.marker_items, self.label_items, self.connector_items):
                item = d.pop(annotation_id, None)
                if item is not None and item.scene() is not None:
                    item.scene().removeItem(item)
        if self.selected_id in removed_ids:
            self.selected_id = None
            self.style_panel.set_selected_annotation(None)
        self.object_panel.set_annotations(self.annotations)
        self.connection_label.setText(f"{len(self.annotations)} objects — {self.source_identifier}")

    def _on_catalog_failed(self, message: str) -> None:
        logger.error("Catalog fetch failed: %s", message)
        QMessageBox.warning(
            self, "Catalog Query Failed",
            f"Could not fetch catalog objects (check your internet connection):\n{message}",
        )

    # --------------------------------------------------------------- scene sync ----

    def _rebuild_scene(self) -> None:
        for d in (self.marker_items, self.label_items, self.connector_items):
            for item in d.values():
                if item.scene() is not None:
                    item.scene().removeItem(item)
            d.clear()
        for ann in self.annotations:
            self._add_scene_items_for(ann)

    def _add_scene_items_for(self, ann: Annotation) -> None:
        """Creates and wires the marker/label/connector scene items for one annotation.
        Shared by _rebuild_scene (full reload) and catalog toggling (incremental add,
        see _fetch_additional_catalog) so both stay in sync with exactly one code path."""
        style = self.global_style_holder[0]
        marker = MarkerItem(ann, style, self.arcsec_per_px, self.max_marker_radius_px, self.catalog_colors)
        label = LabelItem(ann, style, qt_text_measurer(None), self.catalog_colors)
        connector = ConnectorItem(ann, style, self.catalog_colors)
        marker.clicked.connect(lambda a=ann: self.select_annotation(a.id))
        label.clicked.connect(lambda a=ann: self.select_annotation(a.id))
        label.moved.connect(lambda x, y, a=ann: self._on_label_moved(a, x, y))
        marker.context_menu_requested.connect(lambda pos, a=ann: self._show_object_context_menu(a, pos))
        label.context_menu_requested.connect(lambda pos, a=ann: self._show_object_context_menu(a, pos))
        marker.double_clicked.connect(lambda a=ann: self._on_object_double_clicked(a.id))
        label.double_clicked.connect(lambda a=ann: self._on_object_double_clicked(a.id))
        self.image_view.scene_.addItem(connector)
        self.image_view.scene_.addItem(marker)
        self.image_view.scene_.addItem(label)
        marker.setVisible(ann.enabled)
        label.setVisible(ann.enabled)
        self.marker_items[ann.id] = marker
        self.label_items[ann.id] = label
        self.connector_items[ann.id] = connector
        self._update_connector(ann)

    def _update_connector(self, ann: Annotation) -> None:
        connector = self.connector_items.get(ann.id)
        if connector is None:
            return
        if not ann.enabled:
            # Hiding an object via the Objects panel hides its marker/label, but the
            # connector item is separate and wasn't being hidden with them -- confirmed
            # by a real screenshot showing a dangling connector line for an unchecked
            # object with no marker or label attached to either end of it.
            connector.setVisible(False)
            return
        style = self.global_style_holder[0]
        marker_geo = compute_marker_geometry(
            ann, style, self.arcsec_per_px, self.max_marker_radius_px, self.catalog_colors
        )
        label_geo = compute_label_geometry(ann, style, qt_text_measurer(None), self.catalog_colors)
        connector.update_path(marker_geo, label_geo)

    def _refresh_annotation(self, ann: Annotation) -> None:
        marker = self.marker_items.get(ann.id)
        label = self.label_items.get(ann.id)
        if marker is not None:
            marker.prepareGeometryChange()
            marker.setVisible(ann.enabled)
            marker.update()
        if label is not None:
            label.setVisible(ann.enabled)
            label._sync_pos_from_model()
            label.update()
        self._update_connector(ann)
        self.object_panel.refresh()

    def _refresh_all(self) -> None:
        style = self.global_style_holder[0]
        for ann in self.annotations:
            marker = self.marker_items.get(ann.id)
            label = self.label_items.get(ann.id)
            if marker is not None:
                marker.global_style = style
                marker.prepareGeometryChange()
                marker.setVisible(ann.enabled)
            if label is not None:
                label.global_style = style
                label._sync_pos_from_model()
                label.setVisible(ann.enabled)
            connector = self.connector_items.get(ann.id)
            if connector is not None:
                connector.global_style = style
                self._update_connector(ann)
        self.image_view.scene_.update()
        self.object_panel.refresh()
        self.object_panel.set_name_display_mode(style.label_style.name_display)
        self.style_panel.set_global_style(style)

    def _find_annotation(self, annotation_id: str) -> Annotation | None:
        return next((a for a in self.annotations if a.id == annotation_id), None)

    def _marker_radius_fn(self):
        """Real rendered marker radius (angular-size-scaled + capped), for auto_arrange
        -- see layout.py's marker_radius_fn docstring for why the flat style radius
        alone is wrong once angular-size scaling is in play."""
        style = self.global_style_holder[0]
        return lambda a: compute_marker_geometry(
            a, style, self.arcsec_per_px, self.max_marker_radius_px
        ).radius

    @property
    def max_marker_radius_px(self) -> float | None:
        """Caps angular-size-derived marker radii (renderer.py) so a catalog entry with
        a huge real apparent size relative to a tight field of view (e.g. M31's full
        ~178' extent inside a much smaller frame) can't draw a circle bigger than the
        image itself."""
        if self.image_info is None:
            return None
        return default_max_marker_radius_px(self.image_info.width, self.image_info.height)

    # ------------------------------------------------------------ selection sync ----

    def select_annotation(self, annotation_id: str | None) -> None:
        if self.selected_id == annotation_id:
            return
        if self.selected_id is not None:
            self._set_item_selected(self.selected_id, False)
        self.selected_id = annotation_id
        if annotation_id is not None:
            self._set_item_selected(annotation_id, True)
        self._pending_object_cmd = None
        ann = self._find_annotation(annotation_id) if annotation_id is not None else None
        self.style_panel.set_selected_annotation(ann)
        if annotation_id is not None:
            self.object_panel.select_annotation(annotation_id)
            self.selection_label.setText(f"{ann.display_name(self.global_style_holder[0].label_style.name_display)}  |  RA {ann.ra:.4f}  Dec {ann.dec:.4f}")
        else:
            self.object_panel.clear_selection()
            self.selection_label.setText("")

    def _on_background_clicked(self) -> None:
        self.select_annotation(None)

    def _set_item_selected(self, annotation_id: str, selected: bool) -> None:
        marker = self.marker_items.get(annotation_id)
        label = self.label_items.get(annotation_id)
        if marker is not None:
            marker.set_selected_visual(selected)
        if label is not None:
            label.set_selected_visual(selected)

    # -------------------------------------------------------------- interactions ----

    def _on_label_moved(self, ann: Annotation, new_x: float, new_y: float) -> None:
        old_pos = (ann.label_x, ann.label_y)
        old_manual = ann.manually_positioned
        cmd = MoveLabelCommand(ann, old_pos, (new_x, new_y), old_manual, lambda a=ann: self._refresh_annotation(a))
        self.undo_stack.push(cmd)

    def _on_table_visibility_changed(self, annotation_id: str, enabled: bool) -> None:
        ann = self._find_annotation(annotation_id)
        if ann is None:
            return
        cmd = ToggleVisibilityCommand(ann, enabled, lambda a=ann: self._refresh_annotation(a))
        self.undo_stack.push(cmd)

    def _bulk_visibility(self, annotation_ids: list[str], enabled: bool) -> None:
        # Scoped to annotation_ids (the Objects table's *currently filtered* rows, per
        # object_panel.py's _filtered_annotation_ids), not every object in
        # self.annotations regardless of the search box/catalog filter -- lets a user
        # filter down to one catalog and hide/show just that batch in one click.
        self.undo_stack.beginMacro("Show All" if enabled else "Hide All")
        for annotation_id in annotation_ids:
            ann = self._find_annotation(annotation_id)
            if ann is not None and ann.enabled != enabled:
                self.undo_stack.push(ToggleVisibilityCommand(ann, enabled, lambda a=ann: self._refresh_annotation(a)))
        self.undo_stack.endMacro()

    def _reset_layout(self) -> None:
        for ann in self.annotations:
            ann.manually_positioned = False
        self.run_auto_arrange()

    def _hide_selected(self) -> None:
        if self.selected_id is None:
            return
        ann = self._find_annotation(self.selected_id)
        if ann is not None:
            self.undo_stack.push(ToggleVisibilityCommand(ann, False, lambda a=ann: self._refresh_annotation(a)))

    def _show_object_context_menu(self, ann: Annotation, screen_pos) -> None:
        # Right-clicking selects the object first (same as a left click) so it's clear
        # what the menu applies to, then offers a quick hide -- the marker/label being
        # right-clicked is by definition currently visible (a hidden item can't be
        # clicked), so "Hide" is the only visibility action that ever makes sense here;
        # re-showing something stays in the Objects panel, which lists hidden objects
        # too.
        self.select_annotation(ann.id)
        name = ann.display_name(self.global_style_holder[0].label_style.name_display)
        menu = QMenu(self)
        hide_action = menu.addAction(f"Hide {name}")
        hide_action.triggered.connect(lambda: self._on_table_visibility_changed(ann.id, False))
        menu.exec(screen_pos)

    def _on_object_double_clicked(self, annotation_id: str) -> None:
        # Per user request: double-clicking an object on the canvas should land the
        # user directly on its own controls, not just select it and leave them to
        # click over to the Style tab (and then to "Selected Object" inside that)
        # themselves. Switches both the outer Objects/Style dock tab and the inner
        # Global Style/Selected Object/Catalog Colors tab.
        self.select_annotation(annotation_id)
        self.dock_tabs.setCurrentWidget(self.style_panel)
        self.style_panel.show_object_tab()

    def run_auto_arrange(self) -> None:
        if not self.annotations:
            return
        before = {a.id: (a.label_x, a.label_y, a.manually_positioned) for a in self.annotations}
        auto_arrange(
            self.annotations, self.global_style_holder[0],
            self.image_info.width, self.image_info.height,
            text_measurer=qt_text_measurer(None), keep_manual=True,
            marker_radius_fn=self._marker_radius_fn(),
        )
        cmd = AutoArrangeCommand(self.annotations, before, self._refresh_all)
        self.undo_stack.push(cmd)

    # ------------------------------------------------------------------ styling ----

    def _on_global_style_edited(self) -> None:
        new_style = self.style_panel.global_style()
        old_style = self.global_style_holder[0]
        cmd = GlobalStyleChangeCommand(self.global_style_holder, old_style, new_style, self._refresh_all)
        self.undo_stack.push(cmd)
        self._has_saved_style = True
        last_used_store.save_last_used_style(new_style)

    def _on_reset_global_style(self) -> None:
        if self.image_info is None:
            return
        new_style = preset_store.default_preset_for_image(self.image_info.width, self.image_info.height)
        old_style = self.global_style_holder[0]
        cmd = GlobalStyleChangeCommand(self.global_style_holder, old_style, new_style, self._refresh_all)
        self.undo_stack.push(cmd)
        self._has_saved_style = True
        last_used_store.save_last_used_style(new_style)

    def _on_object_style_edited(self, annotation_id: str) -> None:
        ann = self._find_annotation(annotation_id)
        if ann is None:
            return
        new_values = self.style_panel.pending_object_style_values()
        self._push_or_merge_object_command(ann, new_values, f"Change style: {ann.catalog_name}")

    def _on_object_meta_edited(self, annotation_id: str) -> None:
        ann = self._find_annotation(annotation_id)
        if ann is None:
            return
        new_values = self.style_panel.pending_object_meta_values()
        self._push_or_merge_object_command(ann, new_values, f"Edit: {ann.catalog_name}")

    def _push_or_merge_object_command(self, ann: Annotation, new_values: dict, text: str) -> None:
        if self._pending_object_cmd is not None and self._pending_object_target == ann.id:
            self._pending_object_cmd.new_values.update(new_values)
            self._pending_object_cmd.redo()
            return
        old_values = {k: getattr(ann, k) for k in new_values}
        cmd = AnnotationFieldsCommand(ann, old_values, dict(new_values), text, lambda a=ann: self._refresh_annotation(a))
        self.undo_stack.push(cmd)
        self._pending_object_cmd = cmd
        self._pending_object_target = ann.id

    # -------------------------------------------------------------- status bar ----

    def _on_zoom_changed(self, scale: float) -> None:
        self.zoom_label.setText(f"Zoom: {scale * 100:.0f}%")

    def _on_cursor_moved(self, x: float, y: float) -> None:
        if self.wcs is None:
            self.coord_label.setText(f"x={x:.0f} y={y:.0f}")
            return
        try:
            ra, dec = self.wcs.pixel_to_world(x, y)
            self.coord_label.setText(f"RA {ra:.5f}  Dec {dec:.5f}   |   x={x:.0f} y={y:.0f}")
        except Exception:
            self.coord_label.setText(f"x={x:.0f} y={y:.0f}")

    # ------------------------------------------------------------------ export ----

    def open_export_dialog(self) -> None:
        if self.image_info is None:
            return
        dialog = ExportDialog(self.image_info.width, self.image_info.height, self)
        if dialog.exec():
            settings = dialog.export_settings()
            self._run_export(settings)

    def _run_export(self, settings: ExportSettings) -> None:
        default_name = f"{self.source_identifier}_annotated.{self._extension_for(settings.format)}"
        path_str, _ = QFileDialog.getSaveFileName(self, "Export Annotated Image", default_name)
        if not path_str:
            return
        try:
            pixel_data = self.bridge.get_full_pixeldata()
        except Exception as exc:
            QMessageBox.critical(self, "Export Failed", f"Could not read full-resolution image data:\n{exc}")
            return

        self._progress_dialog = QProgressDialog("Exporting…", None, 0, 0, self)
        self._progress_dialog.setWindowModality(Qt.WindowModality.WindowModal)
        self._progress_dialog.show()

        self._export_worker = ExportWorker(
            Path(path_str), pixel_data, self.annotations, self.global_style_holder[0],
            settings, self.arcsec_per_px, self.icc_profile,
            catalog_colors=self.catalog_colors,
        )
        self._export_worker.progress.connect(self._progress_dialog.setLabelText)
        self._export_worker.succeeded.connect(self._on_export_succeeded)
        self._export_worker.failed.connect(self._on_export_failed)
        self._export_worker.start()

    def _on_export_succeeded(self, path: str) -> None:
        if self._progress_dialog:
            self._progress_dialog.close()
        QMessageBox.information(self, "Export Complete", f"Saved to:\n{path}")

    def _on_export_failed(self, message: str) -> None:
        if self._progress_dialog:
            self._progress_dialog.close()
        QMessageBox.critical(self, "Export Failed", message)

    @staticmethod
    def _extension_for(fmt: str) -> str:
        return {"jpeg": "jpg", "png": "png", "tiff8": "tif", "tiff16": "tif"}.get(fmt, "tif")

    # --------------------------------------------------------------- persistence ----

    def save_project(self) -> None:
        if self.image_info is None:
            return
        path_str, _ = QFileDialog.getSaveFileName(
            self, "Save Annotation Layout",
            str(project_path_for_image(Path(f"{self.source_identifier}.fits"))),
            "Annotation Layout (*.annotations.json)",
        )
        if not path_str:
            return
        project = ProjectData(
            source_width=self.image_info.width,
            source_height=self.image_info.height,
            source_identifier=self.source_identifier,
            catalog_config=CatalogConfig(enabled_catalogs=self.active_catalogs),
            global_style=self.global_style_holder[0],
            annotations=self.annotations,
            export_settings=ExportSettings(),
        )
        try:
            save(Path(path_str), project)
            QMessageBox.information(self, "Saved", f"Annotation layout saved to:\n{path_str}")
        except Exception as exc:
            QMessageBox.critical(self, "Save Failed", str(exc))

    def load_project(self) -> None:
        path_str, _ = QFileDialog.getOpenFileName(
            self, "Load Annotation Layout", "", "Annotation Layout (*.annotations.json)"
        )
        if not path_str:
            return
        try:
            project = load(Path(path_str))
        except Exception as exc:
            QMessageBox.critical(self, "Load Failed", str(exc))
            return
        if self.image_info and (
            project.source_width != self.image_info.width or project.source_height != self.image_info.height
        ):
            QMessageBox.warning(
                self, "Dimension Mismatch",
                "This layout was saved for a different image resolution "
                f"({project.source_width}x{project.source_height} vs "
                f"{self.image_info.width}x{self.image_info.height}). Loading anyway.",
            )
        self.annotations = project.annotations
        self.global_style_holder[0] = project.global_style
        self.active_catalogs = set(project.catalog_config.enabled_catalogs)
        for key, action in self.catalog_actions.items():
            action.blockSignals(True)
            action.setChecked(key in self.active_catalogs)
            action.blockSignals(False)
        self.undo_stack.clear()
        self._rebuild_scene()
        self.object_panel.set_annotations(self.annotations)
        self.style_panel.set_global_style(self.global_style_holder[0])
