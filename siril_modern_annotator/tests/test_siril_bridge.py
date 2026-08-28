"""SirilBridge.log(): writes to Siril's own log/console panel, used for the startup
credit banner (modern_annotator.py) -- distinct from this script's own Python logger,
which the user won't see unless Siril was launched from a terminal.

SirilBridge.get_loaded_image_filename(): a best-effort accessor for the actual on-disk
filename of the loaded image, used to name exports after "the original image name" per
user request -- RESEARCH.md has no confirmed sirilpy accessor for this, so unlike this
class's other wrapper methods, it deliberately swallows any failure and returns None
instead of raising, so a wrong guess at the underlying sirilpy method name degrades to
the pre-existing OBJECT-keyword-based naming rather than breaking image loading."""

from __future__ import annotations

import pytest

from siril_modern_annotator.siril_bridge.interface import SirilBridge, SirilBridgeError


class _FakeSiril:
    def __init__(self, filename: str | None = None):
        self.logged: list[str] = []
        self._filename = filename

    def log(self, message: str) -> None:
        self.logged.append(message)

    def get_image_filename(self) -> str | None:
        return self._filename


def test_log_delegates_to_the_underlying_siril_interface():
    bridge = SirilBridge()
    bridge._siril = _FakeSiril()
    bridge.log("hello")
    bridge.log("world")
    assert bridge._siril.logged == ["hello", "world"]


def test_log_before_connect_raises_not_silently_no_ops():
    bridge = SirilBridge()
    with pytest.raises(SirilBridgeError):
        bridge.log("too early")


def test_get_loaded_image_filename_returns_the_underlying_value():
    bridge = SirilBridge()
    bridge._siril = _FakeSiril(filename="M31_stacked.fits")
    assert bridge.get_loaded_image_filename() == "M31_stacked.fits"


def test_get_loaded_image_filename_returns_none_for_empty_string():
    bridge = SirilBridge()
    bridge._siril = _FakeSiril(filename="")
    assert bridge.get_loaded_image_filename() is None


def test_get_loaded_image_filename_swallows_missing_method():
    """If this sirilpy version doesn't expose get_image_filename() at all (a wrong
    guess at the method name, or an older sirilpy), this must return None instead of
    raising AttributeError -- unlike every other SirilBridge method, which is a
    confirmed, documented sirilpy call and is allowed to propagate failures."""

    class _SirilWithoutFilenameMethod:
        pass

    bridge = SirilBridge()
    bridge._siril = _SirilWithoutFilenameMethod()
    assert bridge.get_loaded_image_filename() is None


def test_get_loaded_image_filename_swallows_any_other_exception():
    class _BrokenSiril:
        def get_image_filename(self):
            raise RuntimeError("some unexpected sirilpy-internal failure")

    bridge = SirilBridge()
    bridge._siril = _BrokenSiril()
    assert bridge.get_loaded_image_filename() is None
