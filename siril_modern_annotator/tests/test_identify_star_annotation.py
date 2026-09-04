"""MainWindow._create_star_annotation and _on_star_identify_results -- the "Identify
Star" right-click feature's GUI-side handling of star_identify.py's results (see that
module's own docstring for the SIMBAD lookup itself, covered separately/offline in
test_star_identify.py).

Regression test for the one correctness point a careless copy-paste from
_add_custom_object would get backwards: a custom object's ra/dec is *derived from* the
click (the click is the only position there is), but an identified star's ra/dec is
SIMBAD's own resolved position -- authoritative, not the click, which was only ever the
search center. Getting this backwards would leave the marker sitting at the click
instead of the star's real cataloged position, silently defeating the point of doing a
lookup at all.

No real Qt scene needed -- built against a bare MainWindow instance via
MainWindow.__new__ (see test_deferred_item_cleanup.py's own comment on why, skipping
__init__ and the live sirilpy bridge it needs), with real QUndoStack/QUndoCommand
machinery (AddAnnotationCommand is a plain QUndoCommand, already covered directly in
test_custom_object.py) but every scene-touching callback stubbed out."""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtGui import QUndoStack
from PyQt6.QtWidgets import QApplication

from siril_modern_annotator.annotation.star_identify import StarCandidate
from siril_modern_annotator.gui.main_window import MainWindow

_app = QApplication.instance() or QApplication([])


class _FakeWcs:
    """world_to_pixel returns a position deliberately different from any click
    position a test might otherwise be tempted to pass in -- see world_to_pixel's own
    comment for why that's the point."""

    def world_to_pixel(self, ra: float, dec: float):
        # A fixed, recognizable offset from (ra, dec) -- distinct from any "click"
        # coordinate a regression here might otherwise produce by coincidence.
        return ra + 1000.0, dec + 2000.0


def _bare_window() -> MainWindow:
    window = MainWindow.__new__(MainWindow)
    window.wcs = _FakeWcs()
    window.annotations = []
    window.undo_stack = QUndoStack()
    window._add_scene_items_for = lambda ann: None
    window._remove_scene_items_for = lambda ann: None
    window._refresh_after_annotation_count_change = lambda: None
    window.select_annotation = lambda annotation_id: None
    return window


def _candidate() -> StarCandidate:
    return StarCandidate(
        simbad_id="HD 192163", ra=303.0272916666666, dec=38.35494444444445,
        otype="WR*", magnitude=7.5, separation_arcsec=0.1,
    )


def test_create_star_annotation_uses_the_candidates_own_position_not_a_click():
    window = _bare_window()
    candidate = _candidate()

    window._create_star_annotation(candidate)

    assert len(window.annotations) == 1
    ann = window.annotations[0]
    assert ann.ra == candidate.ra
    assert ann.dec == candidate.dec
    # From _FakeWcs.world_to_pixel(candidate.ra, candidate.dec), not from any click
    # coordinate -- there is no click coordinate available inside _create_star_
    # annotation at all, by design (see its own comment), so this also stands as
    # confirmation the method never introduces one via a stray argument.
    assert ann.image_x == candidate.ra + 1000.0
    assert ann.image_y == candidate.dec + 2000.0


def test_create_star_annotation_sets_catalog_name_and_simbad_id_from_the_candidate():
    window = _bare_window()
    candidate = _candidate()

    window._create_star_annotation(candidate)

    ann = window.annotations[0]
    assert ann.catalog == "user"
    assert ann.catalog_name == "HD 192163"
    assert ann.simbad_id == "HD 192163"
    assert ann.magnitude == 7.5
    assert ann.object_type == "WR*"


def test_create_star_annotation_leaves_marker_style_unset():
    """Unlike Add Custom Object's own 1.6x radius bump (which exists to make a *blank*
    object stand out) -- an identified star already has a real designation, same as
    any other catalog star, so it should render unscaled like one."""
    window = _bare_window()
    window._create_star_annotation(_candidate())
    assert window.annotations[0].marker_style is None


def test_on_star_identify_results_with_no_candidates_places_nothing():
    window = _bare_window()
    window.connection_label = type("Label", (), {"setText": lambda self, text: None})()
    window._on_star_identify_results([], screen_pos=None)
    assert window.annotations == []


def test_on_star_identify_results_with_one_candidate_creates_it_directly():
    window = _bare_window()
    window._on_star_identify_results([_candidate()], screen_pos=None)
    assert len(window.annotations) == 1
    assert window.annotations[0].catalog_name == "HD 192163"
