"""`lore trace` (#192) — correlated drill-down of one flush.

Golden-output tests for --plain / --json pin the rendering; the fixtures
write spine records and flush records directly (the pattern used by
test_status_cmd.py / test_log_cmd.py) rather than driving the full
pipeline, since the reader is the thing under test here.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from lore_cli.trace_cmd import app
from lore_core.flush_store import ErrorCode, FlushState, FlushStore
from lore_core.spine import SpineWriter
from typer.testing import CliRunner

runner = CliRunner()


def _lore_root(tmp_path: Path) -> Path:
    root = tmp_path / "vault"
    (root / ".lore").mkdir(parents=True)
    return root


def _emit(lore_root: Path, *, source: str, event: str, **kw) -> None:
    SpineWriter(lore_root).emit(source=source, event=event, **kw)


def _invoke(lore_root: Path, *args: str, monkeypatch) -> object:
    monkeypatch.setenv("LORE_ROOT", str(lore_root))
    return runner.invoke(app, list(args), catch_exceptions=False)


# ---------------------------------------------------------------------------
# AC1 — all five selectors
# ---------------------------------------------------------------------------


def test_exact_trace_id_selector(tmp_path, monkeypatch):
    lore_root = _lore_root(tmp_path)
    trace_id = "trace-exact-1"
    _emit(lore_root, source="curator", event="run-start", trace_id=trace_id, run_id="r1")
    _emit(lore_root, source="curator", event="run-end", trace_id=trace_id, run_id="r1")

    result = _invoke(lore_root, trace_id, "--plain", monkeypatch=monkeypatch)
    assert result.exit_code == 0
    assert f"trace {trace_id}" in result.output
    assert "curator/run-start" in result.output
    assert "curator/run-end" in result.output


def test_session_id_selector_returns_all_flushes_of_session(tmp_path, monkeypatch):
    lore_root = _lore_root(tmp_path)
    sid = "sess-shared"
    _emit(
        lore_root,
        source="drain",
        event="note-filed",
        trace_id="trace-A",
        session_id=sid,
        wiki="private",
        data={"path": "wiki/private/sessions/a.md"},
    )
    _emit(
        lore_root,
        source="drain",
        event="note-appended",
        trace_id="trace-B",
        session_id=sid,
        wiki="private",
        data={"path": "wiki/private/sessions/b.md"},
    )
    # A third, unrelated flush from a different session must not appear.
    _emit(
        lore_root,
        source="drain",
        event="note-filed",
        trace_id="trace-C",
        session_id="sess-other",
        wiki="private",
    )

    result = _invoke(lore_root, sid, "--plain", monkeypatch=monkeypatch)
    assert result.exit_code == 0
    assert "trace-A" in result.output
    assert "trace-B" in result.output
    assert "trace-C" not in result.output


def test_last_selector_picks_most_recently_updated_flush(tmp_path, monkeypatch):
    lore_root = _lore_root(tmp_path)
    store = FlushStore(lore_root)
    older = store.begin("older-buffer", wiki="private", trace_id="trace-older")
    store.transition(older, FlushState.RUNNING)
    store.transition(older, FlushState.PUBLISHED)
    newer = store.begin("newer-buffer", wiki="private", trace_id="trace-newer")
    store.transition(newer, FlushState.RUNNING)

    result = _invoke(lore_root, "last", "--plain", monkeypatch=monkeypatch)
    assert result.exit_code == 0
    assert "trace-newer" in result.output
    assert "trace-older" not in result.output


def test_dead_selector_lists_dead_lettered_newest_first(tmp_path, monkeypatch):
    lore_root = _lore_root(tmp_path)
    store = FlushStore(lore_root)

    first = store.begin("buf-1", wiki="private", trace_id="trace-dead-1")
    store.transition(first, FlushState.RUNNING)
    store.transition(first, FlushState.DEAD_LETTERED, reason=ErrorCode.COMPOSE_FAILED)

    second = store.begin("buf-2", wiki="private", trace_id="trace-dead-2")
    store.transition(second, FlushState.RUNNING)
    store.transition(second, FlushState.DEAD_LETTERED, reason=ErrorCode.SPAWN_FAILED)

    # A published flush must not show up in the dead list.
    ok = store.begin("buf-3", wiki="private", trace_id="trace-ok")
    store.transition(ok, FlushState.RUNNING)
    store.transition(ok, FlushState.PUBLISHED)

    result = _invoke(lore_root, "dead", "--plain", monkeypatch=monkeypatch)
    assert result.exit_code == 0
    first_pos = result.output.index("trace-dead-2")
    second_pos = result.output.index("trace-dead-1")
    assert first_pos < second_pos, "newest dead-letter must render first"
    assert "trace-ok" not in result.output


def test_note_selector_resolves_via_frontmatter_linkage(tmp_path, monkeypatch):
    lore_root = _lore_root(tmp_path)
    trace_id = "trace-from-note"
    _emit(lore_root, source="curator", event="run-start", trace_id=trace_id, run_id="r1")

    note_dir = lore_root / "wiki" / "private" / "sessions"
    note_dir.mkdir(parents=True)
    note_path = note_dir / "2026-05-01-example.md"
    note_path.write_text(
        "---\n"
        "schema_version: 1\n"
        "type: session\n"
        "linkage:\n"
        f"  trace_id: {trace_id}\n"
        "---\n"
        "Disclaimer.\n"
    )

    by_path = _invoke(lore_root, str(note_path), "--plain", monkeypatch=monkeypatch)
    assert by_path.exit_code == 0
    assert trace_id in by_path.output

    by_wikilink = _invoke(lore_root, "[[2026-05-01-example]]", "--plain", monkeypatch=monkeypatch)
    assert by_wikilink.exit_code == 0
    assert trace_id in by_wikilink.output


def test_unknown_selector_errors_without_crashing(tmp_path, monkeypatch):
    lore_root = _lore_root(tmp_path)
    result = _invoke(lore_root, "no-such-thing", monkeypatch=monkeypatch)
    assert result.exit_code == 1


# ---------------------------------------------------------------------------
# AC2 — header is the current flush status; steps carry model/tokens, note path
# ---------------------------------------------------------------------------


def test_header_shows_current_flush_status(tmp_path, monkeypatch):
    lore_root = _lore_root(tmp_path)
    store = FlushStore(lore_root)
    rec = store.begin("buf-status", wiki="private", trace_id="trace-status")
    store.transition(rec, FlushState.RUNNING)

    result = _invoke(lore_root, "trace-status", "--plain", monkeypatch=monkeypatch)
    assert "-- running" in result.output


def test_llm_step_shows_model_and_tokens(tmp_path, monkeypatch):
    lore_root = _lore_root(tmp_path)
    trace_id = "trace-llm"
    _emit(
        lore_root,
        source="curator",
        event="llm-response",
        trace_id=trace_id,
        run_id="r1",
        data={"model": "claude-opus-4-8", "token_count": 512},
    )

    result = _invoke(lore_root, trace_id, "--plain", monkeypatch=monkeypatch)
    assert "model=claude-opus-4-8" in result.output
    assert "tokens=512" in result.output


# ---------------------------------------------------------------------------
# AC3 — golden --plain / --json
# ---------------------------------------------------------------------------


def test_golden_plain_output(tmp_path, monkeypatch):
    lore_root = _lore_root(tmp_path)
    trace_id = "trace-golden"
    _emit(lore_root, source="hook", event="capture", trace_id=trace_id, data={})
    _emit(
        lore_root,
        source="curator",
        event="run-start",
        trace_id=trace_id,
        run_id="r1",
        data={},
    )
    _emit(
        lore_root,
        source="drain",
        event="note-filed",
        trace_id=trace_id,
        session_id="s1",
        data={"path": "wiki/private/sessions/x.md"},
    )
    # SpineWriter always stamps ``ts`` itself — overwrite the file directly
    # so the fixture controls timestamps (needed for a deterministic golden).
    _rewrite_timestamps(
        lore_root,
        {
            0: "2026-05-01T10:00:00Z",
            1: "2026-05-01T10:00:01Z",
            2: "2026-05-01T10:00:03Z",
        },
    )

    result = _invoke(lore_root, trace_id, "--plain", monkeypatch=monkeypatch)
    expected = (
        "trace trace-golden -- unknown\n"
        "    hook/capture  +1.0s\n"
        "    curator/run-start  +2.0s\n"
        "    drain/note-filed -> wiki/private/sessions/x.md"
    )
    assert result.output.strip() == expected


def _rewrite_timestamps(lore_root: Path, ts_by_line: dict) -> None:
    spine = lore_root / ".lore" / "spine.jsonl"
    lines = spine.read_text().splitlines()
    out = []
    for i, line in enumerate(lines):
        rec = json.loads(line)
        if i in ts_by_line:
            rec["ts"] = ts_by_line[i]
        out.append(json.dumps(rec))
    spine.write_text("\n".join(out) + "\n")


def test_golden_json_output(tmp_path, monkeypatch):
    lore_root = _lore_root(tmp_path)
    trace_id = "trace-json"
    _emit(lore_root, source="curator", event="run-start", trace_id=trace_id, run_id="r1")
    _emit(lore_root, source="curator", event="run-end", trace_id=trace_id, run_id="r1")

    result = _invoke(lore_root, trace_id, "--json", monkeypatch=monkeypatch)
    assert result.exit_code == 0
    lines = [json.loads(ln) for ln in result.output.splitlines() if ln.strip()]
    assert [rec["event"] for rec in lines] == ["run-start", "run-end"]
    assert all(rec["trace_id"] == trace_id for rec in lines)


# ---------------------------------------------------------------------------
# AC4 — partial/failed trace truncates, never errors
# ---------------------------------------------------------------------------


def test_dead_lettered_flush_truncates_at_failure(tmp_path, monkeypatch):
    lore_root = _lore_root(tmp_path)
    store = FlushStore(lore_root)
    trace_id = "trace-truncated"
    rec = store.begin("buf-fail", wiki="private", trace_id=trace_id)
    store.transition(rec, FlushState.RUNNING)
    _emit(lore_root, source="curator", event="buffer-opened", trace_id=trace_id, run_id="r1")
    store.transition(rec, FlushState.DEAD_LETTERED, reason=ErrorCode.COMPOSE_FAILED)

    result = _invoke(lore_root, trace_id, "--plain", monkeypatch=monkeypatch)
    assert result.exit_code == 0
    assert "-- dead-lettered" in result.output
    # Last line rendered is the dead-letter transition itself — nothing
    # is synthesized past the last real event.
    last_line = [ln for ln in result.output.splitlines() if ln.strip()][-1]
    assert "flush-dead-lettered" in last_line
    assert "(compose-failed)" in last_line


def test_trace_with_no_events_does_not_crash(tmp_path, monkeypatch):
    lore_root = _lore_root(tmp_path)
    store = FlushStore(lore_root)
    store.begin("buf-empty", wiki="private", trace_id="trace-empty")

    result = _invoke(lore_root, "trace-empty", "--plain", monkeypatch=monkeypatch)
    assert result.exit_code == 0


# ---------------------------------------------------------------------------
# AC5 — read-only
# ---------------------------------------------------------------------------


def _snapshot(lore_root: Path) -> dict[str, str]:
    out = {}
    for p in sorted(lore_root.rglob("*")):
        if p.is_file():
            out[str(p.relative_to(lore_root))] = hashlib.sha256(p.read_bytes()).hexdigest()
    return out


def test_trace_never_writes_to_lore_root(tmp_path, monkeypatch):
    lore_root = _lore_root(tmp_path)
    store = FlushStore(lore_root)
    rec = store.begin("buf-ro", wiki="private", trace_id="trace-readonly")
    store.transition(rec, FlushState.RUNNING)
    _emit(lore_root, source="curator", event="run-start", trace_id="trace-readonly", run_id="r1")
    note_dir = lore_root / "wiki" / "private" / "sessions"
    note_dir.mkdir(parents=True)
    (note_dir / "n.md").write_text("---\nlinkage:\n  trace_id: trace-readonly\n---\nbody\n")

    before = _snapshot(lore_root)
    for args in (
        ["trace-readonly"],
        ["trace-readonly", "--plain"],
        ["trace-readonly", "--json"],
        ["last"],
        ["dead"],
        [str(note_dir / "n.md")],
    ):
        _invoke(lore_root, *args, monkeypatch=monkeypatch)
    after = _snapshot(lore_root)
    assert before == after, "lore trace must never write to lore_root"
