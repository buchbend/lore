"""The glossary index and `lore glossary` (CONTEXT.md term lookup)."""

from __future__ import annotations

from pathlib import Path

import pytest
from lore_cli.glossary_cmd import app
from lore_cli.hooks import hook_app
from lore_core.glossary import index_text, load, lookup, parse_entries, suggest
from typer.testing import CliRunner

LORE_SHAPE = """\
# Domain

Intro with **emphasis** in a sentence.

**A paragraph lead.** Bold that opens prose is not a term.

## Build

- **Ledger** — the working list of one build run. It holds candidates
  and resume lines.
- **Resume line** — a ledger line with the state.
- **Freshness** (`freshness.py`) — positive evidence only.

### Next

- **Sink** — a destination.
"""

GRILLING_SHAPE = """\
# Ordering

## Language

**Order**:
A request to buy.
_Avoid_: Purchase

**Invoice:**
A request for payment.
"""


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    (root / ".git").mkdir(parents=True)
    return root


def _terms(text: str) -> list[str]:
    return [e.term for e in parse_entries(text, Path("CONTEXT.md"))]


def test_list_items_and_parenthesis_tails_are_terms_and_emphasis_is_not() -> None:
    assert _terms(LORE_SHAPE) == ["Ledger", "Resume line", "Freshness", "Sink"]


def test_grilling_shape_terms_with_colon_inside_or_outside_the_bold() -> None:
    assert _terms(GRILLING_SHAPE) == ["Order", "Invoice"]


def test_an_entry_runs_to_the_next_entry_blank_line_or_heading() -> None:
    entries = {e.term: e for e in parse_entries(LORE_SHAPE + GRILLING_SHAPE, Path("C.md"))}
    assert entries["Ledger"].text.endswith("and resume lines.")
    assert entries["Ledger"].line == 9
    assert entries["Freshness"].text.count("\n") == 0
    assert entries["Order"].text == "**Order**:\nA request to buy.\n_Avoid_: Purchase"


def test_lookup_ignores_case_spacing_parenthesis_and_a_plural() -> None:
    entries = parse_entries(LORE_SHAPE, Path("CONTEXT.md"))
    assert [e.term for e in lookup(entries, "resume  LINE")] == ["Resume line"]
    assert [e.term for e in lookup(entries, "ledgers")] == ["Ledger"]
    assert [e.term for e in lookup(entries, "freshness")] == ["Freshness"]
    assert lookup(entries, "resume") == []


def test_suggest_names_containing_terms_then_near_spellings() -> None:
    entries = parse_entries(LORE_SHAPE, Path("CONTEXT.md"))
    assert suggest(entries, "resume") == ["Resume line"]
    assert suggest(entries, "ledgr") == ["Ledger"]
    assert suggest(entries, "zzz") == []


def test_context_map_links_each_context_and_ignores_links_outside_the_repo(
    repo: Path, tmp_path: Path
) -> None:
    (repo / "src" / "ordering").mkdir(parents=True)
    (repo / "src" / "ordering" / "CONTEXT.md").write_text(GRILLING_SHAPE)
    (tmp_path / "CONTEXT.md").write_text(LORE_SHAPE)
    (repo / "CONTEXT.md").write_text(LORE_SHAPE)  # the map wins over a root file
    (repo / "CONTEXT-MAP.md").write_text(
        "- [Ordering](./src/ordering/CONTEXT.md) — orders\n- [Out](../CONTEXT.md) — outside\n"
    )
    assert [e.term for e in load(repo)] == ["Order", "Invoice"]


def test_index_lists_terms_within_the_budget_and_counts_the_rest(repo: Path) -> None:
    (repo / "CONTEXT.md").write_text(LORE_SHAPE)
    entries = load(repo)
    full = index_text(entries, repo)
    assert full.startswith("## Glossary (CONTEXT.md)\n")
    assert full.endswith("Ledger, Resume line, Freshness, Sink")
    head = len(full) - len("Ledger, Resume line, Freshness, Sink")
    short = index_text(entries, repo, budget=head + len("Ledger, Resume line, "))
    assert short.endswith("Ledger, Resume line, … 2 more: `lore glossary`")
    assert index_text([], repo) is None


def test_cli_prints_each_definition_with_its_location(repo: Path) -> None:
    (repo / "CONTEXT.md").write_text(LORE_SHAPE)
    result = CliRunner().invoke(app, ["Ledger", "sink", "--cwd", str(repo)])
    assert result.exit_code == 0, result.output
    assert "CONTEXT.md:9\n- **Ledger** — the working list" in result.output
    assert "CONTEXT.md:16\n- **Sink** — a destination." in result.output


def test_cli_unknown_term_exits_1_and_names_close_terms(repo: Path) -> None:
    (repo / "CONTEXT.md").write_text(LORE_SHAPE)
    result = CliRunner().invoke(app, ["resume", "Sink", "--cwd", str(repo)])
    assert result.exit_code == 1
    assert "no term 'resume' in the glossary; close: Resume line" in result.output
    assert "- **Sink**" in result.output


def test_cli_without_terms_prints_the_index_and_without_glossary_exits_1(repo: Path) -> None:
    assert CliRunner().invoke(app, ["--cwd", str(repo)]).exit_code == 1
    (repo / "CONTEXT.md").write_text(LORE_SHAPE)
    result = CliRunner().invoke(app, ["--cwd", str(repo / ".git")])
    assert result.exit_code == 0
    assert "Ledger, Resume line, Freshness, Sink" in result.output


def test_session_start_injects_the_index_last(
    repo: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "vault" / "wiki").mkdir(parents=True)
    monkeypatch.setenv("LORE_ROOT", str(tmp_path / "vault"))
    (repo / "CONTEXT.md").write_text(LORE_SHAPE)
    (repo / ".git" / "lore-ledger.md").write_text(
        "- [resume] 2026-09-29T10:00:00Z — round 2 merged; next: wire the hook\n"
    )
    out = (
        CliRunner()
        .invoke(hook_app, ["session-start", "--cwd", str(repo), "--plain", "--probe"], input="")
        .output
    )
    assert out.rstrip().endswith("Ledger, Resume line, Freshness, Sink")
    assert out.index("round 2 merged") < out.index("## Glossary")


def test_session_start_without_glossary_names_none(
    repo: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "vault" / "wiki").mkdir(parents=True)
    monkeypatch.setenv("LORE_ROOT", str(tmp_path / "vault"))
    out = (
        CliRunner()
        .invoke(hook_app, ["session-start", "--cwd", str(repo), "--plain", "--probe"], input="")
        .output
    )
    assert "Glossary" not in out
