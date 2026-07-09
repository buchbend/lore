"""Multi-language code-map symbols via tree-sitter tag queries (#166).

``lore_core.codemap.languages`` is optional: it activates only when the
``lore[codemap]`` extra (tree-sitter + tree-sitter-language-pack) is
installed. These tests cover both paths:

1. WITH the extra: per-language golden fixtures (JS, TS, Vue, Rust, Julia,
   HTML) extract the expected symbols, and ``build_code_map`` folds them
   into the same ranked table as Python symbols.
2. WITHOUT the extra (simulated via monkeypatch): the base generator still
   produces inventory + Python symbols, with no crash or import error.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
from lore_core import codemap as cm
from lore_core.codemap import languages

FIXTURES = Path(__file__).parent / "fixtures" / "codemap_languages"

needs_tree_sitter = pytest.mark.skipif(
    not languages.AVAILABLE, reason="lore[codemap] extra (tree-sitter) not installed"
)


def _names(symbols: list[cm.Symbol]) -> set[tuple[str, str]]:
    return {(s.name, s.kind) for s in symbols}


@needs_tree_sitter
def test_javascript_fixture_extracts_functions_and_class():
    source = (FIXTURES / "sample.js").read_text(encoding="utf-8")
    symbols = languages.extract_symbols("sample.js", source, "javascript")
    assert _names(symbols) == {
        ("greet", "function"),
        ("helper", "function"),
        ("Widget", "class"),
        ("render", "method"),
    }


@needs_tree_sitter
def test_typescript_fixture_extracts_interface_function_class():
    source = (FIXTURES / "sample.ts").read_text(encoding="utf-8")
    symbols = languages.extract_symbols("sample.ts", source, "typescript")
    assert _names(symbols) == {
        ("Shape", "interface"),
        ("makeShape", "function"),
        ("Circle", "class"),
        ("area", "method"),
    }


@needs_tree_sitter
def test_vue_fixture_delegates_script_block_to_typescript_query():
    source = (FIXTURES / "sample.vue").read_text(encoding="utf-8")
    symbols = languages.extract_symbols("sample.vue", source, "vue")
    assert _names(symbols) == {
        ("label", "function"),
        ("helper", "function"),
    }
    # Line numbers are offset past the <template> block, into <script setup>.
    lines = {s.name: s.lineno for s in symbols}
    assert source.splitlines()[lines["label"] - 1].strip().startswith("function label")
    assert source.splitlines()[lines["helper"] - 1].strip().startswith("function helper")


@needs_tree_sitter
def test_rust_fixture_extracts_struct_trait_function():
    source = (FIXTURES / "sample.rs").read_text(encoding="utf-8")
    symbols = languages.extract_symbols("sample.rs", source, "rust")
    assert _names(symbols) == {
        ("Point", "struct"),
        ("Shape", "trait"),
        ("make_point", "function"),
    }


@needs_tree_sitter
def test_julia_fixture_extracts_function_struct_module():
    source = (FIXTURES / "sample.jl").read_text(encoding="utf-8")
    symbols = languages.extract_symbols("sample.jl", source, "julia")
    assert _names(symbols) == {
        ("double", "function"),
        ("Pair", "struct"),
        ("Helpers", "module"),
    }


@needs_tree_sitter
def test_html_fixture_extracts_elements_with_id():
    source = (FIXTURES / "sample.html").read_text(encoding="utf-8")
    symbols = languages.extract_symbols("sample.html", source, "html")
    assert _names(symbols) == {
        ("app", "element"),
        ("footer", "element"),
    }


@needs_tree_sitter
def test_unsupported_language_returns_empty():
    assert languages.extract_symbols("x.foo", "whatever", "made-up-language") == []


def _git(root: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(root), *args], check=True, capture_output=True)


@needs_tree_sitter
def test_build_code_map_joins_multilang_symbols_into_one_ranked_table(tmp_path):
    _git(tmp_path, "init", "-q")
    _git(tmp_path, "config", "user.email", "t@t")
    _git(tmp_path, "config", "user.name", "t")

    (tmp_path / "core.py").write_text("def helper():\n    return 1\n", encoding="utf-8")
    (tmp_path / "sample.rs").write_text(
        (FIXTURES / "sample.rs").read_text(encoding="utf-8"), encoding="utf-8"
    )
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "commit", "-q", "-m", "x")

    code_map = cm.build_code_map(tmp_path)
    qualnames = {s.qualname for s in code_map.symbols}
    # Python and Rust symbols land in the SAME list, ranked together.
    assert "helper" in qualnames
    assert "make_point" in qualnames
    assert "Point" in qualnames


def test_degradation_without_tree_sitter_extra(monkeypatch, tmp_path):
    """Simulate the extra not being installed: base path must not crash."""
    monkeypatch.setattr(languages, "AVAILABLE", False)

    _git(tmp_path, "init", "-q")
    _git(tmp_path, "config", "user.email", "t@t")
    _git(tmp_path, "config", "user.name", "t")
    (tmp_path / "core.py").write_text("def helper():\n    return 1\n", encoding="utf-8")
    (tmp_path / "sample.rs").write_text(
        (FIXTURES / "sample.rs").read_text(encoding="utf-8"), encoding="utf-8"
    )
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "commit", "-q", "-m", "x")

    code_map = cm.build_code_map(tmp_path)
    qualnames = {s.qualname for s in code_map.symbols}
    assert "helper" in qualnames
    assert "make_point" not in qualnames
    assert code_map.inventory.total_files == 2
