"""MainWindow._defer_item_cleanup/_flush_pending_item_cleanup -- the crash-mitigation
mechanism that keeps just-removed QGraphicsItems alive for a short delay so a later
repaint can't touch an already-destroyed C++ object (see that method's own docstring
for the two real native crash reports this exists to prevent).

Regression test for a real crash report: toggling two catalogs off in quick succession
(WR then NGC) followed by re-toggling one back on crashed with a SIGBUS inside
QGraphicsView::paintEvent. Root cause was _pending_item_cleanup being one flat, shared
list -- the *first* batch's 250ms timer unconditionally cleared the *entire* list when
it fired, wiping out a second batch's protection too if the two calls landed close
together, leaving those items with far less than their intended 250ms of protection
before a later repaint could use-after-free them.

No Qt/scene dependency to exercise here -- these two methods only do plain list
bookkeeping plus scheduling a QTimer (never let it actually fire in these tests; the
bug and its fix live entirely in what _flush_pending_item_cleanup does when *given* a
batch, called directly rather than waiting out the real 250ms), so this is tested
against a bare MainWindow instance built via MainWindow.__new__ (skipping __init__,
which needs a live sirilpy bridge) with just the one attribute these methods touch."""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication

from siril_modern_annotator.gui.main_window import MainWindow

_app = QApplication.instance() or QApplication([])


def _bare_window() -> MainWindow:
    # object.__new__ isn't valid for a SIP-wrapped QMainWindow subclass -- goes
    # through MainWindow.__new__ instead, still skipping __init__ (and the live
    # sirilpy bridge it needs) entirely.
    window = MainWindow.__new__(MainWindow)
    window._pending_item_cleanup = []
    return window


def test_defer_item_cleanup_ignores_an_empty_batch():
    window = _bare_window()
    window._defer_item_cleanup([])
    assert window._pending_item_cleanup == []


def test_defer_item_cleanup_queues_a_batch():
    window = _bare_window()
    batch = [object(), object()]
    window._defer_item_cleanup(batch)
    assert window._pending_item_cleanup == [batch]


def test_flushing_one_batch_does_not_clear_a_later_batch():
    """The actual regression: with the old shared-list/plain-.clear() implementation,
    this would leave _pending_item_cleanup empty (batch_b wiped out early). The fix
    must leave batch_b's items alive, since its own 250ms timer hasn't fired yet."""
    window = _bare_window()
    batch_a = [object(), object()]
    batch_b = [object()]
    window._defer_item_cleanup(batch_a)
    window._defer_item_cleanup(batch_b)
    assert window._pending_item_cleanup == [batch_a, batch_b]

    window._flush_pending_item_cleanup(batch_a)  # batch_a's own timer firing first
    assert window._pending_item_cleanup == [batch_b]
    assert batch_b[0] is not None  # still a live, held reference


def test_flushing_the_second_batch_leaves_the_first_untouched():
    window = _bare_window()
    batch_a = [object()]
    batch_b = [object()]
    window._defer_item_cleanup(batch_a)
    window._defer_item_cleanup(batch_b)

    window._flush_pending_item_cleanup(batch_b)
    assert window._pending_item_cleanup == [batch_a]


def test_flushing_every_queued_batch_empties_the_list():
    window = _bare_window()
    batch_a = [object()]
    batch_b = [object()]
    window._defer_item_cleanup(batch_a)
    window._defer_item_cleanup(batch_b)

    window._flush_pending_item_cleanup(batch_a)
    window._flush_pending_item_cleanup(batch_b)
    assert window._pending_item_cleanup == []


def test_flushing_an_already_removed_batch_is_a_harmless_no_op():
    window = _bare_window()
    batch = [object()]
    window._defer_item_cleanup(batch)
    window._flush_pending_item_cleanup(batch)
    # A stray/duplicate flush (e.g. two singleShot callbacks somehow both firing for
    # the same batch) must not raise.
    window._flush_pending_item_cleanup(batch)
    assert window._pending_item_cleanup == []
