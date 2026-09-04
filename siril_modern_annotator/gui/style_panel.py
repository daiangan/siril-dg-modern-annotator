"""Label/marker/connector styling controls: global default style (brief #15-17) plus
per-object overrides for the currently selected annotation (brief #18).
"""

from __future__ import annotations

from dataclasses import replace

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QColor, QFont
from PyQt6.QtWidgets import (
    QCheckBox,
    QColorDialog,
    QComboBox,
    QFontComboBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from ..annotation.catalogs import DEFAULT_CATALOG_COLORS, SUPPORTED_CATALOGS
from .widgets import DarkDoubleSpinBox, DarkSpinBox, LabeledSlider
from ..annotation.models import (
    Annotation,
    BackgroundMode,
    ConnectorStyle,
    DecLabelPosition,
    InfoBoxCorner,
    LabelStyle,
    MarkerShape,
    MarkerStyle,
    NameDisplayMode,
    RaLabelPosition,
    StylePreset,
)
from ..annotation.renderer import resolve_connector_color, resolve_marker_color
from ..persistence import presets as preset_store


def _scrollable(widget: QWidget) -> QScrollArea:
    area = QScrollArea()
    area.setWidgetResizable(True)
    area.setFrameShape(QScrollArea.Shape.NoFrame)
    area.setWidget(widget)
    return area


class ColorButton(QPushButton):
    color_changed = pyqtSignal(str)

    def __init__(self, initial_hex: str = "#ffffff", parent=None):
        super().__init__(parent)
        self.setFixedWidth(48)
        self._hex = initial_hex
        self.set_color(initial_hex)
        self.clicked.connect(self._pick)

    @property
    def hex_color(self) -> str:
        return self._hex

    def set_color(self, hex_color: str) -> None:
        self._hex = hex_color
        self.setStyleSheet(f"background-color: {hex_color}; border: 1px solid #444;")

    def _pick(self) -> None:
        # DontUseNativeDialog: on Windows/macOS, QColorDialog.getColor() otherwise uses
        # the OS's own native color picker, which is a real system dialog outside Qt's
        # control -- it ignores the app-wide dark stylesheet (modern_annotator.py's
        # load_dark_stylesheet()) entirely, rendering light/native regardless of the
        # rest of the app. Confirmed by a real screenshot: the picker looked jarringly
        # different from the annotator's own dark theme. Qt's own bundled dialog is a
        # normal QWidget tree (QLineEdit/QSpinBox/QPushButton etc.), so it already
        # picks up that same stylesheet for free once native is disabled.
        color = QColorDialog.getColor(
            QColor(self._hex), self, "Choose color",
            QColorDialog.ColorDialogOption.DontUseNativeDialog,
        )
        if color.isValid():
            self.set_color(color.name())
            self.color_changed.emit(color.name())


def _normalize_rotation_deg(rotation_deg: float) -> float:
    """Maps any angle onto [-90, 90) -- an ellipse rotated by theta is visually
    identical to one rotated by theta+180 (its two ends are indistinguishable), so
    this is lossless. Used when loading a value into marker_rotation's -90..90 slider
    (see StyleEditorWidget.__init__) so a value from outside that range (a saved
    project file, or any future source) still displays correctly instead of visually
    clamping to the nearest edge and silently disagreeing with the stored value."""
    return ((rotation_deg + 90.0) % 180.0) - 90.0


class StyleEditorWidget(QWidget):
    """Edits a (MarkerStyle, LabelStyle, connector settings) triple in place and emits
    `changed` after every edit. Used both for the global preset and for a per-object
    override — the caller decides what object backs get_marker/get_label/etc."""

    changed = pyqtSignal()

    def __init__(self, parent=None, allow_ellipse: bool = False):
        super().__init__(parent)
        layout = QVBoxLayout(self)

        marker_group = QGroupBox("Marker")
        marker_form = QFormLayout(marker_group)
        self.marker_shape = QComboBox()
        # Per user request: only these shapes are offered (Circle stays the default).
        # The model/renderer still support every MarkerShape value -- this only
        # narrows what's newly selectable, so a project file saved before this
        # restriction with a different shape still renders exactly as saved. Ellipse
        # is per-object only (brief: fit a specific irregular galaxy in a custom oval,
        # not a sensible *default* shape for point-like objects) -- allow_ellipse is
        # only True for the Selected Object tab's editor; see StylePanel.__init__.
        shapes = (MarkerShape.CIRCLE, MarkerShape.BRACKETS)
        if allow_ellipse:
            shapes = shapes + (MarkerShape.ELLIPSE,)
        for shape in shapes:
            self.marker_shape.addItem(shape.value.capitalize(), shape)
        self.marker_color = ColorButton()
        self.marker_stroke = DarkDoubleSpinBox()
        self.marker_stroke.setRange(0.2, 10.0)
        self.marker_stroke.setSingleStep(0.2)
        # Slider, not a spinbox: per user request, matching Radius X/Y below (Circle/
        # Brackets' range is far narrower than Ellipse's 2-5000, so no quadratic curve
        # is needed here -- linear precision across 2-500 is already fine).
        self.marker_radius = LabeledSlider(2.0, 500.0, suffix="px")
        # Sliders, not spinboxes: per user report, incrementing these via a spinbox's
        # tiny arrows was uncomfortable, and dragging is the natural interaction for
        # "adjust this oval until it visually fits the galaxy" anyway. 5000px comfortably
        # covers a large galaxy's radius in native image pixel space on a real
        # high-resolution astrophotography frame -- the previous 500 cap (copied from
        # the circular Radius field above) left a real M31 ellipse unable to reach the
        # galaxy's actual extent, per a real report with a screenshot.
        # quadratic: concentrates drag precision at the low end (2-500ish px, where
        # most objects actually fall) while still reaching 5000px within one drag --
        # per a real report that a plain linear mapping across this wide a range was
        # too sensitive for the small adjustments needed to actually fit an oval.
        self.marker_radius_x = LabeledSlider(2.0, 5000.0, suffix="px", curve="quadratic")
        self.marker_radius_y = LabeledSlider(2.0, 5000.0, suffix="px", curve="quadratic")
        # -90..90, not -180..180: a rotated ellipse is visually identical every 180
        # degrees (its two ends are indistinguishable), so halving the range halves
        # degrees-per-pixel sensitivity for free, with no loss of reachable
        # orientations -- see _normalize_rotation_deg, used in load() below.
        self.marker_rotation = LabeledSlider(-90.0, 90.0, suffix="°")
        self.marker_opacity = DarkDoubleSpinBox()
        self.marker_opacity.setRange(0.05, 1.0)
        self.marker_opacity.setSingleStep(0.05)
        self.marker_size_from_angular = QCheckBox("Scale with angular size")
        marker_form.addRow("Shape", self.marker_shape)
        marker_form.addRow("Color", self.marker_color)
        marker_form.addRow("Stroke width", self.marker_stroke)
        marker_form.addRow("Radius", self.marker_radius)
        marker_form.addRow("Radius X", self.marker_radius_x)
        marker_form.addRow("Radius Y", self.marker_radius_y)
        marker_form.addRow("Rotation", self.marker_rotation)
        marker_form.addRow("Opacity", self.marker_opacity)
        marker_form.addRow(self.marker_size_from_angular)
        self._marker_form = marker_form

        label_group = QGroupBox("Label")
        label_form = QFormLayout(label_group)
        self.font_family = QFontComboBox()
        self.font_size = DarkDoubleSpinBox()
        self.font_size.setRange(6.0, 96.0)
        self.font_bold = QCheckBox("Bold")
        self.font_italic = QCheckBox("Italic")
        self.text_color = ColorButton()
        self.name_display = QComboBox()
        for mode in NameDisplayMode:
            self.name_display.addItem(mode.value.replace("_", " ").title(), mode)
        label_form.addRow("Font", self.font_family)
        label_form.addRow("Size", self.font_size)
        style_row = QHBoxLayout()
        style_row.addWidget(self.font_bold)
        style_row.addWidget(self.font_italic)
        label_form.addRow(style_row)
        label_form.addRow("Text color", self.text_color)
        label_form.addRow("Name display", self.name_display)

        bg_group = QGroupBox("Background")
        bg_form = QFormLayout(bg_group)
        self.background_mode = QComboBox()
        for mode in BackgroundMode:
            self.background_mode.addItem(mode.value.capitalize(), mode)
        self.background_color = ColorButton("#101015")
        # Default per user request: label backgrounds match their catalog's color
        # unless a custom color is explicitly chosen (mirrors marker/connector
        # precedence -- see renderer.compute_label_geometry). Unchecking reveals the
        # color button below it for a manual override.
        self.background_color_inherit_check = QCheckBox("Match catalog color")
        self.background_color_inherit_check.setChecked(True)
        self.background_color.setEnabled(False)
        self.background_color_inherit_check.toggled.connect(
            lambda checked: self.background_color.setEnabled(not checked)
        )
        self.background_opacity = DarkDoubleSpinBox()
        self.background_opacity.setRange(0.0, 1.0)
        self.background_opacity.setSingleStep(0.05)
        self.padding = DarkDoubleSpinBox()
        self.padding.setRange(0.0, 40.0)
        self.corner_radius = DarkDoubleSpinBox()
        self.corner_radius.setRange(0.0, 40.0)
        bg_form.addRow("Mode", self.background_mode)
        bg_form.addRow(self.background_color_inherit_check)
        bg_form.addRow("Color", self.background_color)
        bg_form.addRow("Opacity", self.background_opacity)
        bg_form.addRow("Padding", self.padding)
        bg_form.addRow("Corner radius", self.corner_radius)

        effects_group = QGroupBox("Text effects")
        effects_form = QFormLayout(effects_group)
        self.outline = QCheckBox("Outline")
        self.outline_color = ColorButton("#000000")
        self.shadow = QCheckBox("Drop shadow")
        self.glow = QCheckBox("Glow")
        effects_form.addRow(self.outline, self.outline_color)
        effects_form.addRow(self.shadow)
        effects_form.addRow(self.glow)

        connector_group = QGroupBox("Connector")
        connector_form = QFormLayout(connector_group)
        self.connector_enabled = QCheckBox("Show connector line")
        self.connector_style = QComboBox()
        for style in ConnectorStyle:
            self.connector_style.addItem(style.value.capitalize(), style)
        self.connector_color = ColorButton("#8a8a8a")
        self.connector_width = DarkDoubleSpinBox()
        self.connector_width.setRange(0.2, 6.0)
        self.connector_width.setSingleStep(0.2)
        connector_form.addRow(self.connector_enabled)
        connector_form.addRow("Style", self.connector_style)
        connector_form.addRow("Color", self.connector_color)
        connector_form.addRow("Width", self.connector_width)

        for group in (marker_group, label_group, bg_group, effects_group, connector_group):
            layout.addWidget(group)
        layout.addStretch(1)

        self._connect_signals()
        self._update_marker_shape_fields_visibility()

    def _update_marker_shape_fields_visibility(self) -> None:
        """Radius X/Y/Rotation only make sense for ELLIPSE; plain Radius and "Scale
        with angular size" only make sense for every other shape (see MarkerStyle's
        docstring on radius_x -- ellipse is deliberately manual-only). Hides rather
        than disables, same reasoning as the Selected Object tab's Reset Position
        button: an irrelevant control shouldn't sit there greyed out."""
        is_ellipse = self.marker_shape.currentData() is MarkerShape.ELLIPSE
        for row_widget in (self.marker_radius_x, self.marker_radius_y, self.marker_rotation):
            self._marker_form.setRowVisible(row_widget, is_ellipse)
        self._marker_form.setRowVisible(self.marker_radius, not is_ellipse)
        self._marker_form.setRowVisible(self.marker_size_from_angular, not is_ellipse)

    def _connect_signals(self) -> None:
        widgets = [
            self.marker_shape, self.marker_stroke, self.marker_radius,
            self.marker_radius_x, self.marker_radius_y, self.marker_rotation, self.marker_opacity,
            self.marker_size_from_angular, self.font_family, self.font_size, self.font_bold,
            self.font_italic, self.name_display, self.background_mode,
            self.background_color_inherit_check, self.background_opacity,
            self.padding, self.corner_radius, self.outline, self.shadow, self.glow,
            self.connector_enabled, self.connector_style, self.connector_width,
        ]
        self.marker_shape.currentIndexChanged.connect(self._update_marker_shape_fields_visibility)
        for w in widgets:
            signal = getattr(w, "currentIndexChanged", None) or getattr(w, "valueChanged", None) or getattr(w, "toggled", None)
            if signal:
                signal.connect(self.changed)
        for btn in (self.marker_color, self.text_color, self.background_color, self.outline_color, self.connector_color):
            btn.color_changed.connect(lambda _c: self.changed.emit())

    def load(self, marker: MarkerStyle, label: LabelStyle, connector_style: ConnectorStyle, connector_color: str, connector_width: float, connector_enabled_default: bool = True) -> None:
        block = self.blockSignals(True)
        self.marker_shape.setCurrentIndex(self.marker_shape.findData(marker.shape))
        self.marker_color.set_color(marker.color)
        self.marker_stroke.setValue(marker.stroke_width)
        self.marker_radius.setValue(marker.radius)
        self.marker_radius_x.setValue(marker.radius_x)
        self.marker_radius_y.setValue(marker.radius_y)
        self.marker_rotation.setValue(_normalize_rotation_deg(marker.rotation_deg))
        self.marker_opacity.setValue(marker.opacity)
        self.marker_size_from_angular.setChecked(marker.size_from_angular_size)
        self._update_marker_shape_fields_visibility()

        self.font_family.setCurrentFont(QFont(label.font_family))
        self.font_size.setValue(label.font_size)
        self.font_bold.setChecked(label.bold)
        self.font_italic.setChecked(label.italic)
        self.text_color.set_color(label.text_color)
        self.name_display.setCurrentIndex(self.name_display.findData(label.name_display))

        self.background_mode.setCurrentIndex(self.background_mode.findData(label.background_mode))
        inherit = label.background_color is None
        self.background_color_inherit_check.setChecked(inherit)
        self.background_color.setEnabled(not inherit)
        if not inherit:
            self.background_color.set_color(label.background_color)
        self.background_opacity.setValue(label.background_opacity)
        self.padding.setValue(label.padding)
        self.corner_radius.setValue(label.corner_radius)

        self.outline.setChecked(label.outline)
        self.outline_color.set_color(label.outline_color)
        self.shadow.setChecked(label.shadow)
        self.glow.setChecked(label.glow)

        self.connector_enabled.setChecked(connector_enabled_default)
        self.connector_style.setCurrentIndex(self.connector_style.findData(connector_style))
        self.connector_color.set_color(connector_color)
        self.connector_width.setValue(connector_width)
        self.blockSignals(block)

    def marker_style(self) -> MarkerStyle:
        return MarkerStyle(
            shape=self.marker_shape.currentData(),
            color=self.marker_color.hex_color,
            stroke_width=self.marker_stroke.value(),
            radius=self.marker_radius.value(),
            radius_x=self.marker_radius_x.value(),
            radius_y=self.marker_radius_y.value(),
            rotation_deg=self.marker_rotation.value(),
            opacity=self.marker_opacity.value(),
            size_from_angular_size=self.marker_size_from_angular.isChecked(),
        )

    def label_style(self) -> LabelStyle:
        return LabelStyle(
            font_family=self.font_family.currentFont().family(),
            font_size=self.font_size.value(),
            bold=self.font_bold.isChecked(),
            italic=self.font_italic.isChecked(),
            text_color=self.text_color.hex_color,
            background_mode=self.background_mode.currentData(),
            background_color=(
                None if self.background_color_inherit_check.isChecked() else self.background_color.hex_color
            ),
            background_opacity=self.background_opacity.value(),
            padding=self.padding.value(),
            corner_radius=self.corner_radius.value(),
            outline=self.outline.isChecked(),
            outline_color=self.outline_color.hex_color,
            shadow=self.shadow.isChecked(),
            glow=self.glow.isChecked(),
            name_display=self.name_display.currentData(),
        )

    def connector_style_value(self) -> ConnectorStyle:
        return self.connector_style.currentData()

    def connector_color_value(self) -> str:
        return self.connector_color.hex_color

    def connector_width_value(self) -> float:
        return self.connector_width.value()

    def connector_enabled_value(self) -> bool:
        return self.connector_enabled.isChecked()


class StylePanel(QWidget):
    global_style_changed = pyqtSignal()
    object_style_changed = pyqtSignal(str)  # annotation id
    object_meta_changed = pyqtSignal(str)  # annotation id
    reset_style_requested = pyqtSignal()
    reset_marker_position_requested = pyqtSignal()
    catalog_color_changed = pyqtSignal(str, str)  # catalog key, new hex color
    overlay_settings_changed = pyqtSignal()
    reset_compass_position_requested = pyqtSignal()
    reset_info_box_position_requested = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._global_style: StylePreset | None = None
        self._selected: Annotation | None = None
        self._catalog_colors: dict[str, str] = {}

        layout = QVBoxLayout(self)
        preset_row = QHBoxLayout()
        self.preset_combo = QComboBox()
        self.save_preset_btn = QPushButton("Save As…")
        self.delete_preset_btn = QPushButton("Delete")
        # Selecting a preset from the combo applies its *raw* stored values -- for a
        # high-resolution image that's a step backwards from the resolution-scaled
        # default this app normally starts with, since presets aren't resolution-aware.
        # Confirmed by a real report: after switching to a smaller-marker preset, the
        # user had no way back to "the good default", since re-selecting Minimal Modern
        # from this same combo just reapplies its flat, un-scaled values. This button
        # recomputes the actual per-image default (main_window's default_preset_for_image)
        # instead of merely re-selecting a preset.
        self.reset_style_btn = QPushButton("Reset to Default")
        preset_row.addWidget(QLabel("Preset"))
        preset_row.addWidget(self.preset_combo, 1)
        preset_row.addWidget(self.save_preset_btn)
        preset_row.addWidget(self.delete_preset_btn)
        preset_row.addWidget(self.reset_style_btn)
        layout.addLayout(preset_row)

        self.tabs = QTabWidget()
        self.global_editor = StyleEditorWidget()
        self.object_editor = StyleEditorWidget(allow_ellipse=True)
        self.object_tab = QWidget()
        object_tab_layout = QVBoxLayout(self.object_tab)
        self.object_placeholder = QLabel("No object selected.")
        self.object_placeholder.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.custom_name_label = QLabel("Custom display name / notes")
        # Multi-line so the user can write a longer description if they want (renders
        # as a multi-line label on the canvas/export, not just a single-line name) --
        # a QLineEdit was too cramped for that. Pre-filled with the object's current
        # effective name as real, editable text (see set_selected_annotation) so typing
        # extends/edits that name in place instead of starting from nothing.
        self.custom_name_edit = QPlainTextEdit()
        self.custom_name_edit.setFixedHeight(70)
        self.custom_name_edit.setPlaceholderText("Leave blank to use the catalog/common name")
        self.priority_spin = DarkSpinBox()
        self.priority_spin.setRange(0, 100)
        # Counter-intuitive direction (lower number = more important, like process
        # "niceness"), and only affects one thing (Auto Arrange's placement order) --
        # per user discussion, worth spelling out on hover rather than hiding the
        # control entirely, since it's the only way to keep a specific object's label
        # clean in a crowded field without hand-placing every label.
        priority_tooltip = (
            "Order Auto Arrange places labels in -- lower numbers go first and get the "
            "best free spot; higher numbers may get pushed aside if space is tight.\n"
            "Defaults to a value based on the object's catalog (Messier lower/more "
            "important than a faint LDN nebula, for example). Lower it to keep this "
            "object's label clean in a crowded field."
        )
        self.priority_spin.setToolTip(priority_tooltip)
        self.locked_check = QCheckBox("Locked (excluded from Auto Arrange)")
        self.use_global_check = QCheckBox("Use global style for this object")
        object_tab_layout.addWidget(self.object_placeholder)
        object_tab_layout.addWidget(self.custom_name_label)
        object_tab_layout.addWidget(self.custom_name_edit)
        priority_row = QHBoxLayout()
        self.priority_label = QLabel("Priority")
        self.priority_label.setToolTip(priority_tooltip)
        priority_row.addWidget(self.priority_label)
        priority_row.addWidget(self.priority_spin)
        object_tab_layout.addLayout(priority_row)
        # Only shown once the marker has actually been dragged off its WCS-derived
        # position (Annotation.marker_x/marker_y set) -- see set_selected_annotation.
        # Hidden rather than merely disabled: brief asked for the button to "appear"
        # when there's a custom position, not to sit there greyed out otherwise.
        self.reset_position_btn = QPushButton("Reset Position")
        object_tab_layout.addWidget(self.reset_position_btn)
        object_tab_layout.addWidget(self.locked_check)
        object_tab_layout.addWidget(self.use_global_check)
        object_tab_layout.addWidget(self.object_editor)

        # Per-catalog marker/connector color (brief: "each catalog should have its own
        # color... modify the Marker and Connector"). One swatch per catalog, defaulting
        # to DEFAULT_CATALOG_COLORS the first time the app ever runs and to whatever the
        # user last set after that (main_window wires persistence via set_catalog_colors
        # / catalog_color_changed). An object's own per-object marker override (Selected
        # Object tab, "Use global style for this object" unchecked) still always wins
        # over its catalog's color -- see renderer.compute_marker_geometry.
        catalog_colors_tab = QWidget()
        catalog_colors_layout = QFormLayout(catalog_colors_tab)
        self.catalog_color_buttons: dict[str, ColorButton] = {}
        for key, label in SUPPORTED_CATALOGS.items():
            btn = ColorButton(DEFAULT_CATALOG_COLORS.get(key, "#ffffff"))
            btn.color_changed.connect(lambda hex_color, k=key: self.catalog_color_changed.emit(k, hex_color))
            catalog_colors_layout.addRow(label, btn)
            self.catalog_color_buttons[key] = btn
        # Not driven by SUPPORTED_CATALOGS (deliberately -- see that dict's own
        # comment: it's scoped to catalogs the app can actually *query*, and "user"
        # isn't one). Added as its own row so a manually-placed custom object still
        # gets a color swatch here like every other catalog.
        user_btn = ColorButton(DEFAULT_CATALOG_COLORS.get("user", "#ffffff"))
        user_btn.color_changed.connect(lambda hex_color: self.catalog_color_changed.emit("user", hex_color))
        catalog_colors_layout.addRow("Custom Objects", user_btn)
        self.catalog_color_buttons["user"] = user_btn

        # RA/Dec grid + compass style customization -- on/off itself lives on the
        # toolbar's "Overlays" menu (mirrors Catalogs: that button toggles visibility,
        # this tab only customizes appearance), not duplicated here as a second source
        # of truth for the same enabled flag.
        overlays_tab = QWidget()
        overlays_layout = QVBoxLayout(overlays_tab)

        grid_group = QGroupBox("RA/Dec Grid")
        grid_form = QFormLayout(grid_group)
        self.grid_color = ColorButton("#66AADD")
        self.grid_opacity = DarkDoubleSpinBox()
        self.grid_opacity.setRange(0.05, 1.0)
        self.grid_opacity.setSingleStep(0.05)
        # Range tops out well above what a flat default ever needed -- per user
        # request, the default is now resolution-scaled (see persistence/presets.py's
        # default_overlay_settings_for_image) and can legitimately land north of 5px
        # on a large image; the old 5.0 cap was silently clamping that default on
        # anything above roughly a 7500px short edge.
        self.grid_line_width = DarkDoubleSpinBox()
        self.grid_line_width.setRange(0.2, 40.0)
        self.grid_line_width.setSingleStep(0.2)
        self.grid_show_labels = QCheckBox("Show coordinate labels")
        self.grid_show_labels.setChecked(True)
        # Range tops out well above the plain 6-24 an object label's font size uses --
        # per user request, this is now resolution-scaled (see
        # persistence/presets.py's default_overlay_settings_for_image) and can
        # legitimately land north of 100pt on a large image.
        self.grid_label_size = DarkDoubleSpinBox()
        self.grid_label_size.setRange(6.0, 200.0)
        self.grid_ra_position = QComboBox()
        for pos in RaLabelPosition:
            self.grid_ra_position.addItem(pos.value.capitalize(), pos)
        self.grid_dec_position = QComboBox()
        for pos in DecLabelPosition:
            self.grid_dec_position.addItem(pos.value.capitalize(), pos)
        grid_form.addRow("Color", self.grid_color)
        grid_form.addRow("Opacity", self.grid_opacity)
        grid_form.addRow("Line width", self.grid_line_width)
        grid_form.addRow(self.grid_show_labels)
        grid_form.addRow("Label size", self.grid_label_size)
        grid_form.addRow("RA labels", self.grid_ra_position)
        grid_form.addRow("Dec labels", self.grid_dec_position)

        compass_group = QGroupBox("Compass")
        compass_form = QFormLayout(compass_group)
        self.compass_color = ColorButton("#88CCFF")
        # Same reasoning/cap as grid_line_width above.
        self.compass_line_width = DarkDoubleSpinBox()
        self.compass_line_width.setRange(0.2, 40.0)
        self.compass_line_width.setSingleStep(0.2)
        self.compass_arrow_size = DarkDoubleSpinBox()
        self.compass_arrow_size.setRange(0.02, 0.2)
        self.compass_arrow_size.setSingleStep(0.01)
        self.compass_label_size = DarkDoubleSpinBox()
        self.compass_label_size.setRange(6.0, 200.0)
        # Same "appear only once overridden" convention as the marker's own Reset
        # Position button -- see set_overlay_settings.
        self.reset_compass_btn = QPushButton("Reset Position")
        self.reset_compass_btn.clicked.connect(self.reset_compass_position_requested)
        compass_form.addRow("Color", self.compass_color)
        compass_form.addRow("Line width", self.compass_line_width)
        compass_form.addRow("Arrow size", self.compass_arrow_size)
        compass_form.addRow("Label size", self.compass_label_size)
        compass_form.addRow(self.reset_compass_btn)

        # Technical-details text box (camera/telescope/filter/etc.) -- per user
        # request: pre-populated from the FITS header (see MainWindow._default_info_
        # box_text) as real, editable text, same "starts real, not a placeholder"
        # convention as an object's custom display name. On/off itself lives on the
        # toolbar's "Overlays" menu, same split as Grid/Compass above.
        info_box_group = QGroupBox("Info Box")
        info_box_layout = QVBoxLayout(info_box_group)
        self.info_box_text_edit = QPlainTextEdit()
        self.info_box_text_edit.setFixedHeight(110)
        self.info_box_text_edit.setPlaceholderText("Camera, telescope, filter, exposure, etc.")
        info_box_form = QFormLayout()
        self.info_box_corner = QComboBox()
        for corner in InfoBoxCorner:
            self.info_box_corner.addItem(corner.value.replace("_", " ").title(), corner)
        self.info_box_bg_color = ColorButton("#000000")
        self.info_box_bg_opacity = DarkDoubleSpinBox()
        self.info_box_bg_opacity.setRange(0.0, 1.0)
        self.info_box_bg_opacity.setSingleStep(0.05)
        self.info_box_border_radius = DarkDoubleSpinBox()
        self.info_box_border_radius.setRange(0.0, 40.0)
        self.info_box_padding = DarkDoubleSpinBox()
        self.info_box_padding.setRange(0.0, 40.0)
        self.info_box_text_color = ColorButton("#f2f2f2")
        self.info_box_font_size = DarkDoubleSpinBox()
        self.info_box_font_size.setRange(6.0, 200.0)
        # Same "appear only once overridden" convention as the marker's/compass's own
        # Reset Position button -- see set_overlay_settings.
        self.reset_info_box_btn = QPushButton("Reset Position")
        self.reset_info_box_btn.clicked.connect(self.reset_info_box_position_requested)
        info_box_form.addRow("Corner", self.info_box_corner)
        info_box_form.addRow("Background", self.info_box_bg_color)
        info_box_form.addRow("Background opacity", self.info_box_bg_opacity)
        info_box_form.addRow("Border radius", self.info_box_border_radius)
        info_box_form.addRow("Padding", self.info_box_padding)
        info_box_form.addRow("Text color", self.info_box_text_color)
        info_box_form.addRow("Font size", self.info_box_font_size)
        info_box_form.addRow(self.reset_info_box_btn)
        info_box_layout.addWidget(self.info_box_text_edit)
        info_box_layout.addLayout(info_box_form)

        # Stick-figure lines + name labels (Siril's own bundled constellations.csv/
        # constellationsnames.csv, see annotation/constellations.py) -- on/off itself
        # lives on the toolbar's "Overlays" menu, same split as Grid/Compass/Info Box
        # above.
        constellations_group = QGroupBox("Constellations")
        constellations_form = QFormLayout(constellations_group)
        self.constellations_color = ColorButton("#A9B4C2")
        self.constellations_opacity = DarkDoubleSpinBox()
        self.constellations_opacity.setRange(0.05, 1.0)
        self.constellations_opacity.setSingleStep(0.05)
        # Same reasoning/cap as grid_line_width above.
        self.constellations_line_width = DarkDoubleSpinBox()
        self.constellations_line_width.setRange(0.2, 40.0)
        self.constellations_line_width.setSingleStep(0.2)
        self.constellations_show_labels = QCheckBox("Show constellation names")
        self.constellations_show_labels.setChecked(True)
        # Same reasoning/cap as grid_label_size above.
        self.constellations_label_size = DarkDoubleSpinBox()
        self.constellations_label_size.setRange(6.0, 200.0)
        constellations_form.addRow("Color", self.constellations_color)
        constellations_form.addRow("Opacity", self.constellations_opacity)
        constellations_form.addRow("Line width", self.constellations_line_width)
        constellations_form.addRow(self.constellations_show_labels)
        constellations_form.addRow("Label size", self.constellations_label_size)

        overlays_layout.addWidget(info_box_group)
        overlays_layout.addWidget(grid_group)
        overlays_layout.addWidget(compass_group)
        overlays_layout.addWidget(constellations_group)
        overlays_layout.addStretch(1)

        for w in (
            self.grid_opacity, self.grid_line_width, self.grid_show_labels, self.grid_label_size,
            self.grid_ra_position, self.grid_dec_position,
            self.compass_line_width, self.compass_arrow_size, self.compass_label_size,
            self.info_box_corner, self.info_box_bg_opacity, self.info_box_border_radius,
            self.info_box_padding, self.info_box_font_size,
            self.constellations_opacity, self.constellations_line_width,
            self.constellations_show_labels, self.constellations_label_size,
        ):
            signal = (
                getattr(w, "currentIndexChanged", None)
                or getattr(w, "valueChanged", None)
                or getattr(w, "toggled", None)
            )
            signal.connect(self.overlay_settings_changed)
        for btn in (
            self.grid_color, self.compass_color, self.info_box_bg_color, self.info_box_text_color,
            self.constellations_color,
        ):
            btn.color_changed.connect(lambda _c: self.overlay_settings_changed.emit())
        self.info_box_text_edit.textChanged.connect(self.overlay_settings_changed)

        # The style editors (Marker/Label/Background/Text effects/Connector group
        # boxes) are taller than the dock is usually given room for -- confirmed by a
        # real screenshot where the Connector section was cut off with no way to reach
        # it. Wrap each tab's content in a scroll area rather than relying on the dock
        # itself being tall enough.
        self.tabs.addTab(_scrollable(self.global_editor), "Global Style")
        self.tabs.addTab(_scrollable(self.object_tab), "Selected Object")
        self.tabs.addTab(_scrollable(catalog_colors_tab), "Catalog Colors")
        self.tabs.addTab(_scrollable(overlays_tab), "Overlays")
        layout.addWidget(self.tabs)

        self._refresh_preset_list()
        self.preset_combo.currentIndexChanged.connect(self._apply_selected_preset)
        self.preset_combo.currentIndexChanged.connect(self._update_delete_button_state)
        self.save_preset_btn.clicked.connect(self._save_current_as_preset)
        self.delete_preset_btn.clicked.connect(self._delete_current_preset)
        self.reset_style_btn.clicked.connect(self.reset_style_requested)
        self.global_editor.changed.connect(self._on_global_edited)
        self.object_editor.changed.connect(self._on_object_edited)
        self.custom_name_edit.textChanged.connect(self._on_object_meta_edited)
        self.priority_spin.valueChanged.connect(self._on_object_meta_edited)
        self.locked_check.toggled.connect(self._on_object_meta_edited)
        self.use_global_check.toggled.connect(self._on_use_global_toggled)
        self.reset_position_btn.clicked.connect(self.reset_marker_position_requested)

        self.set_selected_annotation(None)

    def _refresh_preset_list(self) -> None:
        self.preset_combo.blockSignals(True)
        self.preset_combo.clear()
        for name in preset_store.all_presets():
            self.preset_combo.addItem(name)
        self.preset_combo.blockSignals(False)
        self._update_delete_button_state()

    def _update_delete_button_state(self) -> None:
        # Minimal Modern (and any other built-in) can never be deleted -- it's the
        # guaranteed fallback a user always has a way back to.
        name = self.preset_combo.currentText()
        self.delete_preset_btn.setEnabled(bool(name) and name not in preset_store.BUILTIN_PRESETS)

    def show_object_tab(self) -> None:
        """Switches to "Selected Object" (index 1 -- see the addTab order above) so a
        double-click on the canvas lands the user directly on that object's own
        controls instead of wherever the Style tab happened to be left."""
        self.tabs.setCurrentIndex(1)

    def show_overlays_tab(self) -> None:
        """Switches to "Overlays" (index 3 -- see the addTab order above) -- same
        pattern as show_object_tab, used when clicking the Info Box overlay itself so
        editing its text is a single click away instead of hunting for this tab."""
        self.tabs.setCurrentIndex(3)

    def set_overlay_settings(self, settings) -> None:
        """Syncs the Overlays tab's widgets from the current OverlaySettings -- called
        once at startup and again after every drag/undo/redo of the compass, so
        reset_compass_btn's visibility (and the color/size widgets, in case a saved
        project file is reloaded) stay correct without the widgets themselves owning
        state main_window doesn't know about."""
        block = self.blockSignals(True)
        self.grid_color.set_color(settings.grid.color)
        self.grid_opacity.setValue(settings.grid.opacity)
        self.grid_line_width.setValue(settings.grid.line_width)
        self.grid_show_labels.setChecked(settings.grid.show_labels)
        self.grid_label_size.setValue(settings.grid.label_font_size)
        self.grid_ra_position.setCurrentIndex(self.grid_ra_position.findData(settings.grid.ra_label_position))
        self.grid_dec_position.setCurrentIndex(self.grid_dec_position.findData(settings.grid.dec_label_position))
        self.compass_color.set_color(settings.compass.color)
        self.compass_line_width.setValue(settings.compass.line_width)
        self.compass_arrow_size.setValue(settings.compass.arrow_length_fraction)
        self.compass_label_size.setValue(settings.compass.label_font_size)
        # setPlainText always resets the cursor to the start, even when the new text
        # is identical to what's already there -- and this method gets called after
        # *every* overlay refresh, including this field's own textChanged edits (via
        # _on_overlay_style_edited -> _refresh_overlays -> here). Without this guard,
        # every keystroke would immediately reset the cursor back to position 0 --
        # same class of bug as custom_name_edit's blockSignals guard in
        # set_selected_annotation, different mechanism since this widget's own text
        # really can be re-set from several different call sites.
        if self.info_box_text_edit.toPlainText() != settings.info_box.text:
            self.info_box_text_edit.setPlainText(settings.info_box.text)
        self.info_box_corner.setCurrentIndex(self.info_box_corner.findData(settings.info_box.corner))
        self.info_box_bg_color.set_color(settings.info_box.background_color)
        self.info_box_bg_opacity.setValue(settings.info_box.background_opacity)
        self.info_box_border_radius.setValue(settings.info_box.border_radius)
        self.info_box_padding.setValue(settings.info_box.padding)
        self.info_box_text_color.set_color(settings.info_box.text_color)
        self.info_box_font_size.setValue(settings.info_box.font_size)
        self.constellations_color.set_color(settings.constellations.color)
        self.constellations_opacity.setValue(settings.constellations.opacity)
        self.constellations_line_width.setValue(settings.constellations.line_width)
        self.constellations_show_labels.setChecked(settings.constellations.show_labels)
        self.constellations_label_size.setValue(settings.constellations.label_font_size)
        self.blockSignals(block)
        self.reset_compass_btn.setVisible(settings.compass.anchor_x is not None)
        self.reset_info_box_btn.setVisible(settings.info_box.anchor_x is not None)

    def pending_grid_style_values(self) -> dict:
        return {
            "color": self.grid_color.hex_color,
            "opacity": self.grid_opacity.value(),
            "line_width": self.grid_line_width.value(),
            "show_labels": self.grid_show_labels.isChecked(),
            "label_font_size": self.grid_label_size.value(),
            "ra_label_position": self.grid_ra_position.currentData(),
            "dec_label_position": self.grid_dec_position.currentData(),
        }

    def pending_compass_style_values(self) -> dict:
        return {
            "color": self.compass_color.hex_color,
            "line_width": self.compass_line_width.value(),
            "arrow_length_fraction": self.compass_arrow_size.value(),
            "label_font_size": self.compass_label_size.value(),
        }

    def pending_info_box_style_values(self) -> dict:
        return {
            "text": self.info_box_text_edit.toPlainText(),
            "corner": self.info_box_corner.currentData(),
            "background_color": self.info_box_bg_color.hex_color,
            "background_opacity": self.info_box_bg_opacity.value(),
            "border_radius": self.info_box_border_radius.value(),
            "padding": self.info_box_padding.value(),
            "text_color": self.info_box_text_color.hex_color,
            "font_size": self.info_box_font_size.value(),
        }

    def pending_constellation_style_values(self) -> dict:
        return {
            "color": self.constellations_color.hex_color,
            "opacity": self.constellations_opacity.value(),
            "line_width": self.constellations_line_width.value(),
            "show_labels": self.constellations_show_labels.isChecked(),
            "label_font_size": self.constellations_label_size.value(),
        }

    def set_catalog_colors(self, colors: dict[str, str]) -> None:
        # Kept (not just applied to the swatches below) so set_selected_annotation can
        # show a not-yet-overridden object's *actual* rendered marker/connector color
        # (its catalog's color) instead of the flat global default -- see
        # resolve_marker_color/resolve_connector_color.
        self._catalog_colors = colors
        for key, btn in self.catalog_color_buttons.items():
            color = colors.get(key)
            if color:
                btn.blockSignals(True)
                btn.set_color(color)
                btn.blockSignals(False)

    def set_global_style(self, style: StylePreset) -> None:
        self._global_style = style
        self.global_editor.load(
            style.marker_style, style.label_style, style.connector_style,
            style.connector_color, style.connector_width,
        )

    def global_style(self) -> StylePreset:
        assert self._global_style is not None
        return self._global_style

    def set_selected_annotation(self, annotation: Annotation | None) -> None:
        self._selected = annotation
        has_selection = annotation is not None
        self.object_placeholder.setVisible(not has_selection)
        self.object_editor.setVisible(has_selection)
        self.custom_name_label.setVisible(has_selection)
        self.custom_name_edit.setVisible(has_selection)
        self.locked_check.setVisible(has_selection)
        self.use_global_check.setVisible(has_selection)
        self.priority_spin.setVisible(has_selection)
        self.priority_label.setVisible(has_selection)
        if not has_selection:
            self.reset_position_btn.setVisible(False)
            return
        self.reset_position_btn.setVisible(annotation.marker_x is not None)
        # Pre-fill with the object's actual current name as real, editable text (not
        # placeholder text) -- per user request, so starting to type adds to/edits the
        # existing name in place instead of the field looking pre-filled but actually
        # being empty, which silently discarded the original name the moment you typed
        # anything. blockSignals still guards against merely *selecting* an object
        # committing this text as a custom_display_name override on its own; only an
        # actual edit (any keystroke, via _on_object_meta_edited) commits it.
        name_mode = self._global_style.label_style.name_display if self._global_style else NameDisplayMode.CATALOG_ONLY
        self.custom_name_edit.blockSignals(True)
        self.custom_name_edit.setPlainText(annotation.custom_display_name or annotation.display_name(name_mode))
        self.custom_name_edit.blockSignals(False)
        # priority_spin/locked_check must also be blocked while syncing them to the
        # newly-selected object: both are wired to _on_object_meta_edited, which reads
        # *all three* fields (including custom_name_edit) via pending_object_meta_values
        # and commits them as a single meta update. Previously custom_name_edit's real
        # text was always empty on selection, so an unblocked setValue/setChecked firing
        # here was a harmless no-op (committed custom_display_name=None over None) --
        # but now that the field is pre-filled with real text, the same unblocked signal
        # would silently commit that name as a real override just from selecting an
        # object whose priority/locked state happens to differ from the previous one.
        self.priority_spin.blockSignals(True)
        self.priority_spin.setValue(annotation.priority)
        self.priority_spin.blockSignals(False)
        self.locked_check.blockSignals(True)
        self.locked_check.setChecked(annotation.locked)
        self.locked_check.blockSignals(False)
        has_override = (
            annotation.marker_style is not None
            or annotation.label_style is not None
            or annotation.connector_style is not None
            or annotation.connector_color is not None
            or annotation.connector_width is not None
        )
        # blockSignals is essential here, not decorative -- _on_use_global_toggled
        # (wired to this checkbox's toggled signal) emits object_style_changed, which
        # commits whatever pending_object_style_values() reads *right now* from
        # object_editor's widgets. But object_editor.load() below -- which is what
        # actually re-populates those widgets for the newly-selected object -- hasn't
        # run yet at this point in the method. Left unblocked, a real state transition
        # here (e.g. re-selecting an overridden object right after a different one with
        # a different override state) would fire _on_use_global_toggled with the STALE,
        # previous object's values still sitting in the editor, silently clobbering the
        # newly-selected object's real override with garbage -- a real, confirmed
        # report: a per-object Brackets shape override reverted to Circle after
        # deselecting and reselecting the same object.
        self.use_global_check.blockSignals(True)
        self.use_global_check.setChecked(not has_override)
        self.use_global_check.blockSignals(False)
        self.object_editor.setEnabled(has_override)
        # Marker/connector *color* specifically is resolved through catalog_colors,
        # not just "override or flat global default" -- otherwise a not-yet-overridden
        # object's editor showed the flat global color (e.g. white) instead of the
        # catalog pastel actually rendered for it, and unchecking "Use global style"
        # would silently commit that flat color as a real override the instant it was
        # unchecked, before the user touched anything (a real, confirmed report).
        marker = annotation.marker_style or self._global_style.marker_style
        marker = replace(marker, color=resolve_marker_color(annotation, self._global_style, self._catalog_colors))
        label = annotation.label_style or self._global_style.label_style
        self.object_editor.load(
            marker, label,
            annotation.effective_connector_style(self._global_style),
            resolve_connector_color(annotation, self._global_style, self._catalog_colors),
            annotation.effective_connector_width(self._global_style),
            connector_enabled_default=annotation.connector_enabled,
        )

    def _apply_selected_preset(self) -> None:
        name = self.preset_combo.currentText()
        preset = preset_store.all_presets().get(name)
        if preset:
            self.set_global_style(replace(preset, name=self._global_style.name if self._global_style else preset.name))
            self.global_style_changed.emit()

    def _save_current_as_preset(self) -> None:
        name, ok = QInputDialog.getText(self, "Save Preset", "Preset name:")
        if not ok or not name.strip():
            return
        preset = replace(
            self.global_style(),
            name=name.strip(),
            marker_style=self.global_editor.marker_style(),
            label_style=self.global_editor.label_style(),
            connector_style=self.global_editor.connector_style_value(),
            connector_color=self.global_editor.connector_color_value(),
            connector_width=self.global_editor.connector_width_value(),
        )
        preset_store.save_user_preset(preset)
        self._refresh_preset_list()
        self.preset_combo.setCurrentText(name.strip())

    def _delete_current_preset(self) -> None:
        name = self.preset_combo.currentText()
        if not name or name in preset_store.BUILTIN_PRESETS:
            return  # Minimal Modern (or any future built-in) is never deletable.
        reply = QMessageBox.question(
            self, "Delete Preset", f'Delete the preset "{name}"? This cannot be undone.',
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        preset_store.delete_user_preset(name)
        self._refresh_preset_list()
        # The deleted preset was the one currently applied (it's the only thing this
        # combo can be showing), so fall back to Minimal Modern immediately per user
        # request -- but via reset_style_requested (main_window's resolution-scaled
        # "Reset to Default", the same path the Reset button uses), not by just
        # re-selecting "Minimal Modern" here: re-selecting would apply its *raw*,
        # un-scaled values, which on a high-resolution image looks just as broken as
        # the "Scientific"-preset regression that button was built to fix. Block
        # signals while updating the combo display so _apply_selected_preset doesn't
        # also fire and push a redundant, briefly-wrong intermediate style.
        self.preset_combo.blockSignals(True)
        self.preset_combo.setCurrentText(preset_store.DEFAULT_PRESET_NAME)
        self.preset_combo.blockSignals(False)
        self._update_delete_button_state()
        self.reset_style_requested.emit()

    def _on_global_edited(self) -> None:
        if self._global_style is None:
            return
        self._global_style = replace(
            self._global_style,
            marker_style=self.global_editor.marker_style(),
            label_style=self.global_editor.label_style(),
            connector_style=self.global_editor.connector_style_value(),
            connector_color=self.global_editor.connector_color_value(),
            connector_width=self.global_editor.connector_width_value(),
        )
        self.global_style_changed.emit()

    def pending_object_style_values(self) -> dict:
        """Read by MainWindow after object_style_changed fires — this widget never
        mutates the Annotation directly, so undo/redo has a clean old/new snapshot.
        connector_style/color/width are included alongside marker_style/label_style --
        real gap this fixes: the Connector group in the "Selected Object" tab's editor
        was fully interactive but silently did nothing, since Annotation had no fields
        to capture those values into at all."""
        if self.use_global_check.isChecked():
            return {
                "marker_style": None, "label_style": None,
                "connector_style": None, "connector_color": None, "connector_width": None,
            }
        return {
            "marker_style": self.object_editor.marker_style(),
            "label_style": self.object_editor.label_style(),
            "connector_enabled": self.object_editor.connector_enabled_value(),
            "connector_style": self.object_editor.connector_style_value(),
            "connector_color": self.object_editor.connector_color_value(),
            "connector_width": self.object_editor.connector_width_value(),
        }

    def pending_object_meta_values(self) -> dict:
        return {
            "custom_display_name": self.custom_name_edit.toPlainText().strip() or None,
            "priority": self.priority_spin.value(),
            "locked": self.locked_check.isChecked(),
        }

    def _on_object_edited(self) -> None:
        if self._selected is None or self.use_global_check.isChecked():
            return
        self.object_style_changed.emit(self._selected.id)

    def _on_object_meta_edited(self, *_args) -> None:
        if self._selected is None:
            return
        self.object_meta_changed.emit(self._selected.id)

    def _on_use_global_toggled(self, checked: bool) -> None:
        if self._selected is None:
            return
        self.object_editor.setEnabled(not checked)
        self.object_style_changed.emit(self._selected.id)
