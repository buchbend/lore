"""WikiLedger tracks last_curator_a — the only curator.

WikiLedger.update_last_curator("a") is called by Curator A at run-end for
every touched wiki; the SessionStart banner reads it to render "last
curator N ago". Write failures emit a warning to hook-events.jsonl —
never silent.

Back-compat: an old ledger with stale last_curator_b/last_curator_c keys
on disk (pre-B/C-removal) loads fine — unknown keys are ignored.
Partial-failure: if Curator A raises mid-run, last_curator_a must be
EITHER the prior value OR the new value — never absent/clobbered.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import patch

import pytest

from lore_core.ledger import WikiLedger, WikiLedgerEntry


_NOW = datetime(2026, 4, 21, 10, 0, 0, tzinfo=UTC)


# ---------------------------------------------------------------------------
# Ledger-level tests — pure, no Curator involved
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("role", ["a"])
def test_update_last_curator_persists_and_roundtrips(tmp_path: Path, role: str) -> None:
    """update_last_curator(role) writes the timestamp; read() returns it."""
    ledger = WikiLedger(tmp_path, "testwiki")
    (tmp_path / ".lore").mkdir()

    ledger.update_last_curator(role, at=_NOW)

    entry = ledger.read()
    field = f"last_curator_{role}"
    assert getattr(entry, field) == _NOW, (
        f"expected {field} == {_NOW}, got {getattr(entry, field)}"
    )


def test_update_last_curator_preserves_other_fields(tmp_path: Path) -> None:
    """Updating last_curator_a must not clobber other ledger metadata."""
    ledger = WikiLedger(tmp_path, "testwiki")
    (tmp_path / ".lore").mkdir()

    ledger.write(WikiLedgerEntry(wiki="testwiki", pending_transcripts=3))

    ledger.update_last_curator("a", at=_NOW)

    entry = ledger.read()
    assert entry.last_curator_a == _NOW
    assert entry.pending_transcripts == 3


def test_stale_b_c_ledger_keys_load_and_are_ignored(tmp_path: Path) -> None:
    """Back-compat: a ledger written before B/C removal still has stale
    last_curator_b/last_curator_c keys on disk. WikiLedger.read() must
    load it fine and silently ignore the extra keys.
    """
    (tmp_path / ".lore").mkdir()
    old_ledger = tmp_path / ".lore" / "wiki-testwiki-ledger.json"
    old_ledger.write_text(
        json.dumps(
            {
                "wiki": "testwiki",
                "last_curator_a": "2026-04-20T00:00:00+00:00",
                "last_curator_b": "2026-04-20T00:00:00+00:00",
                "last_curator_c": None,
                "last_briefing": None,
                "pending_transcripts": 0,
                "pending_tokens_est": 0,
            }
        )
    )

    ledger = WikiLedger(tmp_path, "testwiki")
    entry = ledger.read()
    assert entry.last_curator_a is not None
    assert not hasattr(entry, "last_curator_b")
    assert not hasattr(entry, "last_curator_c")


def test_update_last_curator_invalid_role_raises(tmp_path: Path) -> None:
    """Defensive: any role other than 'a' is a programmer error, not silent."""
    (tmp_path / ".lore").mkdir()
    ledger = WikiLedger(tmp_path, "testwiki")
    for role in ("b", "c", "z"):
        with pytest.raises(ValueError):
            ledger.update_last_curator(role, at=_NOW)


# ---------------------------------------------------------------------------
# Curator A integration — the bug fix
# ---------------------------------------------------------------------------


def _minimal_curator_a_setup(tmp_path: Path):
    """Build the minimal setup needed to call run_curator_a end-to-end.

    Shared with test_curator_a.py style; seed a pending entry + an
    attached project so Curator A has work.
    """
    from lore_core.ledger import TranscriptLedger, TranscriptLedgerEntry
    from lore_core.types import Turn

    from datetime import UTC, datetime as _dt
    from lore_core.state.attachments import Attachment, AttachmentsFile

    project_dir = tmp_path / "myproject"
    project_dir.mkdir()
    (tmp_path / ".lore").mkdir(parents=True, exist_ok=True)
    _af = AttachmentsFile(tmp_path); _af.load()
    _af.add(Attachment(
        path=project_dir, wiki="private", scope="proj:test",
        attached_at=_dt.now(UTC), source="manual",
    ))
    _af.save()
    wiki_dir = tmp_path / "wiki" / "private"
    (wiki_dir / "sessions").mkdir(parents=True)
    # Curator A gates per-wiki by turn count OR pending age. Tests seed
    # one transcript without populating total_turns (sync isn't run), so
    # the turns arm reads zero — set max_pending_age_s=0 so the age arm
    # always trips. threshold_pending_turns=1 covers the case where a
    # later test does populate total_turns.
    (wiki_dir / ".lore-wiki.yml").write_text(
        "curator:\n  threshold_pending_turns: 1\n  max_pending_age_s: 0\n"
    )

    turns = [
        Turn(index=i, timestamp=None, role="user" if i % 2 == 0 else "assistant", text=f"msg {i}")
        for i in range(5)
    ]
    transcript_path = project_dir / "transcript.jsonl"
    transcript_path.write_text("{}")

    tledger = TranscriptLedger(tmp_path)
    tledger.upsert(
        TranscriptLedgerEntry(
            integration="fake",
            transcript_id="txn-001",
            path=transcript_path,
            directory=project_dir,
            digested_hash=None,
            digested_index_hint=None,
            synthesised_hash=None,
            last_mtime=_NOW,
            curator_a_run=None,
            noteworthy=None,
            session_note=None,
        )
    )
    return project_dir, turns


def _noteworthy_false_client():
    """Importing here to avoid circular collection."""
    from test_curator_a import FakeAnthropicClient
    return FakeAnthropicClient(
        classify_data={
            "noteworthy": False,
            "reason": "trivial",
            "title": "t",
            "bullets": [],
            "files_touched": [],
            "entities": [],
            "decisions": [],
        },
        merge_data={"new": True},
    )


def _fake_adapter(turns):
    from test_curator_a import FakeAdapter
    return FakeAdapter(turns)


def test_curator_a_run_updates_last_curator_a(tmp_path: Path) -> None:
    """After a Curator A run that touched wiki 'private', last_curator_a is set."""
    project_dir, turns = _minimal_curator_a_setup(tmp_path)
    adapter = _fake_adapter(turns)

    from lore_curator.session_curator import run_curator_a

    wledger = WikiLedger(tmp_path, "private")
    assert wledger.read().last_curator_a is None, "precondition"

    run_curator_a(
        lore_root=tmp_path,
        llm_client=_noteworthy_false_client(),
        adapter_lookup=lambda host: adapter if host == "fake" else None,
        now=_NOW,
    )

    after = wledger.read().last_curator_a
    assert after is not None, (
        "last_curator_a must be written after a Curator A run that touched the wiki"
    )
    assert after == _NOW, f"expected {_NOW}, got {after}"


def test_curator_a_does_not_update_untouched_wikis(tmp_path: Path) -> None:
    """If Curator A has no pending entries for wiki X, X's last_curator_a stays None."""
    project_dir, turns = _minimal_curator_a_setup(tmp_path)
    # Create an additional wiki with no transcripts touched.
    (tmp_path / "wiki" / "untouched" / "sessions").mkdir(parents=True)
    wledger_untouched = WikiLedger(tmp_path, "untouched")
    assert wledger_untouched.read().last_curator_a is None

    adapter = _fake_adapter(turns)
    from lore_curator.session_curator import run_curator_a

    run_curator_a(
        lore_root=tmp_path,
        llm_client=_noteworthy_false_client(),
        adapter_lookup=lambda host: adapter if host == "fake" else None,
        now=_NOW,
    )

    assert wledger_untouched.read().last_curator_a is None, (
        "untouched wiki must NOT have last_curator_a updated"
    )


