"""Object selection panel: table of detected objects with visibility/search/filter
(brief #12) and bidirectional selection sync with the canvas.
"""

from __future__ import annotations

from PyQt6.QtCore import QAbstractTableModel, QModelIndex, QSortFilterProxyModel, Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QHBoxLayout,
    QHeaderView,
    QLineEdit,
    QPushButton,
    QTableView,
    QVBoxLayout,
    QWidget,
)

from ..annotation.models import Annotation, NameDisplayMode

_COLUMNS = ["Visible", "Object", "Catalog", "Type", "Magnitude", "Size"]


class AnnotationTableModel(QAbstractTableModel):
    visibility_changed = pyqtSignal(str, bool)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._annotations: list[Annotation] = []
        self._name_display = NameDisplayMode.CATALOG_ONLY

    def set_annotations(self, annotations: list[Annotation]) -> None:
        self.beginResetModel()
        self._annotations = annotations
        self.endResetModel()

    def set_name_display_mode(self, mode: NameDisplayMode) -> None:
        self._name_display = mode
        if self._annotations:
            top_left = self.index(0, 1)
            bottom_right = self.index(len(self._annotations) - 1, 1)
            self.dataChanged.emit(top_left, bottom_right)

    def annotation_at(self, row: int) -> Annotation:
        return self._annotations[row]

    def row_for_id(self, annotation_id: str) -> int | None:
        for i, a in enumerate(self._annotations):
            if a.id == annotation_id:
                return i
        return None

    def rowCount(self, parent=QModelIndex()) -> int:
        return 0 if parent.isValid() else len(self._annotations)

    def columnCount(self, parent=QModelIndex()) -> int:
        return len(_COLUMNS)

    def headerData(self, section, orientation, role=Qt.ItemDataRole.DisplayRole):
        if orientation == Qt.Orientation.Horizontal and role == Qt.ItemDataRole.DisplayRole:
            return _COLUMNS[section]
        return None

    def flags(self, index: QModelIndex):
        base = Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable
        if index.column() == 0:
            return base | Qt.ItemFlag.ItemIsUserCheckable
        return base

    def data(self, index: QModelIndex, role=Qt.ItemDataRole.DisplayRole):
        if not index.isValid():
            return None
        ann = self._annotations[index.row()]
        col = index.column()
        if col == 0 and role == Qt.ItemDataRole.CheckStateRole:
            return Qt.CheckState.Checked if ann.enabled else Qt.CheckState.Unchecked
        if role != Qt.ItemDataRole.DisplayRole:
            return None
        if col == 1:
            return ann.display_name(self._name_display)
        if col == 2:
            return ann.catalog
        if col == 3:
            return ann.object_type
        if col == 4:
            return f"{ann.magnitude:.1f}" if ann.magnitude is not None else ""
        if col == 5:
            return f"{ann.angular_size:.1f}'" if ann.angular_size is not None else ""
        return None

    def setData(self, index: QModelIndex, value, role=Qt.ItemDataRole.EditRole):
        if index.column() == 0 and role == Qt.ItemDataRole.CheckStateRole:
            ann = self._annotations[index.row()]
            # Mutation and the resulting dataChanged signal happen inside MainWindow's
            # undo command (see visibility_changed handling) so this checkbox click is
            # undoable rather than bypassing the undo stack.
            self.visibility_changed.emit(ann.id, value == Qt.CheckState.Checked.value)
            return True
        return False


