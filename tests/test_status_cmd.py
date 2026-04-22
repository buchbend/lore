"""Task 11: lore status — activity-first liveness surface.

UX-approved output shape (decay-first):
    lore: active · private/proj:test · attached at <scope_root>

      · Last note    [[...]] · 18h ago
      · Last run     2h ago · 0 notes from 3 transcripts
      · Pending      2 transcripts
      · Session      loaded 4m ago · /lore:loaded
      · Lock         free

Loud-on-earning alerts only when thresholds are crossed:
    ! last 2 runs (abc123, def456) filed 0 notes — lore runs show abc123
    x last note filed 4d ago — lore runs show latest
    x hook log write failed 2h ago — check disk / permissions
    ! simple-tier fallback active — high tier unavailable

No --plumbing flag (dropped per UX + merciless — doctor owns install).
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from typer.testing import CliRunner

from lore_cli.status_cmd import app


runner = CliRunner()

_NOW = datetime(2026, 4, 21, 12, 0, 0, tzinfo=UTC)


def _iso(dt: datetime) -> str:
    return dt.isoformat().replace("+00:00", "Z")


def _seed_vault(tmp_path: Path) -> tuple[Path, Path]:
    """Returns (lore_root, attached_project_dir)."""
    from datetime import UTC, datetime as _dt
    from lore_core.state.attachments import Attachment, AttachmentsFile

    lore_root = tmp_path / "vault"
    (lore_root / ".lore").mkdir(parents=True)
    (lore_root / "wiki" / "private" / "sessions").mkdir(parents=True)

    project = tmp_path / "project"
    project.mkdir()
    af = AttachmentsFile(lore_root); af.load()
    af.add(Attachment(
        path=project, wiki="private", scope="proj:test",
        attached_at=_dt.now(UTC), source="manual",
    ))
    af.save()
    return lore_root, project


def _seed_happy_run(lore_root: Path, *, ago: timedelta, notes_new: int) -> str:
    from lore_core.ledger import WikiLedger
    run_ts = _NOW - ago
    WikiLedger(lore_root, "private").update_last_curator("a", at=run_ts)

    runs_dir = lore_root / ".lore" / "runs"
    runs_dir.mkdir(parents=True, exist_ok=True)
    stem = run_ts.strftime("%Y-%m-%dT%H-%M-%S") + "-abc123"
    p = runs_dir / f"{stem}.jsonl"
    records = [
        {"type": "run-start", "ts": _iso(run_ts), "schema_version": 1},
    ]
    if notes_new > 0:
        records.append({
            "type": "session-note",
            "ts": _iso(run_ts),
            "action": "filed",
            "wikilink": "[[2026-04-21-my-note]]",
        })
    records.append({"type": "run-end", "ts": _iso(run_ts), "notes_new": notes_new, "errors": 0})
    p.write_text("\n".join(json.dumps(r) for r in records) + "\n")
    return "abc123"


def _invoke(lore_root: Path, cwd: Path | None, *extra: str, monkeypatch) -> str:
    monkeypatch.setenv("LORE_ROOT", str(lore_root))
    args = list(extra)
    if cwd is not None:
        args += ["--cwd", str(cwd)]
    # Inject deterministic "now" via env var the command reads.
    monkeypatch.setenv("_LORE_STATUS_NOW", _iso(_NOW))
    result = runner.invoke(app, args, catch_exceptions=False)
    return result.output


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


def test_status_happy_path_line_count(tmp_path: Path, monkeypatch) -> None:
    lore_root, project = _seed_vault(tmp_path)
    _seed_happy_run(lore_root, ago=timedelta(hours=2), notes_new=1)

    out = _invoke(lore_root, project, monkeypatch=monkeypatch)
    # 8 newlines on happy path (1 header + 1 blank + 6 body lines = 8 '\n').
    # 6th body line = Hook (added for capture-hook liveness surface).
    assert out.count("\n") == 8, f"expected 8 newlines on happy path; got {out.count(chr(10))}:\n{out!r}"


def test_status_happy_path_no_alert_glyphs(tmp_path: Path, monkeypatch) -> None:
    lore_root, project = _seed_vault(tmp_path)
    _seed_happy_run(lore_root, ago=timedelta(hours=2), notes_new=1)

    out = _invoke(lore_root, project, monkeypatch=monkeypatch)
    assert "!" not in out
    assert "x " not in out


def test_status_line_order_is_decay_first(tmp_path: Path, monkeypatch) -> None:
    lore_root, project = _seed_vault(tmp_path)
    _seed_happy_run(lore_root, ago=timedelta(hours=2), notes_new=1)

    out = _invoke(lore_root, project, monkeypatch=monkeypatch)
    note_idx = out.find("Last note")
    run_idx = out.find("Last run")
    hook_idx = out.find("Hook")
    pending_idx = out.find("Pending")
    session_idx = out.find("Session")
    lock_idx = out.find("Lock")
    # Hook sits between "Last run" (effect) and "Pending" (hook's input queue):
    # note ← run ← hook ← pending ← session ← lock.
    assert 0 < note_idx < run_idx < hook_idx < pending_idx < session_idx < lock_idx, (
        f"decay-first order violated: {out!r}"
    )


def test_status_first_line_shows_scope(tmp_path: Path, monkeypatch) -> None:
    lore_root, project = _seed_vault(tmp_path)
    _seed_happy_run(lore_root, ago=timedelta(hours=2), notes_new=1)

    out = _invoke(lore_root, project, monkeypatch=monkeypatch)
    first_line = out.splitlines()[0]
    assert "lore" in first_line
    assert "private/proj:test" in first_line


# ---------------------------------------------------------------------------
# Loud-on-earning alerts
# ---------------------------------------------------------------------------


def test_status_zero_notes_alert(tmp_path: Path, monkeypatch) -> None:
    """Two consecutive 0-note runs → yellow alert line with run IDs."""
    lore_root, project = _seed_vault(tmp_path)
    from lore_core.ledger import WikiLedger

    runs_dir = lore_root / ".lore" / "runs"
    runs_dir.mkdir(parents=True, exist_ok=True)
    for i, suffix in enumerate(["aaa111", "bbb222"]):
        run_ts = _NOW - timedelta(hours=3 - i)
        stem = run_ts.strftime("%Y-%m-%dT%H-%M-%S") + f"-{suffix}"
        (runs_dir / f"{stem}.jsonl").write_text(
            json.dumps({"type": "run-start", "ts": _iso(run_ts), "schema_version": 1}) + "\n"
            + json.dumps({"type": "run-end", "ts": _iso(run_ts), "notes_new": 0, "errors": 0}) + "\n"
        )
    WikiLedger(lore_root, "private").update_last_curator("a", at=_NOW - timedelta(hours=2))

    out = _invoke(lore_root, project, monkeypatch=monkeypatch)
    assert "!" in out, f"expected yellow alert on repeated zero-notes; got:\n{out}"
    assert "0 notes" in out


def test_status_stale_note_red_at_4d(tmp_path: Path, monkeypatch) -> None:
    """Last note 4 days ago → red alert (>3d threshold)."""
    lore_root, project = _seed_vault(tmp_path)
    run_ts = _NOW - timedelta(days=4)
    runs_dir = lore_root / ".lore" / "runs"
    runs_dir.mkdir(parents=True, exist_ok=True)
    stem = run_ts.strftime("%Y-%m-%dT%H-%M-%S") + "-xxx999"
    (runs_dir / f"{stem}.jsonl").write_text(
        "\n".join(
            json.dumps(r)
            for r in [
                {"type": "run-start", "ts": _iso(run_ts), "schema_version": 1},
                {"type": "session-note", "ts": _iso(run_ts), "action": "filed", "wikilink": "[[stale]]"},
                {"type": "run-end", "ts": _iso(run_ts), "notes_new": 1, "errors": 0},
            ]
        ) + "\n"
    )

    out = _invoke(lore_root, project, monkeypatch=monkeypatch)
    # Red glyph marks the line; also an alert block at bottom.
    assert "x " in out, f"expected red glyph for >3d note; got:\n{out}"


def test_status_hook_log_failed_red(tmp_path: Path, monkeypatch) -> None:
    lore_root, project = _seed_vault(tmp_path)
    _seed_happy_run(lore_root, ago=timedelta(hours=1), notes_new=1)
    marker = lore_root / ".lore" / "hook-log-failed.marker"
    marker.touch()

    out = _invoke(lore_root, project, monkeypatch=monkeypatch)
    assert "hook log" in out.lower()
    assert "x " in out


def test_status_hook_body_line_renders_recent_event(tmp_path: Path, monkeypatch) -> None:
    """Healthy path: Hook body line shows most recent hook-event summary."""
    lore_root, project = _seed_vault(tmp_path)
    _seed_happy_run(lore_root, ago=timedelta(hours=2), notes_new=1)
    events = lore_root / ".lore" / "hook-events.jsonl"
    events.write_text(
        json.dumps({
            "ts": _iso(_NOW - timedelta(minutes=12)),
            "event": "session-start",
            "outcome": "spawned-curator",
        }) + "\n"
    )

    out = _invoke(lore_root, project, monkeypatch=monkeypatch)
    # Line shape: "  · Hook         12m ago · session-start · spawned-curator"
    assert "Hook" in out
    assert "session-start" in out
    assert "spawned-curator" in out


def test_status_hook_body_line_dash_when_no_events(tmp_path: Path, monkeypatch) -> None:
    """Fresh vault with no hook-events.jsonl → Hook line shows dash, no alert."""
    lore_root, project = _seed_vault(tmp_path)
    _seed_happy_run(lore_root, ago=timedelta(hours=2), notes_new=1)

    out = _invoke(lore_root, project, monkeypatch=monkeypatch)
    # 8-line happy path; no alert because pending=0.
    assert "Hook" in out
    assert "!" not in out
    assert "no hook events" not in out


def test_status_hook_alert_when_pending_but_hook_stale(tmp_path: Path, monkeypatch) -> None:
    """Pending transcripts exist AND hook events are missing/stale → alert.

    This is the exact diagnostic: "something wants capturing, but capture
    hook isn't leaving traces." Without the alert, the user sees only
    empty runs and has to guess why.
    """
    from lore_core.ledger import TranscriptLedger, TranscriptLedgerEntry

    lore_root, project = _seed_vault(tmp_path)
    _seed_happy_run(lore_root, ago=timedelta(hours=2), notes_new=1)

    # Seed pending transcript.
    ledger = TranscriptLedger(lore_root)
    ledger.upsert(
        TranscriptLedgerEntry(
            host="claude-code",
            transcript_id="t1",
            path=project / "t1.jsonl",
            directory=project,
            digested_hash=None,  # pending
            digested_index_hint=None,
            synthesised_hash=None,
            last_mtime=_NOW - timedelta(hours=1),
            curator_a_run=None,
            noteworthy=None,
            session_note=None,
        )
    )
    # No hook-events.jsonl at all → stale/missing.

    out = _invoke(lore_root, project, monkeypatch=monkeypatch)
    assert "!" in out, f"expected alert line; got:\n{out}"
    assert "no hook events" in out.lower()


def test_status_hook_alert_when_pending_and_hook_events_all_old(tmp_path: Path, monkeypatch) -> None:
    """Same alert when hook-events exist but the newest is > 24h stale."""
    from lore_core.ledger import TranscriptLedger, TranscriptLedgerEntry

    lore_root, project = _seed_vault(tmp_path)
    _seed_happy_run(lore_root, ago=timedelta(hours=2), notes_new=1)

    ledger = TranscriptLedger(lore_root)
    ledger.upsert(
        TranscriptLedgerEntry(
            host="claude-code", transcript_id="t2",
            path=project / "t2.jsonl", directory=project,
            digested_hash=None, digested_index_hint=None,
            synthesised_hash=None,
            last_mtime=_NOW - timedelta(hours=1),
            curator_a_run=None, noteworthy=None, session_note=None,
        )
    )
    (lore_root / ".lore" / "hook-events.jsonl").write_text(
        json.dumps({
            "ts": _iso(_NOW - timedelta(days=2)),
            "event": "session-start",
            "outcome": "below-threshold",
        }) + "\n"
    )

    out = _invoke(lore_root, project, monkeypatch=monkeypatch)
    assert "no hook events" in out.lower()


def test_status_no_hook_alert_when_pending_zero(tmp_path: Path, monkeypatch) -> None:
    """No pending work → no alert even if hook-events is missing.

    Avoids crying wolf on fresh vaults where nothing needs capturing yet.
    """
    lore_root, project = _seed_vault(tmp_path)
    _seed_happy_run(lore_root, ago=timedelta(hours=2), notes_new=1)

    out = _invoke(lore_root, project, monkeypatch=monkeypatch)
    assert "no hook events" not in out.lower()


def test_status_simple_tier_fallback_yellow(tmp_path: Path, monkeypatch) -> None:
    lore_root, project = _seed_vault(tmp_path)
    _seed_happy_run(lore_root, ago=timedelta(hours=1), notes_new=1)
    (lore_root / ".lore" / "warnings.log").write_text("simple-tier-fallback\n")

    out = _invoke(lore_root, project, monkeypatch=monkeypatch)
    assert "simple-tier" in out.lower() or "simple tier" in out.lower()
    assert "!" in out


# ---------------------------------------------------------------------------
# Unattached cwd — exact UX-approved copy
# ---------------------------------------------------------------------------


def test_status_unattached_cwd(tmp_path: Path, monkeypatch) -> None:
    lore_root = tmp_path / "vault"
    (lore_root / ".lore").mkdir(parents=True)
    (lore_root / "wiki" / "private").mkdir(parents=True)
    unrelated = tmp_path / "elsewhere"
    unrelated.mkdir()

    out = _invoke(lore_root, unrelated, monkeypatch=monkeypatch)
    assert "not attached here" in out
    assert "/lore:attach" in out
    assert "Configured vaults" in out
    assert "private" in out


# ---------------------------------------------------------------------------
# --json mode
# ---------------------------------------------------------------------------


def test_status_json_mode(tmp_path: Path, monkeypatch) -> None:
    lore_root, project = _seed_vault(tmp_path)
    _seed_happy_run(lore_root, ago=timedelta(hours=2), notes_new=1)

    out = _invoke(lore_root, project, "--json", monkeypatch=monkeypatch)
    data = json.loads(out)
    assert "scope_name" in data
    assert "curators" in data
    assert data["scope_name"] == "private/proj:test"
    roles = [c["role"] for c in data["curators"]]
    assert roles == ["a", "b", "c"]


# ---------------------------------------------------------------------------
# --help shows a short description so the user discovers the command
# ---------------------------------------------------------------------------


def test_status_help_mentions_activity() -> None:
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    assert "activity" in result.output.lower() or "status" in result.output.lower()