# ---------------------------------------------------------------------------
# Partial-failure guard
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Observability: write-failure emits a warning event
# ---------------------------------------------------------------------------


def test_ledger_write_failure_emits_warning_event(tmp_path: Path) -> None:
    """If update_last_curator's atomic_write_text raises, a warning event appears
    in hook-events.jsonl with exception details. The failure is not swallowed
    silently.
    """
    (tmp_path / ".lore").mkdir()
    wledger = WikiLedger(tmp_path, "testwiki")

    from lore_core import ledger as ledger_mod

    def raising_write(path, content):
        raise OSError("fake disk error")

    with patch.object(ledger_mod, "atomic_write_text", raising_write):
        # Must not raise past the helper — the whole point is observability, not crash.
        wledger.update_last_curator("a", at=_NOW)

    events_path = tmp_path / ".lore" / "hook-events.jsonl"
    assert events_path.exists(), "hook-events.jsonl must exist after write failure"
    lines = [json.loads(l) for l in events_path.read_text().splitlines() if l.strip()]
    warnings = [
        e
        for e in lines
        if e.get("event") == "wiki-ledger" and e.get("outcome") == "warning"
    ]
    assert warnings, f"expected a wiki-ledger/warning event, got events={lines}"
    err = warnings[0].get("error") or {}
    assert "fake disk error" in (err.get("message") or "")


