"""SessionStart additionalContext suppresses citation affordances when
`/lore:off citations` is active for the session.

The citations toggle is narrower than `off all` — hooks and MCP keep
working, but the additionalContext directive includes a "do not emit
`› consulted [[X]]`" line so the agent stops rendering inline
breadcrumbs above answers that consulted the vault.
"""

from __future__ import annotations

import pytest

from lore_core import toggles


@pytest.fixture(autouse=True)
def _isolate_env(tmp_path, monkeypatch):
    monkeypatch.setenv("TMPDIR", str(tmp_path))
    monkeypatch.delenv("CLAUDE_SESSION_ID", raising=False)
    yield


def test_citations_directive_empty_when_unset() -> None:
    """No sentinel, no sid → no directive appended."""
    from lore_cli.hooks import _citation_directive_lines
    assert _citation_directive_lines() == []


def test_citations_directive_empty_when_toggle_off(monkeypatch: pytest.MonkeyPatch) -> None:
    """sid present but no sentinel → no directive."""
    monkeypatch.setenv("CLAUDE_SESSION_ID", "sid-cite-A")
    from lore_cli.hooks import _citation_directive_lines
    assert _citation_directive_lines() == []


def test_citations_directive_present_when_toggle_set(monkeypatch: pytest.MonkeyPatch) -> None:
    """Sentinel set → directive lines include the suppression instruction."""
    sid = "sid-cite-B"
    monkeypatch.setenv("CLAUDE_SESSION_ID", sid)
    toggles.set_off("citations", sid)

    from lore_cli.hooks import _citation_directive_lines
    lines = _citation_directive_lines()

    assert lines, "expected at least one directive line when off-citations is set"
    joined = "\n".join(lines)
    assert "consulted" in joined.lower()
    assert "silenced" in joined.lower() or "suppress" in joined.lower() or "do not" in joined.lower()


def test_citations_directive_independent_of_off_all(monkeypatch: pytest.MonkeyPatch) -> None:
    """`off all` doesn't imply `off citations` for this helper —
    the directive only fires when the citations sentinel is set."""
    sid = "sid-cite-C"
    monkeypatch.setenv("CLAUDE_SESSION_ID", sid)
    toggles.set_off("all", sid)
    # Citations sentinel deliberately not set.

    from lore_cli.hooks import _citation_directive_lines
    assert _citation_directive_lines() == []


def test_user_prompt_submit_emits_directive_mid_session(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    """`/lore:off citations` must take effect on the very next prompt.

    Without this, the citation directive only lands at the *next*
    SessionStart, so the user sees one or more `› consulted` lines
    after they've explicitly silenced them — code-reviewer major
    finding.
    """
    import io
    import json
    import sys as _sys
    from io import StringIO
    from unittest.mock import patch
    from lore_cli import hooks

    sid = "sid-cite-D"
    monkeypatch.setenv("CLAUDE_SESSION_ID", sid)
    monkeypatch.delenv("LORE_CURATOR_MODE", raising=False)

    # Stdin payload publishes the sid (mirrors the SessionStart fixture).
    payload = {"session_id": sid, "hook_event_name": "UserPromptSubmit"}
    stream = io.StringIO(json.dumps(payload))
    stream.isatty = lambda: False  # type: ignore[method-assign]
    monkeypatch.setattr("sys.stdin", stream)

    toggles.set_off("citations", sid)

    captured = StringIO()
    monkeypatch.setattr(_sys, "stdout", captured)

    # Heartbeat returns nothing — a quiet turn. The directive must still fire.
    with patch.object(hooks, "_heartbeat", return_value=("", "")), \
         patch.object(hooks, "resolve_scope", return_value=type("S", (), {"claude_md_path": tmp_path / "CLAUDE.md"})()), \
         patch.object(hooks, "_infer_lore_root", return_value=tmp_path), \
         patch.object(hooks, "_load_wiki_cfg_from_scope", return_value={}):
        hooks.cmd_user_prompt_submit(cwd=str(tmp_path), plain=False)

    out = captured.getvalue()
    assert out, "expected envelope output containing the citation directive"
    # Envelope is a single JSON line.
    envelope = json.loads(out.strip())
    additional = envelope.get("hookSpecificOutput", {}).get("additionalContext", "")
    assert "consulted" in additional.lower()


def test_session_start_does_not_include_citation_directive(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    """The SessionStart banner never carries the citation directive —
    it collapsed into the single ambient directive. The suppression
    still applies mid-session, re-asserted on every prompt (see
    ``test_user_prompt_submit_emits_directive_mid_session``)."""
    sid = "sid-cite-E"
    monkeypatch.setenv("CLAUDE_SESSION_ID", sid)
    toggles.set_off("citations", sid)

    # Build minimal inputs for _session_start_from_lore.
    wiki = tmp_path / "wiki"
    wiki.mkdir()
    (wiki / "_index.txt").write_text("# Index\n")

    from lore_core import session_start

    monkeypatch.setattr(session_start, "current_repo", lambda _cwd: None)

    block = {"wiki": wiki.name, "scope": wiki.name, "backend": None, "issues": None, "prs": None}
    config = (tmp_path / "CLAUDE.md", block)
    out = session_start.session_start_from_lore(str(tmp_path), config, tmp_path)

    assert out is not None
    assert "consulted" not in out.lower()
