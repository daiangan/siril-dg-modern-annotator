"""ObjectPanel's double-click (jump to editing) and right-click ("Open in SIMBAD")
behavior -- per user request: single-click keeps its existing meaning (select/highlight
on canvas, see selection_changed), double-click mirrors the same gesture already used
for a marker/label on the canvas, and the SIMBAD link lives on its own right-click
action rather than overloading the object name with a second, conflicting click
meaning."""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import QPoint
from PyQt6.QtGui import QDesktopServices
from PyQt6.QtWidgets import QApplication, QMenu

from siril_modern_annotator.annotation.models import Annotation
from siril_modern_annotator.gui.object_panel import ObjectPanel, simbad_url_for

_app = QApplication.instance() or QApplication([])


def test_simbad_url_for_a_plain_catalog_name():
    # Public/standalone (no Qt dependency) specifically so gui/main_window.py's canvas
    # right-click menu can build the identical link without a second, drifting copy.
    assert simbad_url_for("messier", "M31") == "https://simbad.cds.unistra.fr/simbad/sim-id?Ident=M31"


def test_simbad_url_rewrites_ldn_to_the_format_simbad_accepts():
    # Regression test for a real report: Siril's own bundled ldn.csv spells this
    # "LdN-1712" (mixed case, hyphen) -- confirmed live that SIMBAD rejects that
    # outright ("incorrect format for catalogs") but accepts "LDN 1712".
    assert simbad_url_for("ldn", "LdN-1712") == "https://simbad.cds.unistra.fr/simbad/sim-id?Ident=LDN%201712"


def test_simbad_url_rewrites_barnard_to_the_unambiguous_full_word():
    # Regression test for a real report: confirmed live that bare "B42" (this app's
    # own catalog_name, see _vii220a_row_to_annotation) is ambiguous on SIMBAD (matches
    # GC/Batten catalogs too) -- only the full word "Barnard 42" resolves unambiguously.
    assert simbad_url_for("barnard", "B42") == "https://simbad.cds.unistra.fr/simbad/sim-id?Ident=Barnard%2042"


def test_simbad_url_prefers_simbad_id_over_catalog_name_when_present():
    # Regression test for a real report: "b01 Cyg" (V/50's reconstructed Bayer name)
    # is rejected by SIMBAD outright, unlike a per-catalog regex fixup (see LDN/Barnard
    # below), the data itself carries a reliable identifier (HD/HR from VizieR), so it
    # takes precedence over any catalog_name-based guessing.
    assert simbad_url_for("bright_star", "b01 Cyg", "HD 186408") == (
        "https://simbad.cds.unistra.fr/simbad/sim-id?Ident=HD%20186408"
    )


def test_simbad_url_falls_back_to_catalog_name_when_simbad_id_is_absent():
    assert simbad_url_for("bright_star", "α Cyg", None) == simbad_url_for("bright_star", "α Cyg")


def test_simbad_url_leaves_other_catalogs_unchanged():
    # Every other catalog's own catalog_name format was confirmed live to already
    # resolve correctly -- messier/ngc/ic/sh2/bright_star/user_dso all pass through.
    assert simbad_url_for("sh2", "Sh2-155") == "https://simbad.cds.unistra.fr/simbad/sim-id?Ident=Sh2-155"
    assert simbad_url_for("ngc", "NGC5471") == "https://simbad.cds.unistra.fr/simbad/sim-id?Ident=NGC5471"


def test_simbad_url_ldn_fixup_is_case_insensitive_and_falls_back_safely():
    # Same fixup applies regardless of incidental case, and a name that doesn't match
    # the expected "LdN-<digits>" shape at all is passed through unchanged rather than
    # silently mangled or raising.
    assert simbad_url_for("ldn", "LDN-42") == "https://simbad.cds.unistra.fr/simbad/sim-id?Ident=LDN%2042"
    assert simbad_url_for("ldn", "some odd name") == "https://simbad.cds.unistra.fr/simbad/sim-id?Ident=some%20odd%20name"


def _catalog_annotation() -> Annotation:
    return Annotation(
        catalog="messier", catalog_name="M31", ra=10.0, dec=41.0, image_x=100.0, image_y=200.0,
    )


def _custom_annotation() -> Annotation:
    return Annotation(
        catalog="user", catalog_name="Custom Object", ra=10.0, dec=41.0, image_x=100.0, image_y=200.0,
    )


def _row_center(panel: ObjectPanel, row: int = 0) -> QPoint:
    proxy_index = panel.proxy.index(row, 1)
    return panel.table.visualRect(proxy_index).center()


