"""build/bundle.py's two readability transforms -- _condense_docstrings and
_render_module_sources -- added after a real Siril scripts maintainer rejected an MR
submission of the bundled script as unreviewable (a 100KB file with lines up to
269,410 characters, and "vast amounts of AI-gen comment blocks"). See bundle.py's own
module docstring for the full rationale.

Real bug caught by hand-testing while building this, now guarded here permanently:
the first version of _render_module_sources used a plain (non-raw) '''...''' wrapper,
which still processes backslash escapes in its body -- embedding a module's source
verbatim inside one silently reinterpreted any \\n, \\t, \\\\, etc. already present as
literal text in that source (e.g. inside a regex pattern). test_render_module_sources_
preserves_backslash_escapes_byte_for_byte below is the regression test for exactly
that."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from bundle import (  # noqa: E402
    _condense_docstring_span,
    _condense_docstrings,
    _render_module_sources,
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
    """Regression guard for the line-splice logic in _condense_docstrings: replacing a
    multi-line docstring span must keep whatever text originally followed it on the
    docstring's own last line (normally just a newline, but this must not silently
    swallow anything else there)."""
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


# ----------------------------------------------------------- _render_module_sources


def _round_trip(modules: dict[str, str]) -> dict[str, str]:
    rendered = _render_module_sources(modules)
    ns: dict = {}
    exec("_MODULE_SOURCES = " + rendered, ns)
    return ns["_MODULE_SOURCES"]


def test_render_module_sources_uses_a_real_multiline_string_not_an_escaped_one_liner():
    modules = {"m": "def f():\n    return 1\n"}
    rendered = _render_module_sources(modules)
    # The whole point: this must be a *real* multi-line literal, not one physical line
    # with every newline escaped as a literal backslash-n.
    assert rendered.count("\n") >= 3
    assert "\\n" not in rendered.split("r'''", 1)[1].split("'''", 1)[0]


def test_render_module_sources_round_trips_module_content_exactly():
    modules = {
        "plain": "def f():\n    return 1\n",
        "with_docstring": '"""A module.\n\nMore detail.\n"""\n\nx = 1\n',
    }
    assert _round_trip(modules) == modules


def test_render_module_sources_falls_back_to_repr_for_a_module_containing_triple_quotes():
    modules = {"tricky": "x = '''a literal triple-quoted string appears here'''\n"}
    rendered = _render_module_sources(modules)
    assert "r'''" not in rendered  # did not attempt the raw-triple-quote path
    assert _round_trip(modules) == modules  # but content still round-trips correctly


def test_render_module_sources_falls_back_to_repr_for_a_module_ending_in_a_backslash():
    modules = {"tricky": "x = 1\\"}
    rendered = _render_module_sources(modules)
    assert "r'''" not in rendered
    assert _round_trip(modules) == modules


def test_render_module_sources_preserves_backslash_escapes_byte_for_byte():
    """The real bug: a plain (non-raw) '''...''' wrapper would silently turn a literal
    two-character \\n (backslash, n) already present in the source -- e.g. inside a
    regex pattern -- into an actual newline character. Confirmed live against this
    project's own _MESSIER_DESC_RE (r"=\\s*M\\s*(\\d+)\\b") before this test was
    written: loaded through a bundle built with the old non-raw wrapper, that pattern
    still matched by chance (undefined escapes like \\s/\\d survive, just with a
    SyntaxWarning), but the failure mode this guards is real for any escape Python
    *does* recognize (\\n, \\t, \\\\, ...), which would be silently corrupted instead."""
    modules = {"m": 'PATTERN = r"=\\s*M\\s*(\\d+)\\b"\n'}
    result = _round_trip(modules)
    assert result == modules
    # Belt and suspenders: actually compile+exec the round-tripped module source and
    # confirm the resulting regex still matches real text the same way the original
    # would -- not just that the source text round-tripped unchanged.
    import re

    ns: dict = {}
    exec(compile(result["m"], "<m>", "exec"), ns)
    pattern = re.compile(ns["PATTERN"])
    match = pattern.search("!!! theta1 Ori and the great neb; = M42")
    assert match is not None
    assert match.group(1) == "42"
