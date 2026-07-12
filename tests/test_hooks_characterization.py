"""Characterization tests for hook-adjacent logic that had no direct coverage.

Written against the pre-decomposition implementation and confirmed green there
first, so they pin the *original* behaviour rather than ratifying a refactor.
Kept as the standing contract for the extracted modules.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from lore_core.ledger import TranscriptLedgerEntry
from lore_core.wiki_config import WikiConfig

# --- under test -------------------------------------------------------------
from lore_cli.hooks import (
    _format_drain_summary as format_drain_summary,
    _maybe_auto_pull_for_scope as maybe_auto_pull_for_scope,
    _tally_drain as tally_drain,
    _wiki_should_spawn as wiki_should_spawn,
    _wiki_suffix as wiki_suffix,
    collect_session_facts,
)


NOW = datetime(2026, 5, 1, 12, 0, tzinfo=UTC)


def _entry(*, turns: int, digested: int | None, age_s: int) -> TranscriptLedgerEntry:
    return TranscriptLedgerEntry(
        integration="claude-code",
        transcript_id=f"t{turns}-{age_s}",
        path=Path("/tmp/t.jsonl"),
        directory=Path("/tmp"),
        digested_hash=None,
        digested_index_hint=digested,
        synthesised_hash=None,
        last_mtime=NOW - timedelta(seconds=age_s),
        curator_a_run=None,
        noteworthy=None,
        session_note=None,
        total_turns=turns,
    )


def _cfg(*, threshold: int = 30, max_age_s: int = 600) -> WikiConfig:
    cfg = WikiConfig()
    cfg.curator.threshold_pending_turns = threshold
    cfg.curator.max_pending_age_s = max_age_s
    return cfg


# ---------------------------------------------------------------------------
# spawn gate — the OR-gate every curator-A spawn routes through
# ---------------------------------------------------------------------------


def test_spawn_gate_empty_bucket_never_spawns() -> None:
    assert wiki_should_spawn([], _cfg(), now=NOW) == (False, "empty")


def test_spawn_gate_turns_threshold_crossed() -> None:
    entries = [_entry(turns=40, digested=5, age_s=10)]  # 35 new turns
    should, reason = wiki_should_spawn(entries, _cfg(threshold=30), now=NOW)
    assert should is True
    assert reason == "turns:35>=30"


def test_spawn_gate_turns_sum_across_entries() -> None:
    entries = [
        _entry(turns=20, digested=None, age_s=10),
        _entry(turns=15, digested=5, age_s=11),
    ]  # 20 + 10 = 30
    should, reason = wiki_should_spawn(entries, _cfg(threshold=30), now=NOW)
    assert should is True
    assert reason == "turns:30>=30"


def test_spawn_gate_digested_hint_beyond_total_turns_clamps_at_zero() -> None:
    """A digest hint ahead of total_turns must not subtract from siblings."""
    entries = [
        _entry(turns=5, digested=99, age_s=10),   # would be -94 unclamped
        _entry(turns=29, digested=None, age_s=10),
    ]
    should, reason = wiki_should_spawn(entries, _cfg(threshold=30), now=NOW)
    assert should is False
    assert reason == "under(turns=29,age=10s)"


def test_spawn_gate_age_fallback_fires_below_turns_threshold() -> None:
    entries = [_entry(turns=1, digested=None, age_s=700)]
    should, reason = wiki_should_spawn(entries, _cfg(max_age_s=600), now=NOW)
    assert should is True
    assert reason == "age:700s>=600s"


def test_spawn_gate_age_uses_oldest_entry() -> None:
    entries = [
        _entry(turns=1, digested=None, age_s=5),
        _entry(turns=1, digested=None, age_s=900),
    ]
    should, _ = wiki_should_spawn(entries, _cfg(max_age_s=600), now=NOW)
    assert should is True


def test_spawn_gate_under_both_reports_both_inputs() -> None:
    entries = [_entry(turns=3, digested=None, age_s=42)]
    should, reason = wiki_should_spawn(entries, _cfg(), now=NOW)
    assert should is False
    assert reason == "under(turns=3,age=42s)"


# ---------------------------------------------------------------------------
# drain tally / summary rendering
# ---------------------------------------------------------------------------


class _Ev:
    def __init__(self, event: str, wiki: str | None = None, wikilink: str | None = None):
        self.event = event
        self.wiki = wiki
        self.data = {"wikilink": wikilink} if wikilink else {}


def test_tally_drain_counts_by_event_name() -> None:
    events = [_Ev("note-filed"), _Ev("note-filed"), _Ev("note-appended")]
    assert tally_drain(events) == {"note-filed": 2, "note-appended": 1}


def test_summary_single_note_uses_its_wikilink() -> None:
    events = [_Ev("note-filed", wiki="w", wikilink="[[a]]")]
    assert format_drain_summary(tally_drain(events), events) == "new note [[a]]"


def test_summary_plural_notes_use_wiki_suffix() -> None:
    events = [_Ev("note-filed", wiki="w"), _Ev("note-filed", wiki="w")]
    assert format_drain_summary(tally_drain(events), events) == "2 new notes in w"


def test_summary_joins_filed_appended_and_surface_with_middots() -> None:
    events = [
        _Ev("note-filed", wiki="w", wikilink="[[a]]"),
        _Ev("note-appended", wiki="w", wikilink="[[b]]"),
        _Ev("surface-proposed", wiki="w"),
    ]
    assert format_drain_summary(tally_drain(events), events) == (
        "new note [[a]] · added to [[b]] · 1 surface proposed in w"
    )


def test_summary_ignores_unknown_event_kinds() -> None:
    events = [_Ev("transcript-synced", wiki="w")]
    assert format_drain_summary(tally_drain(events), events) == ""


def test_wiki_suffix_multi_wiki_orders_by_count_then_name() -> None:
    events = [
        _Ev("note-filed", wiki="b"),
        _Ev("note-filed", wiki="a"),
        _Ev("note-filed", wiki="a"),
    ]
    assert wiki_suffix(events, "note-filed") == " (2 in a, 1 in b)"


def test_wiki_suffix_empty_when_any_event_lacks_a_wiki_tag() -> None:
    """Legacy/migration rows without a wiki must not produce a partial breakdown."""
    events = [_Ev("note-filed", wiki="a"), _Ev("note-filed", wiki=None)]
    assert wiki_suffix(events, "note-filed") == ""


# ---------------------------------------------------------------------------
# session facts collection
# ---------------------------------------------------------------------------


def _wiki_with_session(tmp_path: Path, *, title: str) -> Path:
    wiki = tmp_path / "wiki" / "demo"
    sess = wiki / "sessions" / "2026" / "05"
    sess.mkdir(parents=True)
    (sess / "01-0900-a.md").write_text(
        f"---\ntype: session\ntitle: {title}\n---\n\nbody\n"
    )
    return wiki


def test_collect_session_facts_picks_up_scope_and_latest_session(tmp_path: Path) -> None:
    wiki = _wiki_with_session(tmp_path, title="did a thing")
    facts = collect_session_facts(wiki, None, scope="demo:proj")
    assert facts.wiki_name == "demo"
    assert facts.repo is None
    assert facts.scope == "demo:proj"
    assert facts.project_entry is None
    assert facts.session_hints == (("01-0900-a", "did a thing"),)


def test_collect_session_facts_resolves_project_note_by_repo(tmp_path: Path) -> None:
    wiki = _wiki_with_session(tmp_path, title="t")
    (wiki / "_catalog.json").write_text(
        '{"sections": {"projects": [{"name": "proj", "description": "d",'
        ' "repos": ["org/repo"]}]}}'
    )
    facts = collect_session_facts(wiki, "org/repo")
    assert facts.project_entry is not None
    assert facts.project_entry["name"] == "proj"


def test_collect_session_facts_caps_hints_at_two(tmp_path: Path) -> None:
    wiki = _wiki_with_session(tmp_path, title="t")
    sess = wiki / "sessions" / "2026" / "05"
    for i in range(2, 6):
        (sess / f"0{i}-0900-s{i}.md").write_text(
            f"---\ntype: session\ntitle: t{i}\n---\n\nb\n"
        )
    facts = collect_session_facts(wiki, None)
    assert len(facts.session_hints) == 2
    # newest first
    assert facts.session_hints[0][0] == "05-0900-s5"


def test_collect_session_facts_on_empty_wiki(tmp_path: Path) -> None:
    wiki = tmp_path / "wiki" / "demo"
    wiki.mkdir(parents=True)
    facts = collect_session_facts(wiki, None)
    assert facts.session_hints == ()
    assert facts.project_entry is None
    assert facts.pending_chip is None


# ---------------------------------------------------------------------------
# auto-pull warning routing
# ---------------------------------------------------------------------------


class _Scope:
    wiki = "demo"


@pytest.mark.parametrize(
    ("status", "expected_fragment"),
    [
        ("SKIPPED_DIRTY", "uncommitted changes"),
        ("SKIPPED_DIVERGED", "diverged from origin"),
        ("OK", None),
        ("SKIPPED_UNREACHABLE", None),
    ],
)
def test_auto_pull_warns_only_on_dirty_and_diverged(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, status: str, expected_fragment
) -> None:
    from lore_core import git_sync

    wiki_dir = tmp_path / "wiki" / "demo"
    wiki_dir.mkdir(parents=True)

    result = git_sync.SyncResult(status=getattr(git_sync.SyncStatus, status))
    monkeypatch.setattr(git_sync, "auto_pull", lambda _d: result)

    warning = maybe_auto_pull_for_scope(_Scope(), tmp_path)
    if expected_fragment is None:
        assert warning is None
    else:
        assert warning is not None
        assert expected_fragment in warning


def test_auto_pull_skipped_when_wiki_dir_missing(tmp_path: Path) -> None:
    assert maybe_auto_pull_for_scope(_Scope(), tmp_path) is None
