"""Golden test for ``render_session_banner``.

Pins the ambient-minimum shape: one status line (nothing fetched from
gh), an optional Focus block, the last-active-day recap read off the
transcript ledger, freshness lines only on positive evidence, and a
single collapsed directive. No other section is emitted.

The recap replaced the last-session note hints when session notes were
retired — same three-line budget, a source that costs no LLM call.
"""

from __future__ import annotations

import pytest

from lore_core import session_start
from lore_core.session_start import SessionFacts, render_session_banner


@pytest.fixture(autouse=True)
def _deterministic_directive(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(session_start, "lore_version", lambda: "9.9.9")
    monkeypatch.setattr(
        session_start,
        "load_directive_lines",
        lambda: ["## Directive", "- pull, not push"],
    )


PROJECT_ENTRY = {"name": "data-transfer", "description": "Long-haul data pipeline"}
RECAP = (
    "Last active 2026-08-04 — 2 sessions in ccatobs/data-transfer",
    "Branches: feat/12-fix-thing",
    "Refs: #12",
)


V1_FACTS = SessionFacts(
    wiki_name="ccat",
    repo="ccatobs/data-transfer",
    scope="",
    project_entry=PROJECT_ENTRY,
    pending_chip=None,
    recap=RECAP,
)

V1_EXPECTED = (
    "lore 9.9.9: active · [[data-transfer]] · last active 2026-08-04\n"
    "\n"
    "## Focus: [[data-transfer]]\n"
    "Long-haul data pipeline\n"
    "\n"
    "Last active 2026-08-04 — 2 sessions in ccatobs/data-transfer\n"
    "Branches: feat/12-fix-thing\n"
    "Refs: #12\n"
    "\n"
    "## Directive\n"
    "- pull, not push"
)


V2_FACTS = SessionFacts(
    wiki_name="ccat",
    repo="ccatobs/data-transfer",
    scope="ccat:data-center:data-transfer",
    project_entry=PROJECT_ENTRY,
    pending_chip=None,
    recap=RECAP,
)

V2_EXPECTED = (
    "lore 9.9.9: active · ccat:data-center:data-transfer · last active 2026-08-04\n"
    "\n"
    "## Focus: [[data-transfer]]\n"
    "Long-haul data pipeline\n"
    "\n"
    "Last active 2026-08-04 — 2 sessions in ccatobs/data-transfer\n"
    "Branches: feat/12-fix-thing\n"
    "Refs: #12\n"
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
        pending_chip="1 pending verdict",
    )
    out = render_session_banner(facts)
    assert out.splitlines()[0] == "lore 9.9.9: active · 1 pending verdict"
    assert "_Repo `ccatobs/data-transfer` has no dedicated project note in ccat._" in out


def test_render_session_banner_has_exactly_the_agreed_elements() -> None:
    """The ambient-minimum contract: status line, Focus block, ≤3 recap
    lines, freshness lines, and a single directive — nothing else. Feed
    every optional slot so a stray extra section would show up."""
    facts = SessionFacts(
        wiki_name="ccat",
        repo="ccatobs/data-transfer",
        scope="ccat:data-center:data-transfer",
        project_entry=PROJECT_ENTRY,
        pending_chip="1 pending verdict",
        recap=RECAP,
    )
    out = render_session_banner(facts)

    expected = (
        "lore 9.9.9: active · ccat:data-center:data-transfer · "
        "last active 2026-08-04 · 1 pending verdict\n"
        "\n"
        "## Focus: [[data-transfer]]\n"
        "Long-haul data pipeline\n"
        "\n"
        "Last active 2026-08-04 — 2 sessions in ccatobs/data-transfer\n"
        "Branches: feat/12-fix-thing\n"
        "Refs: #12\n"
        "\n"
        "## Directive\n"
        "- pull, not push"
    )
    assert out == expected

    # The recap's refs come from the ledger's linkage block, which capture
    # wrote from git. The banner still fetches nothing from gh — see
    # ``test_session_facts_has_no_issue_or_pr_fields``.

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
