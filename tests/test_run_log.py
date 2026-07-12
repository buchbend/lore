"""RunLogger emits curator run events onto the event spine (issue #189).

The archival ``runs/<id>.jsonl`` file and the ``runs-live.jsonl`` tee are gone;
every record is one ``source="curator"`` spine envelope keyed by ``run_id``.
"""

import re
from datetime import UTC, datetime
from pathlib import Path

import pytest
from lore_core.run_log import RunLogger, generate_run_id
from lore_core.run_reader import read_curator_runs
from lore_core.spine import read_spine, validate_envelope


def _events(lore_root: Path) -> list[dict]:
    return read_spine(lore_root, source="curator")


def _only_run(lore_root: Path) -> list[dict]:
    runs = list(read_curator_runs(lore_root).values())
    assert len(runs) == 1
    return runs[0]


def test_run_id_format():
    ts = datetime(2026, 4, 20, 14, 32, 5, tzinfo=UTC)
    run_id = generate_run_id(now=ts)
    assert re.fullmatch(r"2026-04-20T14-32-05-[a-z0-9]{6}", run_id), run_id


def test_run_id_uniqueness():
    ids = {generate_run_id() for _ in range(1000)}
    assert len(ids) == 1000


def test_run_start_and_end_land_on_the_spine(tmp_path: Path):
    with RunLogger(tmp_path, trigger="manual", pending_count=2) as logger:
        pass
    evs = _events(tmp_path)
    assert evs[0]["event"] == "run-start"
    assert evs[0]["source"] == "curator"
    assert evs[0]["run_id"] == logger.run_id
    assert evs[0]["data"]["trigger"] == "manual"
    assert evs[0]["data"]["pending_count"] == 2
    assert evs[-1]["event"] == "run-end"
    for e in evs:
        validate_envelope(e)  # closed source/level/error_code sets
    # No legacy files are written any more.
    assert not (tmp_path / ".lore" / "runs").exists()
    assert not (tmp_path / ".lore" / "runs-live.jsonl").exists()


def test_emit_counters_and_ordering(tmp_path: Path):
    with RunLogger(tmp_path, trigger="hook", pending_count=3) as logger:
        logger.emit("transcript-start", transcript_id="t1", new_turns=10)
        logger.emit("noteworthy", transcript_id="t1", verdict=True, reason="x", tier="middle")
        logger.emit(
            "session-note", transcript_id="t1", action="filed", path="p.md", wikilink="[[p]]"
        )
        logger.emit("transcript-start", transcript_id="t2", new_turns=5)
        logger.emit("skip", transcript_id="t2", reason="noteworthy-false")
    records = read_curator_runs(tmp_path)[logger.run_id]
    assert records[0]["type"] == "run-start"
    assert records[-1]["type"] == "run-end"
    assert records[-1]["notes_new"] == 1
    assert records[-1]["notes_merged"] == 0
    assert records[-1]["skipped"] == 1
    assert records[-1]["errors"] == 0
    kinds = [r["type"] for r in records[1:-1]]
    assert kinds == ["transcript-start", "noteworthy", "session-note", "transcript-start", "skip"]


def test_exception_emits_error_and_runend_then_propagates(tmp_path: Path):
    with pytest.raises(ValueError, match="boom"), RunLogger(tmp_path, trigger="hook") as logger:
        logger.emit("transcript-start", transcript_id="t1", new_turns=5)
        raise ValueError("boom")
    records = _only_run(tmp_path)
    types = [r["type"] for r in records]
    assert "error" in types
    assert types[-1] == "run-end"
    assert records[-1]["errors"] >= 1
    # The error record is emitted at error level on the spine.
    err = next(e for e in _events(tmp_path) if e["event"] == "error")
    assert err["level"] == "error"


