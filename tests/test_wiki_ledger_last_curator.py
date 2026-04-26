"""Task 3: WikiLedger must track last_curator_{a,b,c}.

Pre-Task-3: last_curator_a was READ by the SessionStart banner but NEVER
WRITTEN by Curator A. The banner rendered a permanent lie.

Task 3 adds:
- last_curator_c field (symmetry with the A/B/C triad)
- WikiLedger.update_last_curator(role) helper
- Curator A calls it at run-end for every touched wiki
- Curator B continues to update last_curator_b (regression guard)
- Write failures emit a warning to hook-events.jsonl — never silent.

Back-compat: old ledgers without last_curator_c load fine (field = None).
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


@pytest.mark.parametrize("role", ["a", "b", "c"])
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
    """Updating one role must not clobber the others or other metadata."""
    ledger = WikiLedger(tmp_path, "testwiki")
    (tmp_path / ".lore").mkdir()

    earlier = _NOW - timedelta(days=2)
    ledger.write(
        WikiLedgerEntry(
            wiki="testwiki",
            last_curator_a=earlier,
            last_curator_b=earlier,
            pending_transcripts=3,
        )
    )

    ledger.update_last_curator("c", at=_NOW)

    entry = ledger.read()
    assert entry.last_curator_a == earlier, "a must be preserved"
    assert entry.last_curator_b == earlier, "b must be preserved"
    assert entry.last_curator_c == _NOW
    assert entry.pending_transcripts == 3


def test_missing_last_curator_c_field_loads_as_none(tmp_path: Path) -> None:
    """Back-compat: ledger written before Task 3 has no last_curator_c key."""
    (tmp_path / ".lore").mkdir()
    pre_task3_ledger = tmp_path / ".lore" / "wiki-testwiki-ledger.json"
    pre_task3_ledger.write_text(
        json.dumps(
            {
                "wiki": "testwiki",
                "last_curator_a": "2026-04-20T00:00:00+00:00",
                "last_curator_b": "2026-04-20T00:00:00+00:00",
                # NO last_curator_c field — simulates an old ledger
                "last_briefing": None,
                "pending_transcripts": 0,
                "pending_tokens_est": 0,
            }
        )
    )

    ledger = WikiLedger(tmp_path, "testwiki")
    entry = ledger.read()
    assert entry.last_curator_c is None
    assert entry.last_curator_a is not None


def test_update_last_curator_invalid_role_raises(tmp_path: Path) -> None:
    """Defensive: role outside {'a','b','c'} is a programmer error, not silent."""
    (tmp_path / ".lore").mkdir()
    ledger = WikiLedger(tmp_path, "testwiki")
    with pytest.raises(ValueError):
        ledger.update_last_curator("z", at=_NOW)


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
    # P2: Curator A gates per-wiki by threshold_pending. Default is 10, and
    # this fixture only seeds one pending transcript — set threshold=1 so
    # the test actually exercises the classification / ledger-write path.
    (wiki_dir / ".lore-wiki.yml").write_text(
        "curator:\n  threshold_pending: 1\n"
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
            host="fake",
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
    from tests.test_curator_a import FakeAnthropicClient
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
    from tests.test_curator_a import FakeAdapter
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


def test_partial_failure_does_not_clobber_prior_last_curator_a(tmp_path: Path) -> None:
    """If Curator A raises mid-run, last_curator_a is either prior OR new — never cleared.

    Atomic-or-unchanged contract. Guards against a future bug where
    update_last_curator is called before the work completes and an
    exception leaves a partially-updated ledger.
    """
    project_dir, turns = _minimal_curator_a_setup(tmp_path)

    # Seed a prior last_curator_a value so we can detect clobber.
    prior = _NOW - timedelta(days=7)
    wledger = WikiLedger(tmp_path, "private")
    wledger.write(WikiLedgerEntry(wiki="private", last_curator_a=prior))

    adapter = _fake_adapter(turns)

    # Mock classify_slice to raise — mid-run failure.
    from lore_curator import session_curator as curator_a_mod

    def boom(*_args, **_kwargs):
        raise RuntimeError("simulated mid-run failure")

    with patch.object(curator_a_mod, "classify_slice", boom):
        from lore_curator.session_curator import run_curator_a
        with pytest.raises(RuntimeError, match="simulated mid-run failure"):
            run_curator_a(
                lore_root=tmp_path,
                llm_client=_noteworthy_false_client(),
                adapter_lookup=lambda host: adapter if host == "fake" else None,
                now=_NOW,
            )

    after = wledger.read().last_curator_a
    # Contract: EITHER prior (unchanged) OR _NOW (atomic success). Not None.
    assert after is not None, "last_curator_a must NOT be cleared on failure"
    assert after in (prior, _NOW), (
        f"last_curator_a must be prior={prior} or new={_NOW}; got {after}"
    )


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
            host="fake",
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
