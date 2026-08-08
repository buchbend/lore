"""Browser rendering of the flag review walk.

The walk's verdicts already have their own suite (``test_flag_review``).
What is asserted here is what the browser page adds: the ref verdict read
as a colour, the code-stamped lead prefix read as a label, grouping by
owning note, and the token that keeps another origin from forging a
verdict against a guessed port.
"""

from __future__ import annotations

import io
import json
import sys
import threading
import urllib.error
import urllib.request
from pathlib import Path

import pytest
from lore_cli import flag_review_html as board
from lore_cli.__main__ import app
from lore_core import flag
from lore_core.ref_verify import MISSING, UNCHECKED, VERIFIED
from typer.testing import CliRunner

runner = CliRunner()


@pytest.fixture()
def vault(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("LORE_ROOT", str(tmp_path))
    monkeypatch.setenv("LORE_CACHE", str(tmp_path / "cache"))
    monkeypatch.delenv("CLAUDE_SESSION_ID", raising=False)
    (tmp_path / "wiki" / "lore" / "concepts").mkdir(parents=True)
    return tmp_path


class _Terminal(io.StringIO):
    """Stand-in for an interactive stdout, so the browser probe gets that far."""

    def isatty(self) -> bool:
        return True


def _wiki(vault: Path) -> Path:
    return vault / "wiki" / "lore"


def _note(vault: Path, slug: str) -> Path:
    path = _wiki(vault) / "concepts" / f"{slug}.md"
    path.write_text(
        "---\nschema_version: 2\ntype: concept\ncreated: 2026-08-01\n"
        f"last_reviewed: 2026-08-01\ndescription: about {slug}\ntags: []\n---\n\n"
        f"# {slug}\n\nExisting prose.\n",
        encoding="utf-8",
    )
    return path


def _stamped(vault: Path, slug: str, lead: str, verdict: str, flag_id: str) -> None:
    """Append one agent-filed flag whose ref carries a chosen verdict.

    ``flag.write`` runs the real ref verifier, which cannot mint a
    ``VERIFIED`` stamp inside a tmp vault. ``render_block`` is the same
    pure renderer that write calls, so the block on disk is byte-identical
    to one a real write would have produced.
    """
    path = _wiki(vault) / "concepts" / f"{slug}.md"
    if not path.exists():
        _note(vault, slug)
    block = flag.render_block(
        flag_id=flag_id,
        lead=lead,
        body="Why the fact matters.",
        author="claude",
        day="2026-08-08",
        refs=[("pr", "357")],
        verdicts={("pr", "357"): verdict},
        transcript="tr-1",
        reviewed=False,
        stamped=True,
    )
    path.write_text(f"{path.read_text()}\n{block}\n", encoding="utf-8")


# ---------------------------------------------------------------------------
# The page renders the ref verdict as a colour
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("verdict", "expected"),
    [(VERIFIED, "verified"), (UNCHECKED, "unchecked"), (MISSING, "missing")],
)
def test_card_carries_the_ref_verdict_as_a_class(vault: Path, verdict: str, expected: str):
    _stamped(vault, "reaper", "The reaper drops stale rows.", verdict, "a" * 12)
    page = board.render_page(flag.pending(_wiki(vault)), wiki="lore", token="t")
    assert f'class="card {expected}"' in page


def test_page_groups_cards_by_owning_note(vault: Path):
    _stamped(vault, "reaper", "In reaper.", UNCHECKED, "a" * 12)
    _stamped(vault, "drain", "In drain.", UNCHECKED, "b" * 12)
    page = board.render_page(flag.pending(_wiki(vault)), wiki="lore", token="t")
    assert page.count("<section>") == 2
    assert "<h2>reaper</h2>" in page
    assert "<h2>drain</h2>" in page


def test_stamped_lead_prefix_becomes_a_label_and_leaves_the_sentence(vault: Path):
    _stamped(vault, "reaper", "The reaper drops stale rows.", MISSING, "a" * 12)
    page = board.render_page(flag.pending(_wiki(vault)), wiki="lore", token="t")
    # The prefix is authority phrasing (ADR 0004): re-presented, never dropped.
    assert "ref not found" in page
    assert "Claimed in session, ref not found:" not in page
    assert "The reaper drops stale rows." in page


