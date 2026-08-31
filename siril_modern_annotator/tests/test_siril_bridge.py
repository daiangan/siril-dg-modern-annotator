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


class _FakeKeywords:
    """Mimics sirilpy's FKeywords -- only the attributes actually set are present,
    same as a real dataclass instance where unset fields commonly default to None
    (or simply don't exist under an unconfirmed name, on an older sirilpy)."""

    def __init__(self, **attrs):
        for name, value in attrs.items():
            setattr(self, name, value)


class _FakeSirilWithKeywords:
    def __init__(self, keywords):
        self._keywords = keywords

    def get_image_keywords(self):
        return self._keywords


def test_get_technical_metadata_reads_confirmed_fields():
    keywords = _FakeKeywords(instrume="ZWO ASI2600MM", telescop="RC8", focal_length=1600.0, date_obs="2026-01-01")
    bridge = SirilBridge()
    bridge._siril = _FakeSirilWithKeywords(keywords)
    metadata = bridge.get_technical_metadata()
    assert metadata["Camera"] == "ZWO ASI2600MM"
    assert metadata["Telescope"] == "RC8"
    assert metadata["Focal Length"] == "1600 mm"
    assert metadata["Date"] == "2026-01-01"


def test_get_technical_metadata_rounds_focal_length_to_whole_number():
    """Per user request: Focal Length must never show decimal points, e.g. a DWARF
    3's 148.012mm reads as "148 mm", not "148.012 mm"."""
    keywords = _FakeKeywords(focal_length=148.012)
    bridge = SirilBridge()
    bridge._siril = _FakeSirilWithKeywords(keywords)
    assert bridge.get_technical_metadata()["Focal Length"] == "148 mm"


def test_get_technical_metadata_formats_pixel_size_to_one_decimal():
    keywords = _FakeKeywords(pixel_size_x=2.0)
    bridge = SirilBridge()
    bridge._siril = _FakeSirilWithKeywords(keywords)
    assert bridge.get_technical_metadata()["Pixel Size"] == "2.0 µm"


def test_get_technical_metadata_formats_exposure_under_a_minute_as_seconds():
    keywords = _FakeKeywords(exptime=30.0)
    bridge = SirilBridge()
    bridge._siril = _FakeSirilWithKeywords(keywords)
    assert bridge.get_technical_metadata()["Exposure"] == "30 s"


def test_get_technical_metadata_formats_exposure_under_an_hour_as_minutes():
    keywords = _FakeKeywords(exptime=45 * 60.0)
    bridge = SirilBridge()
    bridge._siril = _FakeSirilWithKeywords(keywords)
    assert bridge.get_technical_metadata()["Exposure"] == "45 m"


def test_get_technical_metadata_formats_exposure_an_hour_or_more_as_hours():
    """6510 s (a real DWARF 3 session total) is 1.8083... hours, rounded to one
    decimal place per user request."""
    keywords = _FakeKeywords(exptime=6510.0)
    bridge = SirilBridge()
    bridge._siril = _FakeSirilWithKeywords(keywords)
    assert bridge.get_technical_metadata()["Exposure"] == "1.8 h"


def test_get_technical_metadata_omits_fields_the_keywords_object_does_not_have():
    """filter/gain/exptime are unconfirmed attribute names -- a sirilpy version (or
    mock) without them must simply omit those lines, not raise."""
    keywords = _FakeKeywords(instrume="TestCam")
    bridge = SirilBridge()
    bridge._siril = _FakeSirilWithKeywords(keywords)
    metadata = bridge.get_technical_metadata()
    assert metadata == {"Camera": "TestCam"}


def test_get_technical_metadata_omits_none_and_empty_and_zero_values():
    keywords = _FakeKeywords(instrume="TestCam", telescop=None, filter="", gain=0)
    bridge = SirilBridge()
    bridge._siril = _FakeSirilWithKeywords(keywords)
    metadata = bridge.get_technical_metadata()
    assert metadata == {"Camera": "TestCam"}


def test_get_technical_metadata_empty_when_keywords_is_none():
    bridge = SirilBridge()
    bridge._siril = _FakeSirilWithKeywords(None)
    assert bridge.get_technical_metadata() == {}


def test_get_technical_metadata_swallows_missing_method_and_other_exceptions():
    class _SirilWithoutKeywordsMethod:
        pass

    class _BrokenSiril:
        def get_image_keywords(self):
            raise RuntimeError("some unexpected sirilpy-internal failure")

    bridge = SirilBridge()
    bridge._siril = _SirilWithoutKeywordsMethod()
    assert bridge.get_technical_metadata() == {}

    bridge._siril = _BrokenSiril()
    assert bridge.get_technical_metadata() == {}
