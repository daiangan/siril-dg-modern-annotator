"""Access to bundled resources: the dark theme stylesheet and the app icon.

In the modular dev tree, these read theme_dark.qss / icon.png from disk next to this
file. The single-file bundle produced by build/bundle.py (see ARCHITECTURE.md
#Distribution) replaces this module's source with one that embeds the same files'
contents as literals instead (text for the stylesheet, base64 for the PNG), since the
bundle's synthetic modules have no real __file__ to read from disk relative to. Both
paths return identical bytes/text — the bundler reads these same files at build time,
so there is nothing to keep manually in sync.
"""

from __future__ import annotations

from pathlib import Path


def load_dark_stylesheet() -> str:
    path = Path(__file__).parent / "theme_dark.qss"
    return path.read_text(encoding="utf-8")


def load_app_icon_png_bytes() -> bytes:
    path = Path(__file__).parent / "icon.png"
    return path.read_bytes()
