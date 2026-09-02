"""Main application window (brief #4 layout, #41 MVP feature set).

Owns: the annotation list, the active global StylePreset, the undo stack, and every
signal wire-up between image_view / object_panel / style_panel / the graphics items.
This is the only module allowed to call SirilBridge methods (aside from the entry
point), and it only ever does so on the main thread.
"""

from __future__ import annotations

import logging
from dataclasses import fields as dataclass_fields
from dataclasses import replace
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

from .. import __version__
from ..annotation.catalogs import (
    DEFAULT_CATALOG_COLORS,
    ONLINE_ONLY_CATALOGS,
    SUPPORTED_CATALOGS,
    USER_CATALOG_FILES,
    CompositeProvider,
    GumProvider,
    LocalCsvProvider,
    RcwCorrectedPositionProvider,
    Sh2CorrectedPositionProvider,
    VizierProvider,
    count_local_catalog_entries,
    vizier_is_available,
)
from ..annotation.catalogs import _same_dedup_class
from ..annotation.constellations import load_constellation_lines, load_constellation_names
from ..annotation.layout import auto_arrange
from ..annotation.models import (
    Annotation,
    MarkerShape,
    MarkerStyle,
    OverlaySettings,
    StylePreset,
    default_priority_for_catalog,
)
from ..annotation.pixel_utils import correct_fits_row_order, to_hwc_uint8
from ..annotation.renderer import (
    compute_compass_geometry,
    compute_connector_points,
    compute_info_box_geometry,
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
from .overlay_item import CompassItem, ConstellationLinesItem, GridItem, InfoBoxItem
from .commands import (
    AddAnnotationCommand,
    AnnotationFieldsCommand,
    AutoArrangeCommand,
    DeleteAnnotationCommand,
    GlobalStyleChangeCommand,
    MoveCompassCommand,
    MoveInfoBoxCommand,
    MoveLabelCommand,
    MoveMarkerCommand,
    ToggleVisibilityCommand,
)
from .export_dialog import ExportDialog
from .image_view import ImageView
from .object_panel import ObjectPanel, simbad_url_for
from .style_panel import StylePanel
from .tools_panel import ToolsPanel
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
# LocalCsvProvider already supports it; it just wasn't in this set. ONLINE_ONLY_CATALOGS
# (no bundled local file, e.g. Barnard) is the one deliberate exception: per user
# request, those start unchecked rather than silently returning nothing for a
# first-time/offline user with no explanation.
#
# "user_dso" (Siril's own Astrometry > Annotate > Search Object list) is a second,
# different kind of exception: whether it should default on isn't a static fact like
# ONLINE_ONLY_CATALOGS, it depends on how many entries this particular installation's
# catalogue already has (per user request: on while it's still a short, deliberately
# curated list, off once it's grown past that). Excluded here and added back
# conditionally in MainWindow.__init__, which is the earliest point a live bridge
# connection (needed to read the count) is available.
_DEFAULT_CATALOGS = set(SUPPORTED_CATALOGS) - ONLINE_ONLY_CATALOGS - {"user_dso"}
_USER_DSO_DEFAULT_ON_MAX_ENTRIES = 10

DONATE_URL = "https://www.paypal.com/donate/?hosted_button_id=QKSMSHKZWW7GA"

# Shown in the window title (with __version__ appended) so the user always knows which
# build they're running -- per user request. Also fixes a stale rename: every other
# user-facing surface (the Siril log banner, the built script's filename) already says
# "DG Modern Annotator", but this window title was missed when that rename happened.
APP_TITLE = "DG Modern Annotator"


class MainWindow(QMainWindow):
    def __init__(self, bridge: SirilBridge):
        super().__init__()
        self.setWindowTitle(f"{APP_TITLE} v{__version__}")
        self.resize(1440, 900)

        self.bridge = bridge
        # Static, image-independent data (unlike every CatalogProvider query, this
        # doesn't depend on the WCS at all) -- loaded once here rather than re-read
        # from disk on every image load. Empty lists (not a crash) if this Siril
        # install's catalogue dir happens to lack the files -- see
        # annotation/constellations.py's own loader docstrings.
        self._constellation_lines = load_constellation_lines(self.bridge.get_system_catalogue_dir())
        self._constellation_names = load_constellation_names(self.bridge.get_system_catalogue_dir())
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
        if saved_catalogs is not None:
            self.active_catalogs: set[str] = saved_catalogs
        else:
            self.active_catalogs = set(_DEFAULT_CATALOGS)
            if self._user_dso_catalog_should_default_on():
                self.active_catalogs.add("user_dso")
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
        # Items just removed from the scene (see _remove_scene_items_for and every
        # other _defer_item_cleanup call site), kept alive here for the rest of the
        # session rather than dropped. QGraphicsScene.removeItem() detaches an item,
        # but the scene's own internal spatial index isn't necessarily fully rebuilt by
        # the time it returns -- a later repaint, triggered by something completely
        # unrelated to the item itself (an unrelated toolbar click, or an ordinary
        # repaint while dragging a *different* item), can still walk that index and
        # touch a just-removed item. If Python has already destroyed the underlying
        # C++ object by then, that's a real, confirmed use-after-free crash (multiple
        # native SIGABRT/SIGSEGV crash reports, all inside QGraphicsView::paintEvent's
        # item traversal) -- see _defer_item_cleanup's own docstring.
        #
        # This used to release each batch after a fixed 250ms timer. That was not
        # enough: crash reports kept recurring specifically while the user was also
        # dragging a label, which floods the event queue with geometry-change/repaint
        # events -- under that load a repaint queued *before* removal can still be
        # processed *after* the 250ms timer already freed the item. No fixed delay is
        # provably safe against an arbitrarily backed-up event queue, so batches are no
        # longer auto-flushed at all -- they stay here, permanently, for the life of
        # the session. These are lightweight item wrappers and a session only
        # accumulates a small, bounded number of them, so the memory cost is
        # negligible next to the alternative (a real, user-facing crash).
        self._pending_item_cleanup: list[list[object]] = []
        self.selected_id: str | None = None

        # Placeholder only -- _load_current_image() immediately replaces this with
        # presets.default_overlay_settings_for_image() (resolution-scaled sizing) plus
        # last_used_store.apply_last_used_overlay_settings() (restored on/off state and
        # non-size-dependent style, per user request). Also persisted per-image in a
        # saved project file (see persistence/project.py).
        self.overlay_settings = OverlaySettings()
        self.grid_item: GridItem | None = None
        self.compass_item: CompassItem | None = None
        self.info_box_item: InfoBoxItem | None = None
        self.constellation_item: ConstellationLinesItem | None = None

        self._pending_object_cmd: AnnotationFieldsCommand | None = None
        self._pending_object_target: str | None = None

        self._catalog_worker: CatalogFetchWorker | None = None
        self._export_worker: ExportWorker | None = None
        self._progress_dialog: QProgressDialog | None = None

        self._build_ui()
        self._build_shortcuts()
        # Deferred, not called directly here: modern_annotator.py's main() does
        # `window = MainWindow(bridge); window.show()`, so calling this synchronously
        # from inside this constructor would run its whole synchronous sirilpy call
        # chain (pixel data fetch, WCS setup) *before* the window is ever shown --
        # the user sees nothing at all until it's fully done. QTimer.singleShot(0, ...)
        # defers this exactly like _set_preview_image's fit_to_window call below
        # already does for the same underlying reason -- runs on the next event-loop
        # tick, right after show() has actually put the window on screen, so at least
        # the window shell itself appears immediately. _build_ui() doesn't read
        # image_info/wcs (both stay None until this runs), so nothing here depends on
        # the image being loaded before the window appears.
        QTimer.singleShot(0, self._load_current_image)

    # ------------------------------------------------------------------ UI setup ----

    def _build_ui(self) -> None:
        self.image_view = ImageView()
        self.setCentralWidget(self.image_view)
        self.image_view.zoom_changed.connect(self._on_zoom_changed)
        self.image_view.cursor_native_pos.connect(self._on_cursor_moved)
        self.image_view.background_clicked.connect(self._on_background_clicked)
        # Deferred, same reasoning as the marker/label context_menu_requested wiring in
        # _add_scene_items_for -- background_context_menu_requested is emitted
        # synchronously from inside ImageView's own contextMenuEvent C++ frame.
        self.image_view.background_context_menu_requested.connect(
            lambda x, y, pos: QTimer.singleShot(0, lambda: self._show_add_custom_object_menu(x, y, pos))
        )

        self.object_panel = ObjectPanel()
        self.object_panel.selection_changed.connect(self.select_annotation)
        self.object_panel.visibility_changed.connect(self._on_table_visibility_changed)
        self.object_panel.select_all_requested.connect(lambda ids: self._bulk_visibility(ids, True))
        self.object_panel.deselect_all_requested.connect(lambda ids: self._bulk_visibility(ids, False))
        self.object_panel.reset_requested.connect(self._reset_layout)
        self.object_panel.object_double_clicked.connect(self._on_object_double_clicked)

        self.style_panel = StylePanel()
        self.style_panel.set_global_style(self.global_style_holder[0])
        self.style_panel.set_catalog_colors(self.catalog_colors)
        self.style_panel.global_style_changed.connect(self._on_global_style_edited)
        self.style_panel.object_style_changed.connect(self._on_object_style_edited)
        self.style_panel.object_meta_changed.connect(self._on_object_meta_edited)
        self.style_panel.reset_style_requested.connect(self._on_reset_global_style)
        self.style_panel.reset_marker_position_requested.connect(self._reset_selected_marker_position)
        self.style_panel.catalog_color_changed.connect(self._on_catalog_color_changed)
        self.style_panel.overlay_settings_changed.connect(self._on_overlay_style_edited)
        self.style_panel.reset_compass_position_requested.connect(self._reset_compass_position)
        self.style_panel.reset_info_box_position_requested.connect(self._reset_info_box_position)
        self.style_panel.set_overlay_settings(self.overlay_settings)

        self.tools_panel = ToolsPanel()
        self.tools_panel.auto_arrange_requested.connect(self.run_auto_arrange)
        self.tools_panel.save_layout_requested.connect(self.save_project)
        self.tools_panel.load_layout_requested.connect(self.load_project)

        self.dock_tabs = QTabWidget()
        self.dock_tabs.addTab(self.object_panel, "Objects")
        self.dock_tabs.addTab(self.style_panel, "Style")
        self.dock_tabs.addTab(self.tools_panel, "Tools")
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

        # RA/Dec grid + compass: off by default, quick on/off here (brief: "should be
        # turned off by default and then displayed if the user wants") -- style/color
        # customization lives in the Style panel's Overlays tab, same "quick toggle
        # here, full editing there" split as the Catalogs button above.
        overlays_btn = QToolButton()
        overlays_btn.setText("Overlays ▾")
        overlays_btn.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        overlays_btn.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextOnly)
        self.overlays_menu = CheckableMenu(self)
        self.grid_action = QAction("RA/Dec Grid", self, checkable=True)
        self.grid_action.setChecked(self.overlay_settings.grid.enabled)
        self.grid_action.toggled.connect(self._on_grid_toggled)
        self.compass_action = QAction("Compass", self, checkable=True)
        self.compass_action.setChecked(self.overlay_settings.compass.enabled)
        self.compass_action.toggled.connect(self._on_compass_toggled)
        self.info_box_action = QAction("Info Box", self, checkable=True)
        self.info_box_action.setChecked(self.overlay_settings.info_box.enabled)
        self.info_box_action.toggled.connect(self._on_info_box_toggled)
        self.constellations_action = QAction("Constellations", self, checkable=True)
        self.constellations_action.setChecked(self.overlay_settings.constellations.enabled)
        self.constellations_action.toggled.connect(self._on_constellations_toggled)
        self.overlays_menu.addAction(self.grid_action)
        self.overlays_menu.addAction(self.compass_action)
        self.overlays_menu.addAction(self.info_box_action)
        self.overlays_menu.addAction(self.constellations_action)
        overlays_btn.setMenu(self.overlays_menu)
        toolbar.addWidget(overlays_btn)
        toolbar.addSeparator()

        # Auto Arrange Labels / Save Layout / Load Layout moved to the "Tools" dock tab
        # (see gui/tools_panel.py) -- per user request, to keep this toolbar simple.

        # Per user request: ordered last (Export), with a
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
            header = self.bridge.get_wcs_header_dict()
            # Siril's own image_info.plate_solved reflects only its PLTSOLVD FITS
            # keyword -- its own internal "I solved this" bookkeeping flag, not a WCS
            # standard. An image solved by another tool (Astrometry.net, ASTAP,
            # PixInsight, or a telescope archive's own pipeline -- confirmed via a real
            # Grantecan/GTC download that has a complete, valid TAN-SIP solution but no
            # PLTSOLVD card at all) has no reason to carry that keyword. We already build
            # our own astropy.wcs.WCS straight from the header for every actual
            # coordinate transform (ARCHITECTURE.md #4) rather than trusting Siril's
            # flag for the math, so attempt that construction regardless of
            # plate_solved and only treat it as "not solved" if the header genuinely has
            # no usable celestial WCS -- see SirilWcs.from_header_dict.
            try:
                self.wcs = SirilWcs.from_header_dict(header, self.image_info.width, self.image_info.height)
            except NotPlateSolvedError:
                QMessageBox.warning(
                    self, "Not Plate Solved",
                    "The loaded image has no astrometric solution. Plate solve it in "
                    "Siril first, then relaunch Siril Modern Annotator.",
                )
                return
            if not self.image_info.plate_solved:
                QMessageBox.information(
                    self, "Unverified Astrometric Solution",
                    "Siril did not mark this image as plate solved, but its FITS header "
                    "already contains a usable astrometric solution, so it's being used "
                    "anyway. Object positions may be less reliable than a solution "
                    "verified by Siril itself.",
                )
            self.arcsec_per_px = self.wcs.pixel_scale_arcsec_per_px()
            self.icc_profile = self.bridge.get_image_icc_profile()
            # Label font sizes only -- enabled/color/etc. keep their dataclass defaults
            # (both off). Not gated behind _has_saved_style like global_style_holder
            # below: font/line-width/etc sizing always starts resolution-scaled fresh
            # for this image, not from a previous session's flat saved number, which
            # would look wrong at a different resolution.
            self.overlay_settings = preset_store.default_overlay_settings_for_image(
                self.image_info.width, self.image_info.height
            )
            # On/off state and non-size-dependent style (color, opacity, label
            # positions, corner) DO carry over across sessions, per user request --
            # see apply_last_used_overlay_settings's own docstring for exactly which
            # fields that is and why the rest is deliberately excluded.
            self.overlay_settings = last_used_store.apply_last_used_overlay_settings(self.overlay_settings)
            self.overlay_settings.info_box.text = self._default_info_box_text()
            self._setup_overlay_items()
            self._sync_overlay_action_checks()
            self.style_panel.set_overlay_settings(self.overlay_settings)
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
            self.setWindowTitle(f"{APP_TITLE} v{__version__} — {self.source_identifier}")
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
        # Sh2CorrectedPositionProvider and RcwCorrectedPositionProvider first, ahead of
        # everything else: explicitly experimental (GitHub issue #10 + explicit user
        # request for Sh2; RCW added after a confirmed real report of RCW107 landing
        # ~2.5 arcmin off), see their own and sh2_corrected_positions.py's/
        # rcw_corrected_positions.py's docstrings for the confirmed position-error
        # rationale in each case. "First arrival wins the same-designation dedup tie"
        # (see the comment below) is exactly what makes this work -- delete either
        # provider's one line (and the two files it references) to fully revert that
        # one catalog if verification in Siril doesn't bear it out.
        #
        # GumProvider next -- also bundled data from issue #10's same source (see
        # gum_positions.py), but fully offline/permanent (not experimental like the Sh2
        # fix above), so its position in this list doesn't matter for correctness: it's
        # a different catalog from RCW/NGC/etc., and CompositeProvider._dedupe always
        # lets the objectively higher-priority catalog win a same-position match
        # regardless of arrival order (arrival order only breaks *same*-catalog ties,
        # like Sh2CorrectedPositionProvider's own case above).
        #
        # Local next, VizieR after that: when the same object comes back from both (e.g.
        # NGC/IC/Messier from VII/118 vs Siril's own bundled CSV) and CompositeProvider's
        # dedup ties on catalog priority, it keeps whichever result arrived first -- see
        # _dedupe's own comment on VII/118's RAB2000/DEB2000 being low precision (only
        # ~0.1min RA / 1' Dec). A real screenshot showed markers for NGC5471 and other
        # compact objects landing up to ~1' off their true position because VizieR's
        # coarser coordinates were arriving (and therefore winning) first. Local's finer
        # position now wins that tie; VII/118 still contributes richer object-type data
        # via _dedupe's enrichment step.
        providers: list = [
            Sh2CorrectedPositionProvider(),
            RcwCorrectedPositionProvider(),
            GumProvider(),
            LocalCsvProvider(self.bridge.get_system_catalogue_dir()),
            VizierProvider(),
        ]
        # "user_dso": Siril's own Astrometry > Annotate > Search Object list -- lives in
        # a different, writable directory (get_user_catalogue_dir(), not
        # get_system_catalogue_dir()), via a newer sirilpy accessor
        # (get_siril_userdatadir()) than the core catalogs depend on. Guarded so an
        # older sirilpy without it just silently skips this optional extra rather than
        # breaking image loading entirely -- same defensive pattern as
        # get_loaded_image_filename().
        try:
            providers.append(
                LocalCsvProvider(self.bridge.get_user_catalogue_dir(), catalog_files=USER_CATALOG_FILES)
            )
        except Exception:
            logger.debug("Could not reach Siril's user catalogue directory.", exc_info=True)
        return CompositeProvider(providers)

    def _user_dso_catalog_should_default_on(self) -> bool:
        """First-run-only default for "user_dso" (see _catalog_provider's comment).
        Per user request: on while Siril's own Annotate-tool catalogue is still a
        short, deliberately curated list (<= _USER_DSO_DEFAULT_ON_MAX_ENTRIES entries);
        off once it's grown past that, so it doesn't clutter every future image by
        default -- stays a one-click toggle either way."""
        try:
            count = count_local_catalog_entries(
                self.bridge.get_user_catalogue_dir(), USER_CATALOG_FILES["user_dso"]
            )
        except Exception:
            logger.debug("Could not read the user_dso catalogue for the first-run default.", exc_info=True)
            return False
        return count <= _USER_DSO_DEFAULT_ON_MAX_ENTRIES

    def _start_catalog_fetch(self, catalogs: set[str]) -> None:
        if self.wcs is None:
            return
        # Per user request: this table sits empty with no explanation while the
        # (already background-threaded, non-blocking) initial catalog query runs --
        # object_panel.set_annotations turns this back off once real results arrive.
        self.object_panel.set_loading(True)
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
        # Must also respect _same_dedup_class() -- not just raw proximity -- or a point
        # catalog (e.g. WR) sitting physically inside a deep-sky object it's not a
        # duplicate of (e.g. WR136 is NGC6888/the Crescent Nebula's central star, ~2-3"
        # apart) gets silently dropped when toggled on after the other. Confirmed real
        # report: toggling WR on, then NGC on, hid NGC6888 entirely; toggling either
        # catalog on first "wins" and the other's coincident object never appears.
        # CompositeProvider._dedupe() already gets this right -- this mirrors it exactly
        # rather than the plain-proximity check this used to do.
        threshold_deg = 30.0 / 3600.0
        existing = self.annotations
        new_ones = [
            r for r in results
            if not any(
                _same_dedup_class(r.catalog, e.catalog)
                and abs(r.ra - e.ra) < threshold_deg
                and abs(r.dec - e.dec) < threshold_deg
                for e in existing
            )
        ]
        if not new_ones:
            # An ONLINE_ONLY_CATALOGS entry (no local fallback -- see that constant's
            # own comment) returning zero results while VizieR is known-unreachable
            # this session is indistinguishable, from the results alone, from "there's
            # genuinely nothing in this field" -- surface which one it actually was
            # rather than silently showing the same "N objects" text either way.
            if catalog in ONLINE_ONLY_CATALOGS and not vizier_is_available():
                label = SUPPORTED_CATALOGS.get(catalog, catalog)
                self.connection_label.setText(f"{label} needs an internet connection — couldn't reach VizieR")
            else:
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
        removed_items = []
        for annotation_id in removed_ids:
            for d in (self.marker_items, self.label_items, self.connector_items):
                item = d.pop(annotation_id, None)
                if item is not None and item.scene() is not None:
                    item.setVisible(False)
                    item.scene().removeItem(item)
                    removed_items.append(item)
        self._defer_item_cleanup(removed_items)
        if self.selected_id in removed_ids:
            self.selected_id = None
            self.style_panel.set_selected_annotation(None)
        self.object_panel.set_annotations(self.annotations)
        self.connection_label.setText(f"{len(self.annotations)} objects — {self.source_identifier}")

    def _on_catalog_failed(self, message: str) -> None:
        logger.error("Catalog fetch failed: %s", message)
        # Harmless no-op if this wasn't the initial fetch (the table was already
        # showing) -- covers the case where the initial query itself fails, which
        # otherwise leaves the "Loading objects..." placeholder stuck forever.
        self.object_panel.set_loading(False)
        QMessageBox.warning(
            self, "Catalog Query Failed",
            f"Could not fetch catalog objects (check your internet connection):\n{message}",
        )

    # --------------------------------------------------------------- overlays ----

    def _default_info_box_text(self) -> str:
        """Pre-populates the Info Box overlay from FITS header metadata (camera,
        telescope, filter, exposure, etc. -- whatever get_technical_metadata() finds;
        see its own docstring for which fields are confirmed vs. best-effort), the
        same "real text, not a placeholder" convention as an object's custom display
        name -- the user edits this in place rather than starting from nothing."""
        try:
            metadata = self.bridge.get_technical_metadata()
        except Exception:
            # Purely cosmetic pre-fill -- must never block image loading over it.
            logger.debug("Could not read technical metadata for the Info Box overlay.", exc_info=True)
            return ""
        return "\n".join(f"{label}: {value}" for label, value in metadata.items())

    def _setup_overlay_items(self) -> None:
        """Creates (or, on reloading a different image, re-creates against the new
        wcs/image size) the grid/compass/info box scene items. Independent of
        _rebuild_scene -- those are one-per-Annotation, these are one-per-image.

        Real crash report (macOS crash log, SIGSEGV inside QGraphicsView::paintEvent's
        item traversal, triggered by a later, unrelated toolbar button click): this
        used to call removeItem() and immediately overwrite self.grid_item/
        compass_item/info_box_item, dropping the last Python reference right away.
        Same known class of PyQt/Qt use-after-free _defer_item_cleanup already exists
        to guard against for markers/labels/connectors (see its own docstring) --
        applies just as much here, it just hadn't been wired up for these three items
        yet. Collect what got removed and hand it to that same deferred-cleanup path
        instead of letting them be garbage-collected immediately."""
        if self.wcs is None:
            return
        removed = []
        for item in (self.grid_item, self.compass_item, self.info_box_item, self.constellation_item):
            if item is not None and item.scene() is not None:
                item.scene().removeItem(item)
                removed.append(item)
        self._defer_item_cleanup(removed)
        self.grid_item = GridItem(self.wcs, self.overlay_settings.grid)
        self.compass_item = CompassItem(self.wcs, self.overlay_settings.compass)
        self.compass_item.moved.connect(self._on_compass_moved)
        self.compass_item.context_menu_requested.connect(self._show_compass_context_menu)
        self.info_box_item = InfoBoxItem(
            self.overlay_settings.info_box, self.image_info.width, self.image_info.height,
            text_measurer=qt_text_measurer(None),
        )
        self.info_box_item.moved.connect(self._on_info_box_moved)
        self.info_box_item.context_menu_requested.connect(self._show_info_box_context_menu)
        self.info_box_item.clicked.connect(self._on_info_box_clicked)
        self.constellation_item = ConstellationLinesItem(
            self.wcs, self.overlay_settings.constellations, self._constellation_lines, self._constellation_names,
        )
        self.image_view.scene_.addItem(self.grid_item)
        self.image_view.scene_.addItem(self.compass_item)
        self.image_view.scene_.addItem(self.info_box_item)
        self.image_view.scene_.addItem(self.constellation_item)

    def _sync_overlay_action_checks(self) -> None:
        """Syncs the Overlays menu's checkable actions to self.overlay_settings'
        current on/off state. Confirmed real report: the grid/compass/info box/
        constellations QActions were only ever given a checked state once, at
        __init__ time -- from the placeholder OverlaySettings() that exists before
        the first image even loads, not the real settings _load_current_image()
        restores moments later (last_used_store.apply_last_used_overlay_settings()).
        The restored overlay itself rendered correctly either way (_setup_overlay_items
        reads self.overlay_settings directly, independent of these actions' own
        checked state), but the Overlays dropdown kept showing every entry unchecked
        after a restored session, even though the overlay was genuinely on -- so this
        needs calling anywhere overlay_settings is replaced wholesale (both here, right
        after _setup_overlay_items, and in load() for a project file), not just once at
        construction. blockSignals so this never re-fires _on_grid_toggled/etc, which
        would otherwise treat a code-driven checkbox sync as a user click and re-run
        _setup_overlay_items redundantly."""
        self.grid_action.blockSignals(True)
        self.grid_action.setChecked(self.overlay_settings.grid.enabled)
        self.grid_action.blockSignals(False)
        self.compass_action.blockSignals(True)
        self.compass_action.setChecked(self.overlay_settings.compass.enabled)
        self.compass_action.blockSignals(False)
        self.info_box_action.blockSignals(True)
        self.info_box_action.setChecked(self.overlay_settings.info_box.enabled)
        self.info_box_action.blockSignals(False)
        self.constellations_action.blockSignals(True)
        self.constellations_action.setChecked(self.overlay_settings.constellations.enabled)
        self.constellations_action.blockSignals(False)

    def _refresh_overlays(self) -> None:
        if self.grid_item is not None:
            self.grid_item.prepareGeometryChange()
            self.grid_item.update()
        if self.compass_item is not None:
            self.compass_item._sync_pos_from_model()
            self.compass_item.update()
        if self.info_box_item is not None:
            self.info_box_item._sync_pos_from_model()
            self.info_box_item.update()
        if self.constellation_item is not None:
            self.constellation_item.prepareGeometryChange()
            self.constellation_item.update()
        self.style_panel.set_overlay_settings(self.overlay_settings)

    def _on_grid_toggled(self, checked: bool) -> None:
        self.overlay_settings.grid.enabled = checked
        self._refresh_overlays()
        last_used_store.save_last_used_overlay_settings(self.overlay_settings)

    def _on_compass_toggled(self, checked: bool) -> None:
        self.overlay_settings.compass.enabled = checked
        self._refresh_overlays()
        last_used_store.save_last_used_overlay_settings(self.overlay_settings)

    def _on_info_box_toggled(self, checked: bool) -> None:
        self.overlay_settings.info_box.enabled = checked
        self._refresh_overlays()
        last_used_store.save_last_used_overlay_settings(self.overlay_settings)

    def _on_constellations_toggled(self, checked: bool) -> None:
        self.overlay_settings.constellations.enabled = checked
        self._refresh_overlays()
        last_used_store.save_last_used_overlay_settings(self.overlay_settings)

    def _on_overlay_style_edited(self) -> None:
        # No undo tracking, same as catalog color edits (_on_catalog_color_changed) --
        # a style tweak, not a spatial edit like the compass/info box drag/reset below.
        for key, value in self.style_panel.pending_grid_style_values().items():
            setattr(self.overlay_settings.grid, key, value)
        for key, value in self.style_panel.pending_compass_style_values().items():
            setattr(self.overlay_settings.compass, key, value)
        for key, value in self.style_panel.pending_info_box_style_values().items():
            setattr(self.overlay_settings.info_box, key, value)
        for key, value in self.style_panel.pending_constellation_style_values().items():
            setattr(self.overlay_settings.constellations, key, value)
        self._refresh_overlays()
        # Per user request: on/off state and non-size-dependent style (color, opacity,
        # label positions, corner) should carry over across sessions -- see
        # apply_last_used_overlay_settings's docstring for exactly which fields.
        last_used_store.save_last_used_overlay_settings(self.overlay_settings)

    def _on_compass_moved(self, new_x: float, new_y: float) -> None:
        style = self.overlay_settings.compass
        # Same no-op guard as _on_marker_moved: a plain click without real movement
        # must not silently create a "custom position" override.
        current = compute_compass_geometry(self.wcs, style)
        if current is not None and (new_x, new_y) == current.anchor:
            return
        old_anchor = (style.anchor_x, style.anchor_y)
        cmd = MoveCompassCommand(style, old_anchor, (new_x, new_y), self._refresh_overlays)
        self.undo_stack.push(cmd)

    def _reset_compass_position(self) -> None:
        style = self.overlay_settings.compass
        if style.anchor_x is None:
            return
        old_anchor = (style.anchor_x, style.anchor_y)
        cmd = MoveCompassCommand(style, old_anchor, (None, None), self._refresh_overlays)
        self.undo_stack.push(cmd)

    def _show_compass_context_menu(self, screen_pos) -> None:
        # Mirrors _show_object_context_menu's Reset Position entry: only offered once
        # the compass has actually been dragged off its default corner.
        if self.overlay_settings.compass.anchor_x is None:
            return
        menu = QMenu(self)
        reset_action = menu.addAction("Reset Position")
        reset_action.triggered.connect(self._reset_compass_position)
        menu.exec(screen_pos)

    def _on_info_box_moved(self, new_x: float, new_y: float) -> None:
        style = self.overlay_settings.info_box
        current = compute_info_box_geometry(
            style.text, style, self.image_info.width, self.image_info.height,
        )
        if current is not None and (new_x, new_y) == (current.bbox.x0, current.bbox.y0):
            return
        old_anchor = (style.anchor_x, style.anchor_y)
        cmd = MoveInfoBoxCommand(style, old_anchor, (new_x, new_y), self._refresh_overlays)
        self.undo_stack.push(cmd)

    def _reset_info_box_position(self) -> None:
        style = self.overlay_settings.info_box
        if style.anchor_x is None:
            return
        old_anchor = (style.anchor_x, style.anchor_y)
        cmd = MoveInfoBoxCommand(style, old_anchor, (None, None), self._refresh_overlays)
        self.undo_stack.push(cmd)

    def _show_info_box_context_menu(self, screen_pos) -> None:
        if self.overlay_settings.info_box.anchor_x is None:
            return
        menu = QMenu(self)
        reset_action = menu.addAction("Reset Position")
        reset_action.triggered.connect(self._reset_info_box_position)
        menu.exec(screen_pos)

    def _on_info_box_clicked(self) -> None:
        # Per user request: clicking the Info Box overlay jumps straight to its text
        # field instead of leaving the user to go find the Overlays tab themselves --
        # mirrors _on_object_double_clicked's tab-switch-then-focus pattern below.
        self.dock_tabs.setCurrentWidget(self.style_panel)
        self.style_panel.show_overlays_tab()
        self.style_panel.info_box_text_edit.setFocus()
        self.style_panel.info_box_text_edit.selectAll()

    # --------------------------------------------------------------- scene sync ----

    def _rebuild_scene(self) -> None:
        # Route every removed item through _defer_item_cleanup (see its own docstring
        # for the real native crash reports this exists to prevent) rather than letting
        # d.clear() drop the last Python reference immediately -- this used to free
        # every item synchronously with zero grace period, unlike every other removal
        # path in this file. Confirmed real crash: a SIGSEGV inside QGraphicsView::
        # paintEvent with no toolbar-click frame on the stack at all, i.e. a plain
        # queued repaint touching an item _rebuild_scene had already destroyed moments
        # earlier (e.g. loading a project, or the initial catalog query completing,
        # while a previous scene's items were still live).
        removed_items = []
        for d in (self.marker_items, self.label_items, self.connector_items):
            for item in d.values():
                if item.scene() is not None:
                    item.setVisible(False)
                    item.scene().removeItem(item)
                    removed_items.append(item)
            d.clear()
        self._defer_item_cleanup(removed_items)
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
        marker.moved.connect(lambda x, y, a=ann: self._on_marker_moved(a, x, y))
        label.moved.connect(lambda x, y, a=ann: self._on_label_moved(a, x, y))
        # Deferred (not called directly from the signal handler): context_menu_requested
        # is emitted synchronously from *inside* MarkerItem/LabelItem's own
        # contextMenuEvent, i.e. while that item's own C++ virtual-method call frame is
        # still live on the stack. _show_object_context_menu's menu.exec() nested loop
        # can itself trigger Delete, which destroys this exact item -- if that whole
        # chain runs synchronously here, the item gets destroyed while its own
        # contextMenuEvent frame is still above it on the stack, and unwinding that
        # frame afterward calls a virtual method on an already-destroyed C++ object
        # (confirmed real crash: SIGABRT / "Pure virtual function called!"). A prior
        # fix that deferred only the Delete action itself (via QTimer.singleShot from
        # inside the menu's own triggered handler) did NOT resolve this: Qt's
        # zero-delay timers are serviced by whatever event loop is current when they
        # fire, including menu.exec()'s own still-active nested one, so a delete
        # deferred that way could still run before contextMenuEvent had returned.
        # Deferring the *entire* menu call here instead guarantees contextMenuEvent has
        # already fully returned (this item's frame is off the stack) before
        # menu.exec() -- and anything it triggers -- ever begins; destroying the item
        # from inside a Qt nested loop that has nothing to do with the item's own
        # event handling is completely normal and safe.
        marker.context_menu_requested.connect(
            lambda pos, a=ann: QTimer.singleShot(0, lambda: self._show_object_context_menu(a, pos))
        )
        label.context_menu_requested.connect(
            lambda pos, a=ann: QTimer.singleShot(0, lambda: self._show_object_context_menu(a, pos))
        )
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

    def _remove_scene_items_for(self, ann: Annotation) -> None:
        """Inverse of _add_scene_items_for, for one annotation -- used by
        AddAnnotationCommand.undo() and DeleteAnnotationCommand.redo(). Mirrors the
        item-popping loop _remove_catalog_objects already does inline for a whole
        catalog at once; pulled out as its own method here since these commands only
        ever operate on a single annotation."""
        removed = []
        for d in (self.marker_items, self.label_items, self.connector_items):
            item = d.pop(ann.id, None)
            if item is not None and item.scene() is not None:
                item.setVisible(False)
                item.scene().removeItem(item)
                removed.append(item)
        self._defer_item_cleanup(removed)

    def _defer_item_cleanup(self, items: list) -> None:
        """Keeps just-removed scene items alive for the rest of the session instead of
        letting Python's refcounting destroy their underlying C++ objects the instant
        this function returns. Confirmed real, reproducible crashes otherwise (native
        crash reports: SIGABRT "Pure virtual function called!" in
        QGraphicsItemPrivate::effectiveBoundingRect, and repeated SIGSEGV/pointer-
        authentication use-after-frees in QGraphicsItem::topLevelItem -- all inside
        QGraphicsView::paintEvent's item traversal). QGraphicsScene.removeItem()
        detaches an item, but the scene's own internal spatial index isn't necessarily
        fully rebuilt the instant it returns -- a *later* repaint, even one triggered
        by something totally unrelated to the item (an unrelated toolbar click, empty-
        space right-click, or just an ordinary repaint while dragging a different
        item), can still walk that index and touch a just-removed item. If the C++
        object is already gone by then, that's a use-after-free.

        This used to hold items only for a fixed 250ms via QTimer.singleShot before
        releasing them, on the assumption that gave Qt's event loop enough iterations
        to finish any pending paint/index work first. That assumption doesn't hold
        under load: real crash reports kept recurring specifically while the user was
        also dragging a label (which floods the event queue with geometry-change/
        repaint events), where the backlog can easily exceed 250ms and a repaint still
        queued from *before* the timer fired ends up processed *after* it. There is no
        fixed delay that is provably long enough. Since these are lightweight item
        wrappers and a session only ever accumulates a bounded, small number of them
        (each catalog toggle or delete adds a handful), the only fully safe fix is to
        never let Python free them for the life of the session at all -- see
        _pending_item_cleanup's own comment."""
        if not items:
            return
        self._pending_item_cleanup.append(items)

    def _flush_pending_item_cleanup(self, batch: list) -> None:
        """Not called automatically -- see _defer_item_cleanup's docstring for why a
        timer-based auto-flush was removed. Kept only as an explicit, deliberate way to
        release one batch early, for a call site that can independently guarantee no
        repaint can still be pending against it."""
        try:
            self._pending_item_cleanup.remove(batch)
        except ValueError:
            pass  # already removed somehow -- nothing left to do

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
            marker.setVisible(ann.enabled)
            marker._sync_pos_from_model()
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
                marker._sync_pos_from_model()
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

    def _show_add_custom_object_menu(self, x: float, y: float, screen_pos) -> None:
        # Nothing to place a custom object relative to before an image (and its WCS
        # solution) is actually loaded -- silently do nothing rather than show a menu
        # whose one action would just fail.
        if self.wcs is None or self.image_info is None:
            return
        menu = QMenu(self)
        add_action = menu.addAction("Add Custom Object")
        # Acting on exec()'s return value, not add_action.triggered -- see
        # _show_object_context_menu's comment for the full reasoning (same fix).
        if menu.exec(screen_pos) is add_action:
            self._add_custom_object(x, y)

    def _add_custom_object(self, x: float, y: float) -> None:
        # ra/dec (not just image_x/image_y) are needed even for a manually-placed
        # object -- the RA/Dec grid, the info box, and the Objects panel's RA/Dec
        # display all read straight off the annotation, same as any catalog object
        # (ARCHITECTURE.md #4's "ra/dec are the permanent source of truth").
        ra, dec = self.wcs.pixel_to_world(x, y)
        # A custom object has no catalog angular_size, so it never benefits from
        # compute_marker_geometry's angular-size-based upscaling (renderer.py) the way
        # a real galaxy/nebula often does -- rendered at the flat global radius alone,
        # it read as noticeably tinier than its catalog neighbors. Per user report,
        # give it its own per-object override at 1.6x the current global radius so it
        # stands out as a deliberately-placed point regardless of image resolution or
        # whatever preset is active (both already baked into the global radius it's
        # scaling from).
        base_marker = self.global_style_holder[0].marker_style
        custom_marker_style = replace(
            base_marker,
            radius=base_marker.radius * 1.6,
            radius_x=base_marker.radius_x * 1.6,
            radius_y=base_marker.radius_y * 1.6,
        )
        ann = Annotation(
            catalog="user",
            catalog_name="Custom Object",
            ra=ra,
            dec=dec,
            image_x=x,
            image_y=y,
            object_type="custom",
            priority=default_priority_for_catalog("user"),
            marker_style=custom_marker_style,
        )
        cmd = AddAnnotationCommand(
            ann, self.annotations, self._add_scene_items_for, self._remove_scene_items_for,
            self._refresh_after_annotation_count_change,
        )
        self.undo_stack.push(cmd)
        # Land the user directly on the rename field, text pre-selected, so typing a
        # real name is the very next thing that happens -- reuses the exact "Custom
        # display name" field/flow every other object already renames through (see
        # StylePanel.set_selected_annotation), rather than a bespoke "name this
        # object" dialog. Mirrors _on_object_double_clicked's tab-switching.
        self.select_annotation(ann.id)
        self.dock_tabs.setCurrentWidget(self.style_panel)
        self.style_panel.show_object_tab()
        self.style_panel.custom_name_edit.setFocus()
        self.style_panel.custom_name_edit.selectAll()

    def _delete_annotation(self, annotation_id: str) -> None:
        # Offered only for catalog == "user" objects -- see DeleteAnnotationCommand's
        # own docstring for why catalog objects get Hide instead.
        ann = self._find_annotation(annotation_id)
        if ann is None:
            return
        cmd = DeleteAnnotationCommand(
            ann, self.annotations, self._add_scene_items_for, self._remove_scene_items_for,
            self._refresh_after_annotation_count_change,
        )
        self.undo_stack.push(cmd)

    def _refresh_after_annotation_count_change(self) -> None:
        """Refresh callback for AddAnnotationCommand/DeleteAnnotationCommand -- unlike
        every other command's refresh (_refresh_all / _refresh_annotation), the set of
        rows itself changed, not just a field on an existing one, so the Objects panel
        needs a real set_annotations() (model reset -> new/removed row) rather than
        refresh() (repaints existing rows in place, brief/blind to added or removed
        ones). Must also work correctly when invoked directly by the undo stack
        (Ctrl+Z/Ctrl+Shift+Z), not just via _add_custom_object/_delete_annotation --
        hence clearing selected_id here rather than relying on the caller to notice a
        delete removed the selected object.

        set_annotations() MUST run first, before anything else below touches the
        table/model (clear_selection(), _refresh_all()'s object_panel.refresh() call).
        object_panel.model._annotations is the *same list object* as self.annotations
        (aliased, not copied -- see ObjectPanel.set_annotations), so
        DeleteAnnotationCommand's plain self.annotations.remove(...) already shrank it
        by the time this runs, with the QTableView never told the row count changed
        (only set_annotations()'s beginResetModel()/endResetModel() does that). Touch
        the view in any other way first and it still believes the old, larger row
        count, and can ask AnnotationTableModel.data() for a now out-of-range row --
        an unguarded self._annotations[row] there raises IndexError from inside an
        overridden Qt virtual method. Confirmed real crash (SIGABRT / "Pure virtual
        function called!") deleting a custom object."""
        self.object_panel.set_annotations(self.annotations)
        if self.selected_id is not None and self._find_annotation(self.selected_id) is None:
            self.selected_id = None
            self.style_panel.set_selected_annotation(None)
            self.object_panel.clear_selection()
            self.selection_label.setText("")
        self._refresh_all()
        self.connection_label.setText(f"{len(self.annotations)} objects — {self.source_identifier}")

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

    def _refresh_marker_position(self, ann: Annotation) -> None:
        """Refresh callback for MoveMarkerCommand. On top of the usual item/connector
        refresh, this re-syncs the Reset Position button -- which matters not just
        right after pushing the command but on every later undo()/redo() too, since
        those are invoked directly by the undo stack (Ctrl+Z etc.) and never go
        through _on_marker_moved/_reset_selected_marker_position at all. Deliberately
        does NOT call style_panel.set_selected_annotation(ann) wholesale to get this:
        that also repopulates custom_name_edit, and doing that on every undo/redo
        would reset the text cursor mid-edit (AnnotationFieldsCommand's refresh is
        exactly that same _refresh_annotation, fired on every keystroke)."""
        self._refresh_annotation(ann)
        if ann.id == self.selected_id:
            self.style_panel.reset_position_btn.setVisible(ann.marker_x is not None)

    def _on_marker_moved(self, ann: Annotation, new_x: float, new_y: float) -> None:
        # A plain click (press+release with no real drag) still fires this -- Qt's
        # ItemIsMovable only actually changes pos() if the mouse moved, but when it
        # doesn't, new_x/new_y still equal the marker's current *resolved* position
        # (image_x/image_y for a not-yet-overridden marker). Pushing that unchanged
        # value as a real override would silently "freeze" the marker (and pop up
        # Reset Position) just from selecting it -- skip the no-op instead.
        if (new_x, new_y) == ann.effective_marker_position():
            return
        old_pos = (ann.marker_x, ann.marker_y)
        cmd = MoveMarkerCommand(ann, old_pos, (new_x, new_y), lambda a=ann: self._refresh_marker_position(a))
        self.undo_stack.push(cmd)

    def _reset_selected_marker_position(self) -> None:
        if self.selected_id is None:
            return
        ann = self._find_annotation(self.selected_id)
        if ann is None or ann.marker_x is None:
            return
        old_pos = (ann.marker_x, ann.marker_y)
        cmd = MoveMarkerCommand(ann, old_pos, (None, None), lambda a=ann: self._refresh_marker_position(a))
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
        # Per user report: this only reset label positions (via run_auto_arrange
        # below), not markers -- an object dragged off its catalog/WCS position
        # stayed exactly where it was dragged to. Mirrors _reset_selected_marker_
        # position's per-object behavior (marker_x/marker_y = None means "use the
        # catalog/WCS-derived image_x/image_y", per Annotation.effective_marker_
        # position), just applied to every object instead of just the selected one.
        # One combined undo step for the whole button, not two separate ones --
        # run_auto_arrange() pushes its own command, which becomes a child of this
        # macro since it's still open when that runs.
        #
        # Custom objects (catalog == "user") are skipped here -- per user request.
        # They have no independent catalog/WCS position to reset *to*: wherever the
        # user placed one *is* its position, same as image_x/image_y itself is just
        # wherever they right-clicked when creating it (see _add_custom_object), not
        # a verified astrometric source position the way a real catalog object's is.
        if not self.annotations:
            return
        self.undo_stack.beginMacro("Reset Layout")
        for ann in self.annotations:
            if ann.catalog != "user" and ann.marker_x is not None:
                old_pos = (ann.marker_x, ann.marker_y)
                self.undo_stack.push(
                    MoveMarkerCommand(ann, old_pos, (None, None), lambda a=ann: self._refresh_marker_position(a))
                )
        self.run_auto_arrange()
        self.undo_stack.endMacro()

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
        # Only offered once the marker has actually been dragged off its WCS position --
        # same condition as the Selected Object tab's Reset Position button (brief:
        # give it a right-click entry there too, not just the Style panel button).
        reset_position_action = menu.addAction("Reset Position") if ann.marker_x is not None else None
        # Delete is only offered for a manually-placed custom object (catalog ==
        # "user") -- a catalog object has no equivalent "undo its existence" action,
        # Hide above is the correct (and only) way to remove it from view. See
        # DeleteAnnotationCommand's docstring.
        delete_action = menu.addAction(f"Delete {name}") if ann.catalog == "user" else None
        # Per user request: same "Open in SIMBAD" action as the Objects panel's own
        # right-click menu (object_panel.py), reusing its exact URL-building logic
        # rather than a second, drifting copy. Same catalog == "user" exclusion --  a
        # custom object has no real catalog identifier for SIMBAD to resolve.
        simbad_action = menu.addAction("Open in SIMBAD") if ann.catalog != "user" else None
        # Acting on menu.exec()'s *return value* here, after it returns, rather than
        # on each action's triggered signal (which fires *during* exec()'s own still-
        # active nested event loop) -- real crash report (a full macOS crash log, not
        # just the Siril console line) showed the abort happening inside a totally
        # unrelated *later* QGraphicsView repaint (SIGABRT in
        # QGraphicsItemPrivate::effectiveBoundingRect, reached via an async posted
        # paint event, no Python frames anywhere on that stack), meaning the scene's
        # own internal item index still held a stale reference to the destroyed marker
        # by the time it repainted -- a known class of Qt/PyQt issue when an item is
        # destroyed while still nested inside a QMenu's own exec() loop. Deferring the
        # *entire menu call* (see _add_scene_items_for's context_menu_requested wiring)
        # fixed one layer of reentrancy (contextMenuEvent's own frame) but not this
        # one, since exec() below still opens its own nested loop regardless. Waiting
        # for exec() to fully return before acting matches exactly how
        # _remove_catalog_objects (a plain toolbar checkbox slot, never nested, never
        # crashed) already destroys items safely.
        chosen = menu.exec(screen_pos)
        if chosen is None:
            return
        if chosen is hide_action:
            self._on_table_visibility_changed(ann.id, False)
        elif chosen is reset_position_action:
            self._reset_selected_marker_position()
        elif chosen is delete_action:
            self._delete_annotation(ann.id)
        elif chosen is simbad_action:
            QDesktopServices.openUrl(QUrl(simbad_url_for(ann.catalog, ann.catalog_name, ann.simbad_id)))

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
        # Clear manually_positioned first -- otherwise keep_manual=True below treats
        # every label the user has ever dragged as a fixed obstacle and refuses to
        # touch it, which is the opposite of what clicking a button literally named
        # "Auto Arrange Labels" is for. Confirmed by a real report: a layout made
        # entirely of manually-dragged, now-overlapping labels was completely
        # unaffected by this button. Locked objects are untouched by this (a separate
        # flag) and stay correctly excluded either way. Mirrors _reset_layout's own
        # (now effectively redundant, but harmless) flag-clearing loop.
        for ann in self.annotations:
            ann.manually_positioned = False
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

        # Grid/compass line width and label size are resolution-scaled defaults (see
        # default_overlay_settings_for_image), same reasoning as marker radius/font size
        # above -- "Reset to Default" needs to restore those too, not just marker/label
        # style, or a grid/compass line manually thinned out (or left at an old flat
        # default from before that scaling existed) would survive a reset. `enabled` and
        # the compass's drag position are placement/visibility, not style, so both are
        # preserved rather than reset.
        fresh_overlays = preset_store.default_overlay_settings_for_image(
            self.image_info.width, self.image_info.height,
        )
        self._apply_overlay_style_fields(self.overlay_settings.grid, fresh_overlays.grid, preserve={"enabled"})
        self._apply_overlay_style_fields(
            self.overlay_settings.compass, fresh_overlays.compass, preserve={"enabled", "anchor_x", "anchor_y"},
        )
        self._refresh_overlays()
        last_used_store.save_last_used_overlay_settings(self.overlay_settings)

    @staticmethod
    def _apply_overlay_style_fields(target: object, fresh: object, *, preserve: set[str]) -> None:
        for f in dataclass_fields(target):
            if f.name not in preserve:
                setattr(target, f.name, getattr(fresh, f.name))

    def _on_object_style_edited(self, annotation_id: str) -> None:
        ann = self._find_annotation(annotation_id)
        if ann is None:
            return
        old_shape = ann.marker_style.shape if ann.marker_style is not None else None
        new_values = self.style_panel.pending_object_style_values()
        new_marker_style = new_values.get("marker_style")
        if (
            new_marker_style is not None
            and new_marker_style.shape is MarkerShape.ELLIPSE
            and old_shape is not MarkerShape.ELLIPSE
        ):
            # Switching a per-object marker to Ellipse for the first time: the flat
            # dataclass default (20x12px) is often much smaller than the object's
            # actual rendered circle -- especially with "Scale with angular size" on,
            # e.g. a large galaxy rendering at several hundred px -- confirmed real
            # report that the ellipse "looked very small" right after switching. Size
            # it to at least the circle's current on-screen radius instead, so
            # switching shape doesn't visually shrink the marker. Only applies when
            # radius_x/y are still untouched (the fresh MarkerStyle defaults) -- a
            # deliberately re-tuned ellipse must survive flipping back to Circle and
            # forward again, not get silently reset every time.
            fresh_defaults = MarkerStyle()
            # Approximate, not exact equality: the Radius X/Y sliders' quadratic curve
            # (LabeledSlider) quantizes to a fixed internal step count, so a value that
            # was never touched round-trips close to the dataclass default but not
            # bit-exact (e.g. 19.9928 instead of 20.0).
            _RADIUS_DEFAULT_TOLERANCE = 1.0
            if (
                abs(new_marker_style.radius_x - fresh_defaults.radius_x) < _RADIUS_DEFAULT_TOLERANCE
                and abs(new_marker_style.radius_y - fresh_defaults.radius_y) < _RADIUS_DEFAULT_TOLERANCE
            ):
                current_geo = compute_marker_geometry(
                    ann, self.global_style_holder[0], self.arcsec_per_px,
                    self.max_marker_radius_px, self.catalog_colors,
                )
                target_radius = max(current_geo.radius, fresh_defaults.radius_x)
                new_marker_style = replace(new_marker_style, radius_x=target_radius, radius_y=target_radius)
                new_values["marker_style"] = new_marker_style
                editor = self.style_panel.object_editor
                editor.marker_radius_x.blockSignals(True)
                editor.marker_radius_y.blockSignals(True)
                editor.marker_radius_x.setValue(target_radius)
                editor.marker_radius_y.setValue(target_radius)
                editor.marker_radius_x.blockSignals(False)
                editor.marker_radius_y.blockSignals(False)
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
        # Per user request: remember export settings (format, resolution mode, quality,
        # DPI) across sessions -- these don't depend on any one image the way overlay
        # sizing does, so unlike default_overlay_settings_for_image there's no
        # resolution-based recompute to defeat by restoring them verbatim.
        initial = last_used_store.load_last_used_export_settings()
        dialog = ExportDialog(self.image_info.width, self.image_info.height, self, initial=initial)
        if dialog.exec():
            settings = dialog.export_settings()
            last_used_store.save_last_used_export_settings(settings)
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
            wcs=self.wcs, overlay_settings=self.overlay_settings,
            constellation_lines=self._constellation_lines, constellation_names=self._constellation_names,
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
            overlay_settings=self.overlay_settings,
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
        self.overlay_settings = project.overlay_settings
        self._sync_overlay_action_checks()
        if self.wcs is not None:
            self._setup_overlay_items()
        self.undo_stack.clear()
        self._rebuild_scene()
        self.object_panel.set_annotations(self.annotations)
        self.style_panel.set_global_style(self.global_style_holder[0])
        self.style_panel.set_overlay_settings(self.overlay_settings)
