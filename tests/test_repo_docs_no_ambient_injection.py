"""Repo ADR/PRD pull is explicit-call-only — never ambient.

PRD 0001 ("Ambient vs pull") is explicit: nothing from ADRs/PRDs is
injected into the SessionStart banner or any other ambient surface;
depth comes only from an explicit MCP pull. This is a static guard —
the hook module that renders ambient context must never reference the
new repo-docs handlers.
"""

from __future__ import annotations

import inspect


def test_hooks_module_does_not_reference_repo_docs_handlers() -> None:
    from lore_cli import hooks

    source = inspect.getsource(hooks)
    for needle in (
        "repo_docs",
        "handle_repo_docs_list",
        "handle_repo_docs_fetch",
        "lore_repo_docs_list",
        "lore_repo_docs_fetch",
    ):
        assert needle not in source, f"ambient hook module references {needle!r}"


def test_session_banner_render_is_unaffected_by_repo_docs(tmp_path, monkeypatch) -> None:
    """A repo with populated docs/adr, docs/prd still renders the banner
    without importing or touching the pull handlers."""
    monkeypatch.setenv("LORE_ROOT", str(tmp_path))
    (tmp_path / "docs/adr").mkdir(parents=True)
    (tmp_path / "docs/adr/0001-x.md").write_text("---\ntitle: X\n---\nbody\n")

    from lore_core.session_start import SessionFacts, render_session_banner

    facts = SessionFacts(wiki_name="private", repo=None)
    banner = render_session_banner(facts)
    assert "0001-x" not in banner
    assert "docs/adr" not in banner
