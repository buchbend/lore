"""Flag measurement — spine aggregation for `lore status`/`lore trace` (#360).

Fixture spine records only, written directly with SpineWriter (matching
test_trace_cmd.py's pattern) — no live sessions, no LLM. One test drives
the real flag.write/accept path to prove the pending counter reuses
flag.count_pending rather than reconstructing it from events.
"""

from __future__ import annotations

from pathlib import Path

from lore_core import flag
from lore_core.flag_metrics import FlagCounts, flag_counts, flag_events, review_latency_seconds
from lore_core.spine import SpineWriter


def _emit(lore_root: Path, *, event: str, wiki: str, **data) -> None:
    SpineWriter(lore_root).emit(source=flag.SPINE_SOURCE, event=event, wiki=wiki, data=data)


def _vault(tmp_path: Path) -> Path:
    root = tmp_path / "vault"
    (root / ".lore").mkdir(parents=True)
    (root / "wiki" / "private").mkdir(parents=True)
    return root


# ---------------------------------------------------------------------------
# flag_counts — written/withheld/accepted/declined/retargeted from events,
# pending from flag.count_pending (never reconstructed).
# ---------------------------------------------------------------------------


def test_written_and_withheld_counted_separately(tmp_path: Path):
    root = _vault(tmp_path)
    _emit(root, event=flag.EV_WRITE, wiki="private", outcome="written", flag_id="a")
    _emit(root, event=flag.EV_WRITE, wiki="private", outcome="written", flag_id="b")
    _emit(
        root, event=flag.EV_WRITE, wiki="private", outcome="withheld", flag_id="c", category="email"
    )

    counts = flag_counts(root, "private")
    assert counts.written == 2
    assert counts.withheld == 1


def test_accepted_declined_retargeted_counted_from_verdicts(tmp_path: Path):
    root = _vault(tmp_path)
    _emit(root, event=flag.EV_REVIEW, wiki="private", verdict="accept", flag_id="a")
    _emit(root, event=flag.EV_REVIEW, wiki="private", verdict="accept", flag_id="b")
    _emit(root, event=flag.EV_REVIEW, wiki="private", verdict="decline", flag_id="c")
    _emit(root, event=flag.EV_REVIEW, wiki="private", verdict="retarget", flag_id="d", note="x.md")

    counts = flag_counts(root, "private")
    assert counts.accepted == 2
    assert counts.declined == 1
    assert counts.retargeted == 1


def test_counts_scoped_to_one_wiki(tmp_path: Path):
    root = _vault(tmp_path)
    _emit(root, event=flag.EV_WRITE, wiki="private", outcome="written", flag_id="a")
    _emit(root, event=flag.EV_WRITE, wiki="team", outcome="written", flag_id="b")

    assert flag_counts(root, "private").written == 1
    assert flag_counts(root, "team").written == 1


def test_pending_reuses_flag_count_pending_not_replayed_events(tmp_path: Path, monkeypatch):
    """Pending must come from the note scan (ADR 0008), not from counting
    write-minus-review events — a human-edited note or a note touched
    outside the flag module must still be reflected correctly.
    """
    root = tmp_path / "vault"
    monkeypatch.setenv("LORE_ROOT", str(root))
    monkeypatch.delenv("CLAUDE_SESSION_ID", raising=False)
    (root / "wiki" / "private" / "concepts").mkdir(parents=True)
    (root / ".lore").mkdir(parents=True)

    written = flag.write(
        "One fact.",
        wiki="private",
        target="concepts/topic.md",
        transcript="tr-1",
        author="claude",
        now="2026-08-05",
    )
    flag.write(
        "Another fact.",
        wiki="private",
        target="concepts/topic.md",
        transcript="tr-2",
        author="claude",
        now="2026-08-05",
    )
    flag.accept(root / "wiki" / "private", written.flag_id)

    counts = flag_counts(root, "private")
    assert counts.written == 2
    assert counts.pending == 1  # one accepted, one still unreviewed
    assert counts.pending == flag.count_pending(root / "wiki" / "private")


def test_flag_counts_is_a_frozen_dataclass_shape(tmp_path: Path):
    root = _vault(tmp_path)
    counts = flag_counts(root, "private")
    assert isinstance(counts, FlagCounts)
    assert counts == FlagCounts(
        written=0, withheld=0, pending=0, accepted=0, declined=0, retargeted=0
    )


# ---------------------------------------------------------------------------
# flag_events — chronological, optionally wiki-filtered
# ---------------------------------------------------------------------------


def test_flag_events_sorted_chronologically(tmp_path: Path):
    root = _vault(tmp_path)
    _emit(root, event=flag.EV_REVIEW, wiki="private", verdict="accept", flag_id="a")
    _emit(root, event=flag.EV_WRITE, wiki="private", outcome="written", flag_id="a")
    _rewrite_ts(root, ["2026-08-05T10:05:00Z", "2026-08-05T10:00:00Z"])

    events = flag_events(root)
    assert [e["event"] for e in events] == [flag.EV_WRITE, flag.EV_REVIEW]


def test_flag_events_filtered_by_wiki(tmp_path: Path):
    root = _vault(tmp_path)
    _emit(root, event=flag.EV_WRITE, wiki="private", outcome="written", flag_id="a")
    _emit(root, event=flag.EV_WRITE, wiki="team", outcome="written", flag_id="b")

    events = flag_events(root, wiki="private")
    assert len(events) == 1
    assert events[0]["data"]["flag_id"] == "a"


def test_flag_events_excludes_other_sources(tmp_path: Path):
    root = _vault(tmp_path)
    SpineWriter(root).emit(source="hook", event="session-start", wiki="private", data={})
    _emit(root, event=flag.EV_WRITE, wiki="private", outcome="written", flag_id="a")

    events = flag_events(root)
    assert len(events) == 1
    assert events[0]["source"] == flag.SPINE_SOURCE


def _rewrite_ts(root: Path, ts_list: list[str]) -> None:
    import json

    path = root / ".lore" / "spine.jsonl"
    lines = path.read_text().splitlines()
    out = []
    for line, ts in zip(lines, ts_list, strict=True):
        rec = json.loads(line)
        rec["ts"] = ts
        out.append(json.dumps(rec))
    path.write_text("\n".join(out) + "\n")


# ---------------------------------------------------------------------------
# review_latency_seconds — computable from one flag's write + verdict
# ---------------------------------------------------------------------------


def test_review_latency_computed_from_write_and_verdict(tmp_path: Path):
    root = _vault(tmp_path)
    _emit(root, event=flag.EV_WRITE, wiki="private", outcome="written", flag_id="a")
    _emit(root, event=flag.EV_REVIEW, wiki="private", verdict="accept", flag_id="a")
    _rewrite_ts(root, ["2026-08-05T10:00:00Z", "2026-08-05T10:30:00Z"])

    events = flag_events(root)
    assert review_latency_seconds(events, "a") == 1800.0


def test_review_latency_none_when_still_pending(tmp_path: Path):
    root = _vault(tmp_path)
    _emit(root, event=flag.EV_WRITE, wiki="private", outcome="written", flag_id="a")

    events = flag_events(root)
    assert review_latency_seconds(events, "a") is None


def test_review_latency_none_for_unknown_flag_id(tmp_path: Path):
    root = _vault(tmp_path)
    _emit(root, event=flag.EV_WRITE, wiki="private", outcome="written", flag_id="a")
    _emit(root, event=flag.EV_REVIEW, wiki="private", verdict="accept", flag_id="a")

    events = flag_events(root)
    assert review_latency_seconds(events, "no-such-id") is None
