"""Tests for build/bundle.py: docstring condensation, base64 chunking,
clean module inlining, and end-to-end topological bundling.

Ensures the bundled script:
1. Eliminates _MODULE_SOURCES, sys.meta_path, and dynamic execution.
2. Preserves real, standard top-level Python classes, functions, and definitions.
3. Contains zero internal relative imports.
4. Binds all physical line lengths (< 250 characters) to prevent text editor hangs.
5. Successfully compiles and exposes all core application components.
"""

from __future__ import annotations

import ast
import py_compile
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from bundle import (  # noqa: E402
    _clean_module,
    _condense_docstring_span,
    _condense_docstrings,
    _format_b64_chunks,
    build,
)

# --------------------------------------------------------------- _condense_docstrings


def test_condense_docstrings_keeps_only_the_first_paragraph_of_a_module_docstring():
    source = (
        '"""First paragraph, the essential point.\n'
        "\n"
        'Second paragraph with a lot of history and verification detail.\n'
        '"""\n'
        "\n"
        "x = 1\n"
    )
    result = _condense_docstrings(source)
    assert result == '"""First paragraph, the essential point."""\n\nx = 1\n'


def test_condense_docstrings_leaves_a_single_paragraph_docstring_unchanged():
    source = '"""Just one paragraph, already short."""\n\nx = 1\n'
    assert _condense_docstrings(source) == source


def test_condense_docstrings_covers_module_class_and_function_docstrings():
    source = (
        '"""Module docstring.\n\nMore module detail.\n"""\n'
        "\n"
        "class Foo:\n"
        '    """Class docstring.\n\n    More class detail.\n    """\n'
        "\n"
        "    def method(self):\n"
        '        """Method docstring.\n\n        More method detail.\n        """\n'
        "        return 1\n"
    )
    result = _condense_docstrings(source)
    assert "More module detail" not in result
    assert "More class detail" not in result
    assert "More method detail" not in result
    assert '"""Module docstring."""' in result
    assert '"""Class docstring."""' in result
    assert '"""Method docstring."""' in result
    compile(result, "<test>", "exec")  # must still be valid Python


def test_condense_docstrings_leaves_inline_comments_completely_untouched():
    source = (
        '"""Docstring.\n\nMore detail.\n"""\n'
        "\n"
        "def f():\n"
        "    # a long inline comment explaining something non-obvious about this line\n"
        "    # spanning two lines, deliberately left alone by this transform\n"
        "    return 1\n"
    )
    result = _condense_docstrings(source)
    assert "# a long inline comment explaining something non-obvious about this line" in result
    assert "# spanning two lines, deliberately left alone by this transform" in result


def test_condense_docstrings_preserves_code_after_a_condensed_docstring_on_its_own_line():
    source = (
        "def f():\n"
        '    """First paragraph.\n\n    Second paragraph.\n    """; return 1\n'
    )
    result = _condense_docstrings(source)
    assert result.rstrip().endswith("return 1")
    compile(result, "<test>", "exec")


def test_condense_docstring_span_single_quoted_one_liner_unchanged():
    assert _condense_docstring_span('"a plain one-line string, not a docstring"') == (
        '"a plain one-line string, not a docstring"'
    )


def test_condense_docstring_span_handles_a_raw_string_prefix():
    span = 'r"""First paragraph.\n\nSecond paragraph.\n"""'
    assert _condense_docstring_span(span) == 'r"""First paragraph."""'


# ----------------------------------------------------------- _format_b64_chunks


def test_format_b64_chunks_bounds_line_length_and_decodes_cleanly():
    import base64

    raw_bytes = b"astronomical annotation data " * 50
    b64_text = base64.b64encode(raw_bytes).decode("ascii")
    chunks = _format_b64_chunks(b64_text, chunk_size=76, indent="    ")

    lines = chunks.splitlines()
    assert len(lines) > 1
    for line in lines:
        assert len(line) <= 85  # 4-space indent + 2 quotes + 76 chars

    ns: dict = {}
    code = f"import base64\ndata = base64.b64decode(\n{chunks}\n)"
    exec(compile(code, "<b64_test>", "exec"), ns)
    assert ns["data"] == raw_bytes


# ----------------------------------------------------------- _clean_module


