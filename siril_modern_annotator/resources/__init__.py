"""Access to bundled resources (currently just the dark theme stylesheet).

In the modular dev tree, this reads theme_dark.qss from disk next to this file. The
single-file bundle produced by build/bundle.py (see ARCHITECTURE.md #Distribution)
replaces this module's source with one that embeds the same file's contents as a string
literal instead, since the bundle's synthetic modules have no real __file__ to read
from disk relative to. Both paths return identical text — the bundler reads the same
theme_dark.qss at build time, so there is nothing to keep manually in sync.
"""

from __future__ import annotations

from pathlib import Path


def load_dark_stylesheet() -> str:
    path = Path(__file__).parent / "theme_dark.qss"
    return path.read_text(encoding="utf-8")
