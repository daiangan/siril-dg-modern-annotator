"""Export font resolution: Pillow's ImageFont.truetype() resolving a bare family name
(rather than an actual file path) is a well-known unreliable operation on Windows --
confirmed by a real report where Bayer-designation Greek letters
(annotation.catalogs.bayer_designation_to_greek, e.g. "ξ Sco") rendered correctly in the
live Qt canvas but showed as a missing-glyph box in the exported image, while the plain-
ASCII part of the same label ("Sco") rendered fine. That split is the signature of
silently landing in Pillow's own minimal built-in default font (very limited Unicode
coverage) instead of the actually-installed font -- not of the requested font itself
lacking the glyph."""

from __future__ import annotations

from pathlib import Path

import pytest
from PIL import ImageFont

from siril_modern_annotator.export import exporter


class _Style:
    def __init__(self, font_family: str, font_size: float = 20.0):
        self.font_family = font_family
        self.font_size = font_size


def test_font_file_candidates_windows(monkeypatch):
    monkeypatch.setattr(exporter.platform, "system", lambda: "Windows")
    candidates = exporter._font_file_candidates("Verdana")
    assert exporter._WINDOWS_FONTS_DIR / "verdana.ttf" in candidates
    assert exporter._WINDOWS_FONTS_DIR / "verdana.ttc" in candidates


def test_font_file_candidates_windows_lowercases_and_strips_spaces(monkeypatch):
    monkeypatch.setattr(exporter.platform, "system", lambda: "Windows")
    candidates = exporter._font_file_candidates("Times New Roman")
    assert exporter._WINDOWS_FONTS_DIR / "timesnewroman.ttf" in candidates


def test_font_file_candidates_macos_keeps_original_casing(monkeypatch):
    monkeypatch.setattr(exporter.platform, "system", lambda: "Darwin")
    candidates = exporter._font_file_candidates("Verdana")
    assert any(c.name == "Verdana.ttf" for c in candidates)
    assert any(str(c).startswith("/System/Library/Fonts") for c in candidates)
    assert any(str(c).startswith("/Library/Fonts") for c in candidates)


def test_font_file_candidates_macos_includes_supplemental_dir(monkeypatch):
    """Regression test: confirmed by direct inspection of a real machine that macOS
    keeps the classic "Microsoft core fonts" (Verdana, Georgia, Times New Roman, ...)
    under /System/Library/Fonts/Supplemental, not the top-level Fonts folder (which
    only has Apple's own system faces) -- an earlier version of this list missed it
    entirely, so the file-path fallback would have failed to find Verdana on macOS."""
    monkeypatch.setattr(exporter.platform, "system", lambda: "Darwin")
    candidates = exporter._font_file_candidates("Verdana")
    assert Path("/System/Library/Fonts/Supplemental/Verdana.ttf") in candidates


def test_font_file_candidates_linux(monkeypatch):
    monkeypatch.setattr(exporter.platform, "system", lambda: "Linux")
    candidates = exporter._font_file_candidates("Verdana")
    assert exporter.Path("/usr/share/fonts/verdana.ttf") in candidates


def test_font_for_style_uses_bare_name_when_it_resolves(monkeypatch):
    calls = []

    def fake_truetype(name, size):
        calls.append(name)
        return "FAKE_FONT"

    monkeypatch.setattr(exporter.ImageFont, "truetype", fake_truetype)
    result = exporter._font_for_style(_Style("Verdana"), scale=1.0)
    assert result == "FAKE_FONT"
    assert calls == ["Verdana"]  # never needed to try a file-path fallback


def test_font_for_style_falls_back_to_file_path_when_bare_name_fails(monkeypatch):
    """Regression test for the actual reported bug: when the bare family name fails to
    resolve, it must try an actual font *file*, not jump straight to Pillow's limited
    built-in default."""
    monkeypatch.setattr(exporter.platform, "system", lambda: "Windows")
    expected_path = str(exporter._WINDOWS_FONTS_DIR / "verdana.ttf")
    calls = []

    def fake_truetype(name_or_path, size):
        calls.append(name_or_path)
        if name_or_path == "Verdana":
            raise OSError("cannot find font (bare name lookup fails on Windows)")
        return f"FONT[{name_or_path}]"

    monkeypatch.setattr(exporter.ImageFont, "truetype", fake_truetype)
    monkeypatch.setattr(Path, "is_file", lambda self: str(self) == expected_path)

    result = exporter._font_for_style(_Style("Verdana"), scale=1.0)
    assert result == f"FONT[{expected_path}]"
    assert calls[0] == "Verdana"
    assert expected_path in calls