def test_page_offers_existing_note_slugs_as_retarget_completions(vault: Path):
    _stamped(vault, "reaper", "In reaper.", UNCHECKED, "a" * 12)
    _note(vault, "drain")
    page = board.render_page(
        flag.pending(_wiki(vault)), wiki="lore", token="t", slugs=board.note_slugs(_wiki(vault))
    )
    assert "<datalist" in page
    assert '<option value="concepts/drain.md">' in page


def test_page_escapes_html_a_flag_body_could_carry(vault: Path):
    _stamped(vault, "reaper", "A lead with <script>alert(1)</script> inside.", UNCHECKED, "a" * 12)
    page = board.render_page(flag.pending(_wiki(vault)), wiki="lore", token="t")
    assert "<script>alert(1)</script>" not in page
    assert "&lt;script&gt;" in page


# ---------------------------------------------------------------------------
# Verdicts run through lore_core.flag — no second verdict path
# ---------------------------------------------------------------------------


def test_accept_from_the_page_removes_the_marker(vault: Path):
    _stamped(vault, "reaper", "Keep this.", UNCHECKED, "a" * 12)
    result = board.apply_verdict(_wiki(vault), "a" * 12, "accept")
    assert result["ok"] is True
    assert flag.count_pending(_wiki(vault)) == 0


def test_decline_from_the_page_deletes_the_block(vault: Path):
    _stamped(vault, "reaper", "Drop this.", UNCHECKED, "a" * 12)
    board.apply_verdict(_wiki(vault), "a" * 12, "decline")
    assert "Drop this." not in (_wiki(vault) / "concepts" / "reaper.md").read_text()


def test_retarget_moves_the_block_and_keeps_it_pending(vault: Path):
    _stamped(vault, "reaper", "Wrong home.", UNCHECKED, "a" * 12)
    _note(vault, "drain")
    result = board.apply_verdict(_wiki(vault), "a" * 12, "retarget", "concepts/drain.md")
    assert result["ok"] is True
    # ADR 0008 gives marker removal to accept alone, so the card stays up.
    assert result["pending"] is True
    still = flag.pending(_wiki(vault))
    assert len(still) == 1
    assert Path(still[0].note_path).stem == "drain"


def test_retarget_without_a_target_is_refused(vault: Path):
    _stamped(vault, "reaper", "Wrong home.", UNCHECKED, "a" * 12)
    result = board.apply_verdict(_wiki(vault), "a" * 12, "retarget", "")
    assert result["ok"] is False
    assert flag.count_pending(_wiki(vault)) == 1


# ---------------------------------------------------------------------------
# The listener: loopback, token-gated, exits on done
# ---------------------------------------------------------------------------


@pytest.fixture()
def server(vault: Path):
    srv = board.build_server(_wiki(vault))
    thread = threading.Thread(target=srv.serve_forever, daemon=True)
    thread.start()
    yield srv
    srv.shutdown()
    srv.server_close()
    thread.join(timeout=5)


def _url(srv, path: str = "/") -> str:
    return f"http://127.0.0.1:{srv.server_port}{path}"


def _post(srv, path: str, payload: dict):
    request = urllib.request.Request(
        _url(srv, path),
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"},
    )
    return urllib.request.urlopen(request, timeout=5)


def test_listener_binds_the_loopback_address(server):
    assert server.server_address[0] == "127.0.0.1"


def test_page_needs_the_token(vault: Path, server):
    _stamped(vault, "reaper", "Secret-ish.", UNCHECKED, "a" * 12)
    with pytest.raises(urllib.error.HTTPError) as caught:
        urllib.request.urlopen(_url(server, "/?t=wrong"), timeout=5)
    assert caught.value.code == 403


def test_page_renders_with_the_token(vault: Path, server):
    _stamped(vault, "reaper", "Visible fact.", UNCHECKED, "a" * 12)
    body = urllib.request.urlopen(_url(server, f"/?t={server.token}"), timeout=5).read().decode()
    assert "Visible fact." in body