class ObjectFilterProxyModel(QSortFilterProxyModel):
    """Combines free-text search (object name) and a catalog dropdown filter
    simultaneously, which a single stock QSortFilterProxyModel filter key cannot do."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._search_text = ""
        self._catalog: str | None = None

    def set_search_text(self, text: str) -> None:
        self._search_text = text.strip().lower()
        self.invalidateFilter()

    def set_catalog(self, catalog: str | None) -> None:
        self._catalog = catalog
        self.invalidateFilter()

    def filterAcceptsRow(self, source_row: int, source_parent: QModelIndex) -> bool:
        model = self.sourceModel()
        if self._catalog is not None:
            catalog_index = model.index(source_row, 2, source_parent)
            if str(model.data(catalog_index)) != self._catalog:
                return False
        if self._search_text:
            name_index = model.index(source_row, 1, source_parent)
            if self._search_text not in str(model.data(name_index)).lower():
                return False
        return True


class ObjectPanel(QWidget):
    selection_changed = pyqtSignal(str)  # annotation id
    visibility_changed = pyqtSignal(str, bool)
    select_all_requested = pyqtSignal(list)  # annotation ids currently shown in the table
    deselect_all_requested = pyqtSignal(list)  # annotation ids currently shown in the table
    reset_requested = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.model = AnnotationTableModel(self)
        self.model.visibility_changed.connect(self.visibility_changed)
        self.proxy = ObjectFilterProxyModel(self)
        self.proxy.setSourceModel(self.model)

        self.search_box = QLineEdit()
        self.search_box.setPlaceholderText("Search objects…")
        self.search_box.textChanged.connect(self.proxy.set_search_text)

        self.catalog_filter = QComboBox()
        self.catalog_filter.addItem("All catalogs", None)
        self.catalog_filter.currentIndexChanged.connect(
            lambda _i: self.proxy.set_catalog(self.catalog_filter.currentData())
        )

        self.table = QTableView()
        self.table.setModel(self.proxy)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self.table.verticalHeader().setVisible(False)
        self.table.setSortingEnabled(True)
        self.table.selectionModel().currentRowChanged.connect(self._on_row_changed)

        btn_row = QHBoxLayout()
        # Named "Show"/"Hide" rather than "Select"/"Deselect" -- these toggle the
        # Visible checkbox column, not table row selection (a separate, existing
        # concept: selection_changed above, which drives the Style panel's "Selected
        # Object" tab). Scoped to whatever the search box/catalog filter currently
        # shows, not every object regardless of filter -- per a real workflow request:
        # filter down to one catalog, hide all of it in one click, then re-check just
        # the one or two objects actually wanted, instead of unchecking everything
        # else one row at a time.
        select_all_btn = QPushButton("Show All")
        select_all_btn.setToolTip("Make every object currently shown in the table below visible")
        deselect_all_btn = QPushButton("Hide All")
        deselect_all_btn.setToolTip("Hide every object currently shown in the table below")
        reset_btn = QPushButton("Reset")
        select_all_btn.clicked.connect(lambda: self.select_all_requested.emit(self._filtered_annotation_ids()))
        deselect_all_btn.clicked.connect(lambda: self.deselect_all_requested.emit(self._filtered_annotation_ids()))
        reset_btn.clicked.connect(self.reset_requested)
        btn_row.addWidget(select_all_btn)
        btn_row.addWidget(deselect_all_btn)
        btn_row.addWidget(reset_btn)

        filter_row = QHBoxLayout()
        filter_row.addWidget(self.search_box, 2)
        filter_row.addWidget(self.catalog_filter, 1)

        layout = QVBoxLayout(self)
        layout.addLayout(filter_row)
        layout.addWidget(self.table)
        layout.addLayout(btn_row)

    def set_name_display_mode(self, mode: NameDisplayMode) -> None:
        self.model.set_name_display_mode(mode)

    def set_annotations(self, annotations: list[Annotation]) -> None:
        self.model.set_annotations(annotations)
        catalogs = sorted({a.catalog for a in annotations})
        current = self.catalog_filter.currentData()
        self.catalog_filter.blockSignals(True)
        self.catalog_filter.clear()
        self.catalog_filter.addItem("All catalogs", None)
        for c in catalogs:
            self.catalog_filter.addItem(c, c)
        idx = self.catalog_filter.findData(current)
        self.catalog_filter.setCurrentIndex(idx if idx >= 0 else 0)
        self.catalog_filter.blockSignals(False)

    def _filtered_annotation_ids(self) -> list[str]:
        """Annotation ids for the rows the proxy model currently shows -- i.e. respecting
        the search box and catalog dropdown, not every object regardless of filter."""
        ids = []
        for row in range(self.proxy.rowCount()):
            source_index = self.proxy.mapToSource(self.proxy.index(row, 0))
            ids.append(self.model.annotation_at(source_index.row()).id)
        return ids

    def refresh(self) -> None:
        if self.model.rowCount() == 0:
            return
        top_left = self.model.index(0, 0)
        bottom_right = self.model.index(self.model.rowCount() - 1, self.model.columnCount() - 1)
        self.model.dataChanged.emit(top_left, bottom_right)

    def select_annotation(self, annotation_id: str) -> None:
        row = self.model.row_for_id(annotation_id)
        if row is None:
            return
        source_index = self.model.index(row, 1)
        proxy_index = self.proxy.mapFromSource(source_index)
        selection_model = self.table.selectionModel()
        selection_model.setCurrentIndex(
            proxy_index,
            selection_model.SelectionFlag.ClearAndSelect | selection_model.SelectionFlag.Rows,
        )
        self.table.scrollTo(proxy_index)

    def clear_selection(self) -> None:
        # Deliberately clearCurrentIndex too, not just clearSelection -- clearSelection
        # alone leaves the table's *current* row still highlighted (a fainter "current,
        # not selected" outline), which would look like some row was still selected
        # after the user explicitly clicked empty canvas space to deselect everything.
        self.table.selectionModel().clearSelection()
        self.table.selectionModel().clearCurrentIndex()

    def _on_row_changed(self, current: QModelIndex, _previous: QModelIndex) -> None:
        if not current.isValid():
            return
        source_index = self.proxy.mapToSource(current)
        ann = self.model.annotation_at(source_index.row())
        self.selection_changed.emit(ann.id)
