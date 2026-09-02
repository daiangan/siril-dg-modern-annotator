"""MainWindow._on_additional_catalog_results -- the dedup check run when a catalog is
toggled on *after* others are already loaded, to keep re-enabling a catalog from
duplicating objects another already-active catalog also carries (e.g. Messier + NGC).

Regression test for a real report: toggling WR on, then NGC on, silently dropped
NGC6888 entirely (and the reverse order dropped WR136 instead) -- whichever catalog
loaded first "won". Root cause was this dedup using a raw 30" proximity check against
*every* existing annotation regardless of catalog, unlike CompositeProvider._dedupe()
(catalogs.py) which only treats two objects as duplicates when _same_dedup_class() says
their catalogs are allowed to merge. WR136 sits ~2-3" from NGC6888 (it's the Crescent
Nebula's own central star) -- well inside the 30" threshold -- but "wr" and "ngc" are
not the same dedup class, so they must never be treated as duplicates of each other."""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication

from siril_modern_annotator.annotation.models import Annotation, StylePreset
from siril_modern_annotator.gui.main_window import MainWindow

_app = QApplication.instance() or QApplication([])

# Real-world figures: WR136 and NGC6888 (Crescent Nebula), ~2-3" apart.
_NGC6888_RA, _NGC6888_DEC = 303.02709, 38.355
_WR136_RA, _WR136_DEC = 303.0272916666666, 38.35494444444445


def _ann(catalog: str, catalog_name: str, ra: float, dec: float) -> Annotation:
    return Annotation(
        catalog=catalog, catalog_name=catalog_name, ra=ra, dec=dec,
        image_x=100.0, image_y=100.0,
    )


class _StubObjectPanel:
    def set_annotations(self, annotations):
        pass


class _StubLabel:
    def setText(self, text):
        pass


def _bare_window(existing: list[Annotation]) -> MainWindow:
    # See test_deferred_item_cleanup.py's own comment on why MainWindow.__new__ (not
    # object.__new__) is needed to bypass __init__ (which needs a live sirilpy bridge).
    window = MainWindow.__new__(MainWindow)
    window.annotations = list(existing)
    window.global_style_holder = [StylePreset(name="test")]
    window.arcsec_per_px = 1.0
    window.image_info = type("ImageInfo", (), {"width": 4000, "height": 3000})()
    window.source_identifier = "test.fits"
    window.object_panel = _StubObjectPanel()
    window.connection_label = _StubLabel()
    # Real scene/render side effects aren't under test here -- only which annotations
    # survive the dedup filter -- so these are stubbed out as no-ops.
    window._add_scene_items_for = lambda ann: None
    window._refresh_all = lambda: None
    return window


def test_a_catalog_toggled_on_second_is_not_dropped_by_a_coincident_object():
    """The exact regression: WR already loaded (from NGC6888's central star), then NGC
    toggled on -- NGC6888 must still appear, not get silently treated as a duplicate of
    WR136 just because they're a few arcseconds apart."""
    existing = [_ann("wr", "WR 136", _WR136_RA, _WR136_DEC)]
    window = _bare_window(existing)
    results = [_ann("ngc", "NGC 6888", _NGC6888_RA, _NGC6888_DEC)]

    window._on_additional_catalog_results("ngc", results)

    catalogs_present = {a.catalog for a in window.annotations}
    assert catalogs_present == {"wr", "ngc"}


def test_the_reverse_order_also_keeps_both_objects():
    """NGC already loaded, WR toggled on second -- same bug, opposite order."""
    existing = [_ann("ngc", "NGC 6888", _NGC6888_RA, _NGC6888_DEC)]
    window = _bare_window(existing)
    results = [_ann("wr", "WR 136", _WR136_RA, _WR136_DEC)]

    window._on_additional_catalog_results("wr", results)

    catalogs_present = {a.catalog for a in window.annotations}
    assert catalogs_present == {"wr", "ngc"}


def test_same_dedup_class_objects_are_still_deduped_by_proximity():
    """The proximity check itself must still work for catalogs that *are* allowed to
    merge (e.g. Messier + NGC legitimately referencing the same object) -- this fix
    must not turn off dedup entirely, only make it catalog-aware."""
    existing = [_ann("messier", "M42", _NGC6888_RA, _NGC6888_DEC)]
    window = _bare_window(existing)
    results = [_ann("ngc", "NGC 6888", _NGC6888_RA, _NGC6888_DEC)]

    window._on_additional_catalog_results("ngc", results)

    assert len(window.annotations) == 1
    assert window.annotations[0].catalog == "messier"
