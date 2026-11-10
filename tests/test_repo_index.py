"""`lore index` — the index of a repo, in the banner and in AGENTS.md (PRD 0020)."""

from __future__ import annotations

from pathlib import Path

import pytest
from lore_cli.index_cmd import app
from typer.testing import CliRunner


def _prd(num: str, title: str, status: str) -> str:
    return f"---\ntitle: {title}\nstatus: {status}\n---\n\n# {title}\n"


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    (root / ".git").mkdir(parents=True)
    adr = root / "docs" / "adr"
    prd = root / "docs" / "prd"
    adr.mkdir(parents=True)
    prd.mkdir(parents=True)
    (adr / "index.md").write_text("# Architecture Decision Records\n")
    (adr / "0001-one-home.md").write_text("# ADR 0001: One home per fact\n")
    (adr / "0002-the-logbook.md").write_text("# The logbook\n")
    (adr / "0003-federated.md").write_text("# 0003 — Search is federated\n")
    (prd / "0001-old-plan.md").write_text(_prd("0001", "Old plan", "shipped"))
    (prd / "0002-new-plan.md").write_text(_prd("0002", "New plan", "draft"))
    return root


def _index(repo: Path, *args: str) -> CliRunner:
    return CliRunner().invoke(app, ["--cwd", str(repo), *args])


def test_index_names_both_adrs_and_only_the_current_prd(repo: Path) -> None:
    (repo / "CONTEXT.md").write_text("- **Ledger** — the working list.\n")
    (repo / "ROADMAP.md").write_text("# Roadmap\n\n## Now\n\n- x\n\n## Not placed yet\n")
    result = _index(repo)
    assert result.exit_code == 0, result.output
    out = result.output
    assert "ADR 0001: One home per fact" in out
    assert "ADR 0002: The logbook" in out
    assert "ADR 0003: Search is federated" in out
    assert "PRD 0002: New plan" in out
    assert "Old plan" not in out
    assert "Architecture Decision Records" not in out
    assert "Ledger" in out
    assert "- Now\n" in out and "- Not placed yet\n" in out
    assert "`lore glossary <term>`" in out


def test_over_budget_each_group_keeps_its_newest_lines_and_counts_the_rest(repo: Path) -> None:
    adr = repo / "docs" / "adr"
    for n in range(4, 61):
        (adr / f"{n:04d}-decision.md").write_text(f"# ADR {n:04d}: A decision with a long title\n")
    (repo / "CONTEXT.md").write_text("".join(f"- **Term{n}** — a word.\n" for n in range(80)))
    out = _index(repo).output
    assert len(out.rstrip("\n")) <= 2000
    assert "ADR 0060: A decision" in out
    assert "ADR 0001:" not in out
    assert "Term79" in out
    assert "Term0," not in out
    assert out.count(" more\n") == 2
    assert "New plan" in out


def test_a_group_without_content_prints_no_line(tmp_path: Path) -> None:
    root = tmp_path / "bare"
    (root / ".git").mkdir(parents=True)
    (root / "CONTEXT.md").write_text("- **Ledger** — the working list.\n")
    out = _index(root).output
    assert "Ledger" in out
    assert "ADRs" not in out and "PRDs" not in out and "Roadmap" not in out
    (root / "CONTEXT.md").unlink()
    assert _index(root).output == ""


def test_write_appends_the_block_then_replaces_only_the_text_between_the_markers(
    repo: Path,
) -> None:
    agents = repo / "AGENTS.md"
    agents.write_text("# Agents\n\nHand-written rules.\n")
    assert _index(repo, "--write").exit_code == 0
    first = agents.read_text()
    assert first.startswith("# Agents\n\nHand-written rules.\n\n<!-- lore index: start")
    assert "ADR 0002: The logbook" in first
    agents.write_text(first + "\n## Tail\n\nKept.\n")
    (repo / "docs" / "adr" / "0004-new.md").write_text("# ADR 0004: A new decision\n")
    assert _index(repo, "--write").exit_code == 0
    second = agents.read_text()
    assert second.startswith("# Agents\n\nHand-written rules.\n\n<!-- lore index: start")
    assert second.endswith("<!-- lore index: end -->\n\n## Tail\n\nKept.\n")
    assert "ADR 0004: A new decision" in second
    assert second.count("<!-- lore index: start") == 1


