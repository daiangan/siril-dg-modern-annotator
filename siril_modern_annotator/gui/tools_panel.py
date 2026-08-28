"""Tools dock tab: houses actions that used to live directly on the main toolbar
(Auto Arrange Labels, Save/Load Layout) -- per user request, to keep the toolbar itself
simple. Deliberately its own tab (not folded into Objects or Style) so more tools have
an obvious home later without the toolbar growing again.
"""

from __future__ import annotations

from PyQt6.QtCore import pyqtSignal
from PyQt6.QtWidgets import QGroupBox, QPushButton, QVBoxLayout, QWidget


class ToolsPanel(QWidget):
    auto_arrange_requested = pyqtSignal()
    save_layout_requested = pyqtSignal()
    load_layout_requested = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)

        layout_group = QGroupBox("Layout")
        layout_form = QVBoxLayout(layout_group)
        self.auto_arrange_btn = QPushButton("Auto Arrange Labels")
        self.save_layout_btn = QPushButton("Save Layout")
        self.load_layout_btn = QPushButton("Load Layout")
        self.auto_arrange_btn.clicked.connect(self.auto_arrange_requested)
        self.save_layout_btn.clicked.connect(self.save_layout_requested)
        self.load_layout_btn.clicked.connect(self.load_layout_requested)
        layout_form.addWidget(self.auto_arrange_btn)
        layout_form.addWidget(self.save_layout_btn)
        layout_form.addWidget(self.load_layout_btn)

        layout.addWidget(layout_group)
        layout.addStretch(1)