def test_font_for_style_falls_back_to_verdana_when_requested_font_is_not_installed_at_all(monkeypatch, caplog):
    """Regression test for a real report that persisted after the file-path fallback
    fix above: a user's already-saved style (persistence/last_used.py saves on every
    edit) can still reference "Inter", this app's *former* default font_family, from
    before the shipped default changed to "Verdana". "Inter" genuinely isn't installed
    on their system at all -- no amount of file-path guessing finds a font that was
    never there -- so this must fall through to the app's own fallback font (Verdana,
    confirmed present on both macOS and Windows) rather than Pillow's limited default,
    the same way Qt's own font substitution already does for the live canvas.

    Logged only at debug level, not warning -- per a real follow-up report, Siril
    captures this script's own stderr and displays it as if it came from Siril itself,
    so a WARNING here showed up looking like a real error even though the fallback
    works correctly. modern_annotator.py configures the root logger at INFO, so DEBUG
    records are silent under normal operation; this test simulates that by only
    checking WARNING+ records are captured."""
    monkeypatch.setattr(exporter.platform, "system", lambda: "Windows")
    verdana_path = str(exporter._WINDOWS_FONTS_DIR / "verdana.ttf")
    calls = []

    def fake_truetype(name_or_path, size):
        calls.append(name_or_path)
        if name_or_path == verdana_path:
            return "REAL_VERDANA_FONT"
        raise OSError(f"'{name_or_path}' is not installed on this system")

    monkeypatch.setattr(exporter.ImageFont, "truetype", fake_truetype)
    monkeypatch.setattr(Path, "is_file", lambda self: str(self) == verdana_path)

    with caplog.at_level("WARNING"):  # matches the app's real INFO-level configuration
        result = exporter._font_for_style(_Style("Inter"), scale=1.0)
    assert result == "REAL_VERDANA_FONT"
    assert caplog.text == "", (
        "font fallback must not log at WARNING+ -- it would surface in Siril's log "
        "looking like a real error even though the fallback works correctly"
    )


def test_font_for_style_falls_back_to_pillow_default_when_nothing_resolves(monkeypatch, caplog):
    """Even this last-resort path (nothing at all could be located, not even the
    Verdana fallback) must stay silent at WARNING+ -- same reasoning as the test
    above: it would surface in Siril's log looking like a real error."""
    real_font = ImageFont.load_default(size=12)  # fetched before any monkeypatching

    monkeypatch.setattr(exporter.platform, "system", lambda: "Windows")
    monkeypatch.setattr(exporter.ImageFont, "truetype", lambda *a, **kw: (_ for _ in ()).throw(OSError("nope")))
    monkeypatch.setattr(Path, "is_file", lambda self: False)
    monkeypatch.setattr(exporter.ImageFont, "load_default", lambda size: real_font)

    with caplog.at_level("WARNING"):  # matches the app's real INFO-level configuration
        result = exporter._font_for_style(_Style("SomeMissingFont"), scale=1.0)
    assert result is real_font
    assert caplog.text == ""


def test_font_for_style_scales_size():
    sizes = []

    class _CapturingImageFont:
        @staticmethod
        def truetype(name, size):
            sizes.append(size)
            raise OSError("force fallback path for this test")

        @staticmethod
        def load_default(size):
            sizes.append(size)
            return ImageFont.load_default(size=size)

    import siril_modern_annotator.export.exporter as exporter_module

    original = exporter_module.ImageFont
    exporter_module.ImageFont = _CapturingImageFont
    try:
        exporter_module._font_for_style(_Style("Verdana", font_size=20.0), scale=2.0)
    finally:
        exporter_module.ImageFont = original
    assert sizes[0] == 40  # 20.0 * 2.0