def test_check_exits_1_and_names_the_file_when_the_block_is_stale(repo: Path) -> None:
    assert _index(repo, "--check").exit_code == 1
    _index(repo, "--write")
    assert _index(repo, "--check").exit_code == 0
    (repo / "docs" / "prd" / "0003-another.md").write_text(_prd("0003", "Another", "draft"))
    result = _index(repo, "--check")
    assert result.exit_code == 1
    assert str(repo / "AGENTS.md") in result.output


def _session_start(repo: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> str:
    from lore_cli.hooks import hook_app

    (tmp_path / "vault" / "wiki").mkdir(parents=True)
    monkeypatch.setenv("LORE_ROOT", str(tmp_path / "vault"))
    args = ["session-start", "--cwd", str(repo), "--plain", "--probe"]
    return CliRunner().invoke(hook_app, args, input="").output


def test_session_start_injects_the_adr_titles_where_the_glossary_index_sat(
    repo: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (repo / "CONTEXT.md").write_text("- **Ledger** — the working list.\n")
    out = _session_start(repo, tmp_path, monkeypatch)
    assert "ADR 0001: One home per fact" in out
    assert "PRD 0002: New plan" in out
    assert out.rstrip().endswith("Detail: `docs/prd/<number>-*.md`")
    assert "## Glossary" not in out


def test_the_banner_holds_the_filing_trigger_and_reading_rule_and_no_routing_line(
    repo: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from lore_core import session_start

    wiki = tmp_path / "wiki"
    wiki.mkdir()
    monkeypatch.setattr(session_start, "current_repo", lambda _cwd: None)
    block = {"wiki": wiki.name, "scope": wiki.name, "backend": None, "issues": None, "prs": None}
    out = session_start.session_start_from_lore(str(repo), (repo / "CLAUDE.md", block), tmp_path)
    assert out is not None
    assert "File every team-relevant fact" in out
    assert "lore style show filing" in out
    assert "At session end, list every artifact" in out
    assert "Only `Invariant` lines bind" in out
    assert "becomes an issue on the owning repo" not in out
    assert "becomes a comment on it" not in out
    assert "Never merge an ADR, PRD or wiki topic-note PR yourself" in out
    assert "Close only issues you opened" in out


def test_style_show_filing_prints_the_routing_lines() -> None:
    from lore_cli.style_cmd import app as style_app

    result = CliRunner().invoke(style_app, ["show", "filing"])
    assert result.exit_code == 0, result.output
    assert "becomes an issue on the owning repo" in result.output
    assert "run `lore roadmap add`" in result.output
    assert "becomes a comment on it" in result.output
    assert "closed as not planned" in result.output
    assert (
        "An ADR, a PRD or a wiki topic-note edit outside a grilling session is a pull request."
        in result.output
    )


def test_file_issue_reads_the_filing_rules_before_it_drafts() -> None:
    skill = Path(__file__).resolve().parent.parent / "lore-workflow/skills/file-issue/SKILL.md"
    text = skill.read_text(encoding="utf-8")
    step_one = text.split("\n## 1. ", 1)[1].split("\n## 2. ", 1)[0]
    assert "lore style show filing" in step_one


@pytest.mark.parametrize(
    "markers",
    [
        "<!-- lore index: start -->\n",
        "<!-- lore index: end -->\n<!-- lore index: start -->\n",
        "<!-- lore index: start -->\n<!-- lore index: end -->\n" * 2,
    ],
)
def test_write_refuses_unmatched_markers_and_keeps_the_user_text(repo: Path, markers: str) -> None:
    agents = repo / "AGENTS.md"
    text = f"# Agents\n\n{markers}\nHand-written rules.\n"
    agents.write_text(text)
    for _ in range(2):
        result = _index(repo, "--write")
        assert result.exit_code == 1
        assert f"{agents}: unmatched lore index marker" in result.output
    assert agents.read_text() == text
    check = _index(repo, "--check")
    assert check.exit_code == 1
    assert "unmatched lore index marker" in check.output


def test_the_repo_agents_md_block_is_current() -> None:
    """A stale committed block fails the suite: run `lore index --write`."""
    from lore_core import repo_index

    root = Path(__file__).resolve().parent.parent
    assert repo_index.agents_block_is_current(root), "run `lore index --write`"


# --- the banner does not repeat what AGENTS.md already loaded -----------------


def _with_block(repo: Path, *, imported: bool = True) -> None:
    (repo / "CONTEXT.md").write_text("- **Ledger** — the working list.\n")
    assert _index(repo, "--write").exit_code == 0
    if imported:
        (repo / "CLAUDE.md").write_text("@AGENTS.md\n")


def test_session_start_points_to_a_current_agents_block_and_does_not_repeat_it(
    repo: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _with_block(repo)
    out = _session_start(repo, tmp_path, monkeypatch)
    assert "ADR 0001: One home per fact" not in out
    assert "index block of `AGENTS.md`" in out


def test_session_start_injects_the_full_index_and_a_hint_when_the_block_is_stale(
    repo: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _with_block(repo)
    (repo / "docs" / "adr" / "0004-new.md").write_text("# ADR 0004: A new decision\n")
    out = _session_start(repo, tmp_path, monkeypatch)
    assert "ADR 0004: A new decision" in out
    assert "is stale. Run `lore index --write`" in out


def test_session_start_injects_the_full_index_when_claude_md_does_not_import_agents_md(
    repo: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _with_block(repo, imported=False)
    out = _session_start(repo, tmp_path, monkeypatch)
    assert "ADR 0001: One home per fact" in out
    assert "is stale" not in out


def test_a_title_keeps_a_leading_number_that_is_not_the_document_number(repo: Path) -> None:
    (repo / "docs" / "adr" / "0004-routing.md").write_text("# 3-tier model routing\n")
    out = _index(repo).output
    assert "- ADR 0004: 3-tier model routing" in out
    assert "- ADR 0003: Search is federated" in out


def test_write_ignores_a_marker_in_a_code_span_or_block(repo: Path) -> None:
    agents = repo / "AGENTS.md"
    quoted = (
        "Write `<!-- lore index: start -->` and `<!-- lore index: end -->` by hand.\n\n"
        "```\n<!-- lore index: start -->\n<!-- lore index: end -->\n```\n"
    )
    agents.write_text(f"# Agents\n\n{quoted}")
    assert _index(repo, "--write").exit_code == 0
    assert _index(repo, "--write").exit_code == 0
    text = agents.read_text()
    assert text.startswith(f"# Agents\n\n{quoted}")
    assert text.count("### ADRs") == 1
    assert _index(repo, "--check").exit_code == 0


_START, _END = "<!-- lore index: start -->", "<!-- lore index: end -->"


@pytest.mark.parametrize(
    "quoted",
    [
        f"    {_START}\n    {_END}\n",  # indented code block
        f"Write ``{_START}`` and ``{_END}`` by hand.\n",  # double-backtick span
        f"- an item:\n\n  ```\n  {_START}\n  {_END}\n  ```\n",  # fence in a list item
        f"````\n```\n{_START}\n{_END}\n```\n````\n",  # fence around a fence
        f"~~~\n```\n{_START}\n~~~\n",  # a fence closes only on its own character
        "```\nnever closed\n",  # an unclosed fence hides no real block
    ],
)
def test_write_ignores_a_marker_in_any_code_form(repo: Path, quoted: str) -> None:
    agents = repo / "AGENTS.md"
    agents.write_text(f"# Agents\n\n{quoted}")
    assert _index(repo, "--write").exit_code == 0
    assert _index(repo, "--write").exit_code == 0
    text = agents.read_text()
    assert text.startswith(f"# Agents\n\n{quoted}")
    assert text.count("### ADRs") == 1
    assert _index(repo, "--check").exit_code == 0
