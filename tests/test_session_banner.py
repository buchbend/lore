"""Golden test for ``render_session_banner``.

Pins byte-stability across the v1 → v2 SessionStart unification
(issue #80, PR 5). Both v1-style facts (legacy fallback — no scope,
no gh issues/PRs) and v2-style facts (``## Lore`` block — full
filter-driven fetch) flow through the same renderer; this test
asserts each shape produces its expected literal output.
"""

from __future__ import annotations

import pytest

from lore_cli import hooks
from lore_cli.hooks import SessionFacts, render_session_banner


@pytest.fixture(autouse=True)
def _deterministic_directives(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(hooks, "_lore_version", lambda: "9.9.9")
    monkeypatch.setattr(
        hooks, "_load_directive_lines", lambda: ["## Directives", "- vault-first"]
    )
    monkeypatch.setattr(hooks, "_citation_directive_lines", lambda: [])
    monkeypatch.setattr(hooks, "_journal_directive_lines", lambda: [])


PROJECT_ENTRY = {"name": "data-transfer", "description": "Long-haul data pipeline"}
SESSION_HINTS = (
    ("12-1530-fix-thing", "fixed the thing"),
    ("12-0900-prep", "prep for sprint"),
)


V1_FACTS = SessionFacts(
    wiki_name="ccat",
    repo="ccatobs/data-transfer",
    scope="",
    issues=(),
    prs=(),
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
    "## Directives\n"
    "- vault-first"
)


V2_FACTS = SessionFacts(
    wiki_name="ccat",
    repo="ccatobs/data-transfer",
    scope="ccat:data-center:data-transfer",
    issues=({"number": 47, "title": "x"}, {"number": 52, "title": "y"}),
    prs=({"number": 31, "title": "z"},),
    project_entry=PROJECT_ENTRY,
    session_hints=SESSION_HINTS,
    freshness_audit_lines=(),
    pending_chip=None,
)

V2_EXPECTED = (
    "lore 9.9.9: active · ccat:data-center:data-transfer · "
    "last: fixed the thing · 2 issues · 1 PR\n"
    "\n"
    "## Focus: [[data-transfer]]\n"
    "Long-haul data pipeline\n"
    "\n"
    "Last: [[12-1530-fix-thing]] — fixed the thing\n"
    "Last: [[12-0900-prep]] — prep for sprint\n"
    "\n"
    "## Directives\n"
    "- vault-first"
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
        issues=(),
        prs=(),
        project_entry=None,
        session_hints=(),
        freshness_audit_lines=(),
        pending_chip="1 pending verdict",
    )
    out = render_session_banner(facts)
    assert out.splitlines()[0] == "lore 9.9.9: active · 1 pending verdict"
    assert "_Repo `ccatobs/data-transfer` has no dedicated project note in ccat._" in out