def test_verdict_without_the_token_is_refused(vault: Path, server):
    _stamped(vault, "reaper", "Keep this.", UNCHECKED, "a" * 12)
    with pytest.raises(urllib.error.HTTPError) as caught:
        _post(server, "/verdict", {"id": "a" * 12, "verdict": "accept", "t": "wrong"})
    assert caught.value.code == 403
    assert flag.count_pending(_wiki(vault)) == 1


def test_verdict_with_the_token_applies(vault: Path, server):
    _stamped(vault, "reaper", "Keep this.", UNCHECKED, "a" * 12)
    _post(server, "/verdict", {"id": "a" * 12, "verdict": "accept", "t": server.token})
    assert flag.count_pending(_wiki(vault)) == 0


def test_done_stops_the_listener(vault: Path):
    srv = board.build_server(_wiki(vault))
    thread = threading.Thread(target=srv.serve_forever, daemon=True)
    thread.start()
    _post(srv, "/done", {"t": srv.token})
    thread.join(timeout=5)
    assert thread.is_alive() is False
    srv.server_close()


# ---------------------------------------------------------------------------
# CLI wiring: browser by default, terminal prompts as the fallback
# ---------------------------------------------------------------------------


def test_review_serves_the_page_by_default(vault: Path, monkeypatch: pytest.MonkeyPatch):
    _stamped(vault, "reaper", "A fact.", UNCHECKED, "a" * 12)
    served: list[Path] = []
    monkeypatch.setattr(board, "browser_available", lambda: True)
    monkeypatch.setattr(board, "serve", lambda path, **kw: served.append(path) or "url")
    result = runner.invoke(app, ["flag", "review", "--wiki", "lore"])
    assert result.exit_code == 0
    assert served == [_wiki(vault)]


def test_browser_available_reports_false_on_a_headless_host(monkeypatch: pytest.MonkeyPatch):
    import webbrowser

    monkeypatch.setattr(sys, "stdout", _Terminal())
    monkeypatch.setattr(
        webbrowser, "get", lambda *a: (_ for _ in ()).throw(webbrowser.Error("none"))
    )
    assert board.browser_available() is False


def test_browser_available_reports_false_when_stdout_is_not_a_terminal(
    monkeypatch: pytest.MonkeyPatch,
):
    # Without the guard the listener runs forever against nobody: a piped or
    # captured run blocks in serve_forever with no prompt and no page.
    monkeypatch.setattr(sys, "stdout", io.StringIO())
    assert board.browser_available() is False


def test_review_tty_runs_the_terminal_prompts(vault: Path, monkeypatch: pytest.MonkeyPatch):
    _stamped(vault, "reaper", "A fact.", UNCHECKED, "a" * 12)
    monkeypatch.setattr(
        board, "serve", lambda *a, **kw: pytest.fail("--tty must not start a listener")
    )
    result = runner.invoke(app, ["flag", "review", "--wiki", "lore", "--tty"], input="a\n")
    assert result.exit_code == 0
    assert "accepted" in result.output
    assert flag.count_pending(_wiki(vault)) == 0


def test_review_falls_back_to_the_prompts_when_no_browser_resolves(
    vault: Path, monkeypatch: pytest.MonkeyPatch
):
    _stamped(vault, "reaper", "A fact.", UNCHECKED, "a" * 12)
    monkeypatch.setattr(board, "browser_available", lambda: False)
    monkeypatch.setattr(
        board, "serve", lambda *a, **kw: pytest.fail("a headless host must not start a listener")
    )
    result = runner.invoke(app, ["flag", "review", "--wiki", "lore"], input="s\n")
    assert result.exit_code == 0
    assert "A fact." in result.output


def test_review_reports_nothing_pending_without_starting_a_listener(
    vault: Path, monkeypatch: pytest.MonkeyPatch
):
    _note(vault, "reaper")
    monkeypatch.setattr(
        board, "serve", lambda *a, **kw: pytest.fail("an empty queue must not start a listener")
    )
    result = runner.invoke(app, ["flag", "review", "--wiki", "lore"])
    assert result.exit_code == 0
    assert "(no pending flags)" in result.output