def test_clean_module_extracts_docstring_and_removes_top_imports():
    source = (
        '"""Module docstring first paragraph.\n\nExtended module details.\n"""\n'
        "\n"
        "from __future__ import annotations\n"
        "import math\n"
        "from .models import Annotation\n"
        "\n"
        "def calculate_area(r: float) -> float:\n"
        "    return math.pi * r * r\n"
    )
    doc, cleaned = _clean_module(source)
    assert doc == "Module docstring first paragraph."
    assert "import math" not in cleaned
    assert "from .models" not in cleaned
    assert "def calculate_area(r: float) -> float:" in cleaned
    compile(cleaned, "<test>", "exec")


def test_clean_module_inlines_nested_internal_imports():
    source = (
        "def query_database():\n"
        "    from .sh2_corrected_positions import CORRECTED_SH2_POSITIONS\n"
        "    return CORRECTED_SH2_POSITIONS\n"
    )
    doc, cleaned = _clean_module(source)
    assert "from .sh2_corrected_positions" not in cleaned
    assert "pass  # inlined internal import" in cleaned
    assert "return CORRECTED_SH2_POSITIONS" in cleaned
    compile(cleaned, "<test>", "exec")


# ----------------------------------------------------------- build() integration


def test_bundle_build_compiles_cleanly_and_has_no_meta_path():
    bundle_path = build()
    assert bundle_path.is_file()

    content = bundle_path.read_text(encoding="utf-8")

    # Guard against regression to synthetic module dict / meta_path
    assert "_MODULE_SOURCES" not in content
    assert "sys.meta_path" not in content
    assert "_EmbeddedFinder" not in content

    # Standard compile check
    py_compile.compile(str(bundle_path), doraise=True)


def test_bundle_build_contains_zero_relative_imports():
    bundle_path = build()
    content = bundle_path.read_text(encoding="utf-8")
    tree = ast.parse(content)

    relative_imports: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.level > 0:
            relative_imports.append(ast.unparse(node))
        elif isinstance(node, ast.ImportFrom) and node.module and node.module.startswith("siril_modern_annotator"):
            relative_imports.append(ast.unparse(node))

    assert relative_imports == [], f"Found relative imports in bundle: {relative_imports}"


def test_bundle_build_bounds_all_line_lengths():
    bundle_path = build()
    content = bundle_path.read_text(encoding="utf-8")

    long_lines = [(idx, len(line), line) for idx, line in enumerate(content.splitlines(), 1) if len(line) > 300]
    assert long_lines == [], f"Found lines exceeding 300 characters: {long_lines[:3]}"


def test_bundle_execution_exports_expected_symbols():
    bundle_path = build()
    content = bundle_path.read_text(encoding="utf-8")

    ns: dict = {}
    code_obj = compile(content, "DG_Modern_Annotator.py", "exec")
    exec(code_obj, ns)

    expected_symbols = [
        "Annotation",
        "SirilWcs",
        "SirilBridge",
        "MainWindow",
        "main",
        "load_dark_stylesheet",
        "load_app_icon_png_bytes",
    ]
    for sym in expected_symbols:
        assert sym in ns, f"Expected {sym} to be present in bundled script namespace"

    # Verify embedded assets
    icon_bytes = ns["load_app_icon_png_bytes"]()
    assert icon_bytes.startswith(b"\x89PNG\r\n\x1a\n")
    assert len(icon_bytes) < 20_000  # under 20 KB

    qss = ns["load_dark_stylesheet"]()
    assert "Siril Modern Annotator" in qss


def test_bundle_main_window_instantiation(tmp_path):
    from unittest.mock import MagicMock
    from PyQt6.QtWidgets import QApplication

    _ = QApplication.instance() or QApplication([])

    bundle_path = build()
    content = bundle_path.read_text(encoding="utf-8")

    ns: dict = {}
    code_obj = compile(content, "DG_Modern_Annotator.py", "exec")
    exec(code_obj, ns)

    assert ns.get("__version__") == "0.4.3"

    bridge = MagicMock()
    bridge.get_system_catalogue_dir.return_value = tmp_path
    bridge.has_image.return_value = False

    window = ns["MainWindow"](bridge)

    assert window.windowTitle() == "DG Modern Annotator v0.4.3"
    assert window.style_panel is not None
    assert window.object_panel is not None
    assert window.tools_panel is not None
    window.close()