def test_llm_records_carry_metadata_only(tmp_path: Path):
    """Full prompt/response text must never hit the spine — the O_APPEND
    atomicity budget (< PIPE_BUF) assumes small records."""
    with RunLogger(tmp_path, trigger="dry-run", trace_llm=True) as logger:
        logger.emit(
            "llm-prompt",
            call="noteworthy",
            tier="middle",
            token_count=100,
            messages=[{"role": "user", "content": "a very long secret prompt"}],
        )
        logger.emit("llm-response", call="noteworthy", token_count=5, body="a long body")
    evs = _events(tmp_path)
    prompt = next(e for e in evs if e["event"] == "llm-prompt")
    assert prompt["data"] == {"call": "noteworthy", "tier": "middle", "token_count": 100}
    assert "messages" not in prompt["data"]
    resp = next(e for e in evs if e["event"] == "llm-response")
    assert "body" not in resp["data"]
    assert resp["data"]["token_count"] == 5


def test_emit_serializes_non_json_native_types(tmp_path: Path):
    with RunLogger(tmp_path, trigger="hook") as logger:
        logger.emit(
            "warning",
            message="non-json",
            a_path=Path("/tmp/foo"),
            a_ts=datetime(2026, 1, 1, tzinfo=UTC),
        )
    warn = next(e for e in _events(tmp_path) if e["event"] == "warning")
    assert "/tmp/foo" in warn["data"]["a_path"]


def test_role_field_in_run_start_and_end(tmp_path: Path):
    with RunLogger(tmp_path, trigger="hook", role="b") as logger:
        logger.emit("skip", reason="empty")
    evs = _events(tmp_path)
    assert evs[0]["event"] == "run-start"
    assert evs[0]["data"]["role"] == "b"
    assert evs[-1]["event"] == "run-end"
    assert evs[-1]["data"]["role"] == "b"


def test_backward_compat_no_role_defaults_to_a(tmp_path: Path):
    with RunLogger(tmp_path, trigger="hook"):
        pass
    evs = _events(tmp_path)
    assert evs[0]["data"]["role"] == "a"
    assert evs[-1]["data"]["role"] == "a"


def test_hygiene_pass_counters(tmp_path: Path):
    """The `lore curator [--wiki] [--apply]` hygiene pass tags role="c" and
    counts action-applied / action-skipped."""
    with RunLogger(tmp_path, trigger="hook", role="c") as logger:
        logger.emit("wiki-start", wiki="private")
        logger.emit("action-applied", kind="review_stale", path="n.md", reason="90d")
        logger.emit("action-applied", kind="mark_superseded", path="m.md", reason="newer")
        logger.emit("action-skipped", path="x.md", reason="mtime changed")
    records = _only_run(tmp_path)
    end = records[-1]
    assert end["actions_applied"] == 2
    assert end["actions_skipped"] == 1
    assert "wiki-start" in [r["type"] for r in records[1:-1]]


def test_retired_record_types_downgraded_to_warning(tmp_path: Path):
    """Retired ambition record types fall back to 'warning' rather than being
    emitted verbatim."""
    retired_types = ["cluster-formed", "surface-filed", "defrag-pass", "wiki-skip"]
    with RunLogger(tmp_path, trigger="hook", role="a") as logger:
        for rt in retired_types:
            logger.emit(rt, detail="test")
    emitted = [e["event"] for e in _events(tmp_path)]
    for rt in retired_types:
        assert rt not in emitted
    assert emitted.count("warning") == len(retired_types)


def test_wiki_lifts_to_envelope_field(tmp_path: Path):
    with RunLogger(tmp_path, trigger="hook", role="c") as logger:
        logger.emit("wiki-start", wiki="private")
    ev = next(e for e in _events(tmp_path) if e["event"] == "wiki-start")
    assert ev["wiki"] == "private"


def test_emit_survives_unwritable_spine(tmp_path: Path, monkeypatch):
    """A failing spine write degrades to a marker; RunLogger never raises."""
    import os as _os

    real_open = _os.open

    def faulty(path, *a, **k):
        if str(path).endswith("spine.jsonl"):
            raise OSError("disk full")
        return real_open(path, *a, **k)

    monkeypatch.setattr(_os, "open", faulty)
    with RunLogger(tmp_path, trigger="hook") as logger:
        logger.emit("transcript-start", transcript_id="t1", new_turns=5)
    assert (tmp_path / ".lore" / "spine-failed.marker").exists()
