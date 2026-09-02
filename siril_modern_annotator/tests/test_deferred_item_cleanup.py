"""MainWindow._defer_item_cleanup/_flush_pending_item_cleanup -- the crash-mitigation
mechanism that keeps just-removed QGraphicsItems alive so a later repaint can't touch
an already-destroyed C++ object (see that method's own docstring for the real native
crash reports this exists to prevent, including why batches are no longer auto-flushed
on a timer at all -- _flush_pending_item_cleanup now exists only for a call site that
can independently prove no repaint is still pending).

Regression test for a real crash report: toggling two catalogs off in quick succession
(WR then NGC) followed by re-toggling one back on crashed with a SIGBUS inside
QGraphicsView::paintEvent. Root cause (from back when batches *were* auto-flushed after
a fixed 250ms) was _pending_item_cleanup being one flat, shared list -- the *first*
batch's timer unconditionally cleared the *entire* list when it fired, wiping out a
second batch's protection too if the two calls landed close together. That timer-based
auto-flush is gone now (a fixed delay was never provably safe against a backed-up event
queue -- see _defer_item_cleanup's docstring for the later crash reports that showed
250ms wasn't enough either), but the batch-isolation this test guards -- one batch's
flush must never affect another's -- still matters for _flush_pending_item_cleanup's
one remaining, deliberate use.

No Qt/scene dependency to exercise here -- these two methods only do plain list
bookkeeping, so this is tested against a bare MainWindow instance built via
MainWindow.__new__ (skipping __init__, which needs a live sirilpy bridge) with just the
one attribute these methods touch."""

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


def test_defer_item_cleanup_schedules_no_auto_flush_timer(monkeypatch):
    """The removed behavior, made explicit: a batch must sit in
    _pending_item_cleanup indefinitely, not get scheduled for release via
    QTimer.singleShot -- see _defer_item_cleanup's own docstring for why a fixed-delay
    auto-flush was removed (it wasn't provably safe against a backed-up event queue,
    confirmed by real crash reports recurring even with the delay in place)."""
    from PyQt6.QtCore import QTimer

    def _fail_if_called(*args, **kwargs):
        raise AssertionError("_defer_item_cleanup must not schedule a QTimer anymore")

    monkeypatch.setattr(QTimer, "singleShot", _fail_if_called)
    window = _bare_window()
    window._defer_item_cleanup([object()])
    assert len(window._pending_item_cleanup) == 1


def test_flushing_one_batch_does_not_clear_a_later_batch():
    """The actual regression: with the old shared-list/plain-.clear() implementation,
    this would leave _pending_item_cleanup empty (batch_b wiped out early). The fix
    must leave batch_b's items alive -- flushing must never be an all-or-nothing
    operation across unrelated batches."""
    window = _bare_window()
    batch_a = [object(), object()]
    batch_b = [object()]
    window._defer_item_cleanup(batch_a)
    window._defer_item_cleanup(batch_b)
    assert window._pending_item_cleanup == [batch_a, batch_b]

    window._flush_pending_item_cleanup(batch_a)
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
