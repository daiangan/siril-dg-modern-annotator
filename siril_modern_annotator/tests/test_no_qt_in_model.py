"""Import-lint guard (ARCHITECTURE.md #5, #9): the annotation model and the export
renderer must stay importable and testable without PyQt6, so neither package may import
it. This is what lets annotation/ and export/ be unit tested headlessly and guarantees
the export renderer can never accidentally depend on the interactive Qt canvas.
"""

from __future__ import annotations

import ast
from pathlib import Path

_PACKAGE_ROOT = Path(__file__).resolve().parent.parent
_FORBIDDEN_PACKAGES = ("PyQt6",)
_CHECKED_SUBPACKAGES = ("annotation", "export")


def _imported_top_level_names(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                names.add(alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                names.add(node.module.split(".")[0])
    return names


def test_annotation_and_export_packages_never_import_qt():
    offenders = []
    for subpackage in _CHECKED_SUBPACKAGES:
        for path in sorted((_PACKAGE_ROOT / subpackage).rglob("*.py")):
            imported = _imported_top_level_names(path)
            for forbidden in _FORBIDDEN_PACKAGES:
                if forbidden in imported:
                    offenders.append(f"{path.relative_to(_PACKAGE_ROOT)} imports {forbidden}")
    assert not offenders, "Qt import(s) found in Qt-free packages:\n" + "\n".join(offenders)
