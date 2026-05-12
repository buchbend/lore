"""SessionStart hook applies ``redact_human_only`` to injected note body.

PRD #92 slice #95. The SessionStart hook is an LLM-facing boundary —
any body content it injects must be stripped of the human-only region
before reaching the agent. Today the only body-content injection is
``_render_project_orientation``; this test asserts the contract there
and is the reference for any future body-injection site to honour.

Acceptance criteria:

* Fixture orientation note with a ``<!-- lore:human-only -->`` region:
  the returned context block does not contain the human-only body.
* Old notes (no marker) are injected in full — backwards compatible.
"""

from __future__ import annotations

from pathlib import Path

from lore_cli.hooks import _render_project_orientation
from lore_core.regions import HUMAN_ONLY_MARKER
from lore_core.types import Scope


def _scope(wiki: str, scope_str: str) -> Scope:
    return Scope(
        wiki=wiki,
        scope=scope_str,
        backend="none",
        claude_md_path=Path("/tmp/dummy/CLAUDE.md"),
    )


def test_orientation_redacts_human_only_region(tmp_path):
    """A project orientation note with a human-only marker yields a
    SessionStart context block that contains the reload-safe body
    but never the human-only body."""
    wiki = tmp_path / "private"
    project_dir = wiki / "projects" / "lore"
    project_dir.mkdir(parents=True)
    (project_dir / "lore.md").write_text(
        "---\n"
        "type: project\n"
        "scope: lore\n"
        "---\n"
        "\n"
        "# Project: lore\n"
        "\n"
        "Reload-safe orientation body — LLM may see this.\n"
        "\n"
        f"{HUMAN_ONLY_MARKER}\n"
        "\n"
        "SECRET-HUMAN-ONLY-PHRASE never load this into a session.\n"
    )

    result = _render_project_orientation(_scope("private", "lore"), tmp_path)
    assert result is not None
    assert "Reload-safe orientation body" in result
    assert "SECRET-HUMAN-ONLY-PHRASE" not in result
    assert HUMAN_ONLY_MARKER not in result


def test_orientation_without_marker_returns_full_body(tmp_path):
    """Backwards compatibility — notes that pre-date the two-region
    schema have no marker and must be injected in full."""
    wiki = tmp_path / "private"
    project_dir = wiki / "projects" / "lore"
    project_dir.mkdir(parents=True)
    legacy_body = (
        "# Project: lore\n"
        "\n"
        "Legacy orientation — the whole body is reload-safe.\n"
        "\n"
        "Second paragraph still present.\n"
    )
    (project_dir / "lore.md").write_text(
        "---\ntype: project\n---\n\n" + legacy_body
    )

    result = _render_project_orientation(_scope("private", "lore"), tmp_path)
    assert result is not None
    assert "Legacy orientation" in result
    assert "Second paragraph still present" in result


def test_orientation_redacts_before_truncation(tmp_path):
    """The human-only region is dropped *before* the orientation budget
    cap is applied — otherwise a long human-only tail could push the
    reload-safe body out of the visible window."""
    from lore_cli.hooks import ORIENTATION_BUDGET_CHARS

    wiki = tmp_path / "private"
    project_dir = wiki / "projects" / "lore"
    project_dir.mkdir(parents=True)
    reload_safe_body = "Short reload-safe orientation.\n"
    human_only_tail = "x" * (ORIENTATION_BUDGET_CHARS * 2)
    (project_dir / "lore.md").write_text(
        "---\ntype: project\n---\n\n"
        + reload_safe_body
        + f"\n{HUMAN_ONLY_MARKER}\n\n"
        + human_only_tail
    )

    result = _render_project_orientation(_scope("private", "lore"), tmp_path)
    assert result is not None
    assert "Short reload-safe orientation" in result
    assert "orientation truncated" not in result
    assert "xxx" not in result