# ---------------------------------------------------------------------------
# Banner integration — proves the bug-fix actually stops the lie
# ---------------------------------------------------------------------------


def test_banner_renders_real_last_curator_time(tmp_path: Path) -> None:
    """With last_curator_a written 30m ago AND a pending transcript present,
    the banner's "last curator 30m ago" substring appears.

    Pre-Task-3: last_curator_a was never written in prod, so this banner
    segment was either missing or rendered stale/migration fixture data.
    """
    from lore_core.ledger import TranscriptLedger, TranscriptLedgerEntry
    (tmp_path / ".lore").mkdir()
    (tmp_path / "wiki" / "private" / "sessions").mkdir(parents=True)

    # Write last_curator_a 30m ago.
    thirty_min_ago = _NOW - timedelta(minutes=30)
    WikiLedger(tmp_path, "private").update_last_curator("a", at=thirty_min_ago)

    # Seed a pending transcript so the banner enters the branch that shows
    # "last curator". (Banner design: "last curator" rides on the pending
    # count; "up to date" branch omits the curator timestamp.)
    project = tmp_path / "project"
    project.mkdir()
    tledger = TranscriptLedger(tmp_path)
    tledger.upsert(
        TranscriptLedgerEntry(
            integration="fake",
            transcript_id="t1",
            path=project / "t.jsonl",
            directory=project,
            digested_hash=None,
            digested_index_hint=None,
            synthesised_hash=None,
            last_mtime=_NOW,
            curator_a_run=None,
            noteworthy=None,
            session_note=None,
        )
    )

    from lore_cli.breadcrumb import BannerContext, render_banner
    from lore_core.types import Scope as ScopeType
    from lore_core.wiki_config import load_wiki_config

    scope = ScopeType(
        wiki="private",
        scope="test",
        backend="none",
        claude_md_path=tmp_path / "CLAUDE.md",
    )
    (tmp_path / "CLAUDE.md").write_text("# x\n## Lore\n- wiki: private\n- scope: test\n")
    wiki_dir = tmp_path / "wiki" / "private"
    cfg = load_wiki_config(wiki_dir)

    ctx = BannerContext(
        lore_root=tmp_path,
        scope=scope,
        wiki_config=cfg,
        now=_NOW,
        note_count=0,
    )
    banner = render_banner(ctx)
    assert banner is None, (
        "non-error pipeline state should not produce a banner"
    )
