"""Golden test for ``render_session_banner``.

Pins the ambient-minimum shape settled in the trim-to-safe-core PRD: one
status line (no issue/PR counts), an optional Focus block, at most two
last-session hints, freshness lines only on positive evidence, and a
single collapsed directive. No other section is emitted.
"""

from __future__ import annotations

import pytest

from lore_cli import hooks
from lore_cli.hooks import SessionFacts, render_session_banner


@pytest.fixture(autouse=True)
def _deterministic_directive(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(hooks, "_lore_version", lambda: "9.9.9")
    monkeypatch.setattr(
        hooks, "_load_directive_lines", lambda: ["## Directive", "- pull, not push"]
    )


PROJECT_ENTRY = {"name": "data-transfer", "description": "Long-haul data pipeline"}
SESSION_HINTS = (
    ("12-1530-fix-thing", "fixed the thing"),
    ("12-0900-prep", "prep for sprint"),
)


V1_FACTS = SessionFacts(
    wiki_name="ccat",
    repo="ccatobs/data-transfer",
    scope="",
    project_entry=PROJECT_ENTRY,
    session_hints=SESSION_HINTS,
    freshness_audit_lines=(),
    pending_chip=None,
)

V1_EXPECTED = (
    "lore 9.9.9: active · [[data-transfer]] · last: fixed the thing\n"
    "\n"
    "## Focus: [[data-transfer]]\n"
    "Long-haul data pipeline\n"
    "\n"
    "Last: [[12-1530-fix-thing]] — fixed the thing\n"
    "Last: [[12-0900-prep]] — prep for sprint\n"
    "\n"
    "## Directive\n"
    "- pull, not push"
)


V2_FACTS = SessionFacts(
    wiki_name="ccat",
    repo="ccatobs/data-transfer",
    scope="ccat:data-center:data-transfer",
    project_entry=PROJECT_ENTRY,
    session_hints=SESSION_HINTS,
    freshness_audit_lines=(),
    pending_chip=None,
)

V2_EXPECTED = (
    "lore 9.9.9: active · ccat:data-center:data-transfer · last: fixed the thing\n"
    "\n"
    "## Focus: [[data-transfer]]\n"
    "Long-haul data pipeline\n"
    "\n"
    "Last: [[12-1530-fix-thing]] — fixed the thing\n"
    "Last: [[12-0900-prep]] — prep for sprint\n"
    "\n"
    "## Directive\n"
    "- pull, not push"
)


@pytest.mark.parametrize(
    "facts, expected",
    [
        pytest.param(V1_FACTS, V1_EXPECTED, id="v1-legacy-shape"),
        pytest.param(V2_FACTS, V2_EXPECTED, id="v2-lore-block"),
    ],
)
def test_render_session_banner_byte_stable(facts: SessionFacts, expected: str) -> None:
    assert render_session_banner(facts) == expected


def test_render_session_banner_includes_pending_chip_and_no_project_note() -> None:
    """No project_entry + a repo → renders the "no dedicated project note"
    fallback line; pending_chip appears as the last status-line bit."""
    facts = SessionFacts(
        wiki_name="ccat",
        repo="ccatobs/data-transfer",
        scope="",
        project_entry=None,
        session_hints=(),
        freshness_audit_lines=(),
        pending_chip="1 pending verdict",
    )
    out = render_session_banner(facts)
    assert out.splitlines()[0] == "lore 9.9.9: active · 1 pending verdict"
    assert "_Repo `ccatobs/data-transfer` has no dedicated project note in ccat._" in out


def test_render_session_banner_has_exactly_the_agreed_elements() -> None:
    """The ambient-minimum contract: status line, Focus block, ≤2 session
    hints, freshness lines, and a single directive — nothing else. Feed
    every optional slot so a stray extra section would show up."""
    facts = SessionFacts(
        wiki_name="ccat",
        repo="ccatobs/data-transfer",
        scope="ccat:data-center:data-transfer",
        project_entry=PROJECT_ENTRY,
        session_hints=SESSION_HINTS,
        freshness_audit_lines=("### Filtered for staleness", "- 1 excluded"),
        pending_chip="1 pending verdict",
    )
    out = render_session_banner(facts)

    expected = (
        "lore 9.9.9: active · ccat:data-center:data-transfer · "
        "last: fixed the thing · 1 pending verdict\n"
        "\n"
        "## Focus: [[data-transfer]]\n"
        "Long-haul data pipeline\n"
        "\n"
        "Last: [[12-1530-fix-thing]] — fixed the thing\n"
        "Last: [[12-0900-prep]] — prep for sprint\n"
        "\n"
        "### Filtered for staleness\n"
        "- 1 excluded\n"
        "\n"
        "## Directive\n"
        "- pull, not push"
    )
    assert out == expected

    # No count-shaped bits ("N issues" / "N PRs") anywhere — the banner
    # never fetches gh for counts.
    assert "issue" not in out.lower()
    assert "PR" not in out

    # Exactly one directive heading — the three former blocks (vault-first
    # + freshness nudges, citation suppression, journal invitation) are
    # collapsed into this single one.
    assert out.count("## Directive") == 1


def test_session_facts_has_no_issue_or_pr_fields() -> None:
    """SessionFacts carries no gh-derived issue/PR data — the banner
    never counts them, so there is nothing to fetch or store."""
    import dataclasses

    field_names = {f.name for f in dataclasses.fields(SessionFacts)}
    assert "issues" not in field_names
    assert "prs" not in field_names
