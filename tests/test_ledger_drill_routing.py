"""Owner-local drill routing: "which sessions touched PR / issue / file X".

The transcript ledger answers from its linkage blocks and returns
transcript pointers. Machine-local by design (ADR 0009) — nothing here
crosses to the team layer.
"""
from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest
from lore_core.ledger import TranscriptLedger, TranscriptLedgerEntry, find_sessions
from lore_core.spine import read_spine, validate_envelope


def _seed(lore_root: Path) -> None:
    def entry(tid: str, day: int, **linkage) -> TranscriptLedgerEntry:
        return TranscriptLedgerEntry(
            integration="claude-code",
            transcript_id=tid,
            path=lore_root / f"{tid}.jsonl",
            directory=lore_root / "proj",
            digested_hash=None,
            digested_index_hint=None,
            synthesised_hash=None,
            last_mtime=datetime(2026, 8, day, 9, 0, tzinfo=UTC),
            curator_a_run=None,
            noteworthy=None,
            session_note=None,
            linkage=linkage,
        )

    TranscriptLedger(lore_root).bulk_upsert([
        entry("t-ledger", 4, repo="buchbend/lore", branch="feat/358-ledger",
              issues=[358], prs=[364], files=["lib/lore_core/ledger.py"]),
        entry("t-flag", 3, repo="buchbend/lore", branch="feat/357-flag",
              issues=[357], prs=[], files=["lib/lore_core/publish_gate.py"]),
    ])


def test_query_naming_an_issue_returns_that_session_pointer(tmp_path: Path) -> None:
    _seed(tmp_path)

    hits = find_sessions(tmp_path, "what did we do on #357")

    assert [h["transcript_id"] for h in hits] == ["t-flag"]
    assert hits[0]["path"] == str(tmp_path / "t-flag.jsonl")
    assert hits[0]["integration"] == "claude-code"
    assert hits[0]["branch"] == "feat/357-flag"


def test_query_naming_a_pr_returns_that_session_pointer(tmp_path: Path) -> None:
    _seed(tmp_path)
    assert [h["transcript_id"] for h in find_sessions(tmp_path, "PR 364")] == ["t-ledger"]


def test_query_naming_a_file_returns_the_sessions_that_edited_it(tmp_path: Path) -> None:
    _seed(tmp_path)
    assert [h["transcript_id"] for h in find_sessions(tmp_path, "ledger.py")] == ["t-ledger"]


def test_query_with_no_routable_token_returns_nothing(tmp_path: Path) -> None:
    _seed(tmp_path)
    assert find_sessions(tmp_path, "retry semantics") == []


def test_a_routed_query_emits_exactly_one_read_side_spine_event(tmp_path: Path) -> None:
    """AC6: one event per ledger-backed query, on the shared envelope."""
    _seed(tmp_path)

    find_sessions(tmp_path, "#358")

    records = [r for r in read_spine(tmp_path) if r["event"] == "ledger-query"]
    assert len(records) == 1
    validate_envelope(records[0])
    assert records[0]["data"]["hits"] == 1


def test_an_unroutable_query_emits_no_event(tmp_path: Path) -> None:
    _seed(tmp_path)
    find_sessions(tmp_path, "retry semantics")
    assert [r for r in read_spine(tmp_path) if r["event"] == "ledger-query"] == []


def test_drill_returns_transcript_pointers_for_a_routed_query(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """AC6 at the tool surface: `lore_drill` carries the pointers."""
    from lore_mcp import server

    _seed(tmp_path)
    (tmp_path / "wiki" / "demo").mkdir(parents=True)
    monkeypatch.setenv("LORE_ROOT", str(tmp_path))
    monkeypatch.setattr(server, "handle_search", lambda **kw: [])

    out = server.handle_drill(query="#358", wiki="demo")

    assert [s["transcript_id"] for s in out["result"]["sessions"]] == ["t-ledger"]
    assert len([r for r in read_spine(tmp_path) if r["event"] == "ledger-query"]) == 1


def test_drill_cli_prints_the_transcript_pointers(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from lore_cli import drill_cmd
    from typer.testing import CliRunner

    _seed(tmp_path)
    monkeypatch.setattr(
        drill_cmd,
        "handle_drill",
        lambda **kw: {
            "trace": [],
            "result": {
                "notes": [],
                "sessions": [
                    {
                        "transcript_id": "t-ledger",
                        "integration": "claude-code",
                        "path": str(tmp_path / "t-ledger.jsonl"),
                        "directory": str(tmp_path / "proj"),
                        "last_active": "2026-08-04T09:00:00+00:00",
                        "repo": "buchbend/lore",
                        "branch": "feat/358-ledger",
                        "issues": [358],
                        "prs": [364],
                    }
                ],
            },
        },
    )

    out = CliRunner().invoke(drill_cmd.app, ["#358"]).output

    assert "t-ledger" in out
    assert "feat/358-ledger" in out
    assert "2026-08-04" in out


def test_prose_abbreviations_do_not_route_to_the_ledger(tmp_path: Path) -> None:
    """"e.g." is not a filename. A single-letter suffix must not cost a
    ledger parse and a spine event on an ordinary topical query."""
    _seed(tmp_path)

    assert find_sessions(tmp_path, "the retry policy, e.g. the backoff") == []
    assert [r for r in read_spine(tmp_path) if r["event"] == "ledger-query"] == []