def test_double_click_emits_the_annotation_id():
    ann = _catalog_annotation()
    panel = ObjectPanel()
    panel.set_annotations([ann])
    emitted: list[str] = []
    panel.object_double_clicked.connect(emitted.append)
    panel.table.doubleClicked.emit(panel.proxy.index(0, 1))
    assert emitted == [ann.id]


def test_context_menu_opens_simbad_for_a_catalog_object(monkeypatch):
    ann = _catalog_annotation()
    panel = ObjectPanel()
    panel.set_annotations([ann])

    # Simulates the user picking whatever single action the menu offers, without
    # actually blocking on a real modal event loop.
    monkeypatch.setattr(QMenu, "exec", lambda self, *a, **kw: (self.actions() or [None])[0])
    opened: list[str] = []
    monkeypatch.setattr(QDesktopServices, "openUrl", lambda url: opened.append(url.toString()))

    panel._show_row_context_menu(_row_center(panel))
    assert len(opened) == 1
    assert opened[0] == "https://simbad.cds.unistra.fr/simbad/sim-id?Ident=M31"


def test_context_menu_applies_the_barnard_fixup():
    ann = Annotation(
        catalog="barnard", catalog_name="B42", ra=10.0, dec=41.0, image_x=100.0, image_y=200.0,
    )
    panel = ObjectPanel()
    panel.set_annotations([ann])
    assert simbad_url_for(ann.catalog, ann.catalog_name) == (
        "https://simbad.cds.unistra.fr/simbad/sim-id?Ident=Barnard%2042"
    )


def test_context_menu_uses_simbad_id_when_the_annotation_has_one(monkeypatch):
    ann = Annotation(
        catalog="bright_star", catalog_name="b01 Cyg", ra=10.0, dec=41.0,
        image_x=100.0, image_y=200.0, simbad_id="HD 186408",
    )
    panel = ObjectPanel()
    panel.set_annotations([ann])

    monkeypatch.setattr(QMenu, "exec", lambda self, *a, **kw: (self.actions() or [None])[0])
    opened: list[str] = []
    monkeypatch.setattr(QDesktopServices, "openUrl", lambda url: opened.append(url.toString()))

    panel._show_row_context_menu(_row_center(panel))
    # toString() reformats for human readability (decodes %20 back to a space); assert
    # on the identifier itself rather than the raw encoded bytes, as in the plain-name
    # SIMBAD tests above.
    assert opened == ["https://simbad.cds.unistra.fr/simbad/sim-id?Ident=HD 186408"]


def test_context_menu_omits_simbad_for_a_custom_object(monkeypatch):
    ann = _custom_annotation()
    panel = ObjectPanel()
    panel.set_annotations([ann])

    seen_actions: list[list[str]] = []

    def _record_and_return_none(self, *a, **kw):
        seen_actions.append([a.text() for a in self.actions()])
        return None

    monkeypatch.setattr(QMenu, "exec", _record_and_return_none)
    opened: list[str] = []
    monkeypatch.setattr(QDesktopServices, "openUrl", lambda url: opened.append(url.toString()))

    panel._show_row_context_menu(_row_center(panel))
    assert seen_actions == [[]]  # no "Open in SIMBAD" action offered at all
    assert opened == []


def test_context_menu_on_empty_area_does_nothing(monkeypatch):
    panel = ObjectPanel()  # no annotations at all -- table is empty
    exec_calls = []
    monkeypatch.setattr(QMenu, "exec", lambda self, *a, **kw: exec_calls.append(1))
    panel._show_row_context_menu(QPoint(5, 5))
    assert exec_calls == []


def test_simbad_url_encodes_a_name_with_a_space(monkeypatch):
    # Bright-star Bayer names carry a space (e.g. "ξ Cyg") -- must be percent-encoded,
    # not silently truncated or left raw in the query string.
    ann = Annotation(
        catalog="bright_star", catalog_name="ξ Cyg", ra=10.0, dec=41.0, image_x=100.0, image_y=200.0,
    )
    panel = ObjectPanel()
    panel.set_annotations([ann])
    monkeypatch.setattr(QMenu, "exec", lambda self, *a, **kw: (self.actions() or [None])[0])
    opened: list[str] = []
    # toEncoded() (not toString(), which Qt reformats for human readability and may
    # decode percent-escapes back) -- the actual bytes Qt would send/hand off.
    monkeypatch.setattr(QDesktopServices, "openUrl", lambda url: opened.append(bytes(url.toEncoded()).decode()))

    panel._show_row_context_menu(_row_center(panel))
    assert len(opened) == 1
    assert " " not in opened[0].split("Ident=")[1]
    assert "%CE%BE" in opened[0]  # percent-encoded ξ
