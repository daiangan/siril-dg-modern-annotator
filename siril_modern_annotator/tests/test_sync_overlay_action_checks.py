"""MainWindow._sync_overlay_action_checks -- keeps the Overlays menu's checkable
actions (grid/compass/info box/constellations) in sync with self.overlay_settings.

Regression test for a real report: toggling the RA/Dec Grid overlay on, closing the
script, and reopening it against the same image correctly restored the grid on screen
(_setup_overlay_items reads self.overlay_settings directly) -- but the Overlays
dropdown still showed every entry unchecked. Root cause was that grid_action/
compass_action/info_box_action/constellations_action only ever had their checked state
set once, at __init__ time, from the placeholder OverlaySettings() that exists before
the first image even loads -- not the real settings _load_current_image() restores
moments later via last_used_store.apply_last_used_overlay_settings()."""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtGui import QAction
from PyQt6.QtWidgets import QApplication

from siril_modern_annotator.annotation.models import OverlaySettings
from siril_modern_annotator.gui.main_window import MainWindow

_app = QApplication.instance() or QApplication([])


def _checkable_action() -> QAction:
    action = QAction("test", checkable=True)
    action.setChecked(False)
    return action


def _bare_window(overlay_settings: OverlaySettings) -> MainWindow:
    # See test_deferred_item_cleanup.py's own comment on why MainWindow.__new__ (not
    # object.__new__) is needed to bypass __init__ (which needs a live sirilpy bridge).
    window = MainWindow.__new__(MainWindow)
    window.overlay_settings = overlay_settings
    window.grid_action = _checkable_action()
    window.compass_action = _checkable_action()
    window.info_box_action = _checkable_action()
    window.constellations_action = _checkable_action()
    return window


def test_sync_overlay_action_checks_reflects_every_enabled_overlay():
    settings = OverlaySettings()
    settings.grid.enabled = True
    settings.compass.enabled = False
    settings.info_box.enabled = True
    settings.constellations.enabled = False
    window = _bare_window(settings)

    window._sync_overlay_action_checks()

    assert window.grid_action.isChecked() is True
    assert window.compass_action.isChecked() is False
    assert window.info_box_action.isChecked() is True
    assert window.constellations_action.isChecked() is False


def test_sync_overlay_action_checks_turns_off_a_previously_checked_action():
    """The actual regression: a stale True from a previous overlay_settings object
    must not survive -- this must be a full sync, not just an "enable" pass."""
    settings = OverlaySettings()  # every overlay off by default
    window = _bare_window(settings)
    window.grid_action.setChecked(True)  # simulate a stale checked state

    window._sync_overlay_action_checks()

    assert window.grid_action.isChecked() is False


def test_sync_overlay_action_checks_does_not_trigger_toggled_signal():
    """Must use blockSignals -- otherwise this code-driven sync would be
    indistinguishable from a real user click, firing _on_grid_toggled/etc and
    redundantly re-running _setup_overlay_items."""
    settings = OverlaySettings()
    settings.grid.enabled = True
    window = _bare_window(settings)
    fired = []
    window.grid_action.toggled.connect(lambda checked: fired.append(checked))

    window._sync_overlay_action_checks()

    assert fired == []
