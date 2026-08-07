"""Guard test: every declared surface carries a producer.

Three structural properties, asserted against the source tree:

* every drain event kind the vocabulary declares has an emitter,
* every spine error code is reachable from a caller,
* every capture row `lore status` prints has a writer.

The behavioural assertions below pin the removals the properties force:
the flush store's write half, the flush selectors in ``lore trace``, the
``lore backfill`` verb, the transcript ledger's pending set, the drain
banner, the heartbeat's drain read, and the duplicate parse/enumerate
helpers.

Prior art: ``tests/test_dead_code_gone.py``.
"""

from __future__ import annotations

import importlib
import inspect
import json
import re
import subprocess
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from typer.testing import CliRunner

REPO = Path(__file__).resolve().parent.parent
LIB = REPO / "lib"

runner = CliRunner()

_NOW = datetime(2026, 4, 21, 12, 0, 0, tzinfo=UTC)


def _iso(dt: datetime) -> str:
    return dt.isoformat().replace("+00:00", "Z")


def _lib_sources(*, exclude: frozenset[str] = frozenset()) -> dict[Path, str]:
    """Every ``lib/`` python source, keyed by path, minus ``exclude`` basenames."""
    return {
        p: p.read_text(errors="replace") for p in sorted(LIB.rglob("*.py")) if p.name not in exclude
    }


# ---------------------------------------------------------------------------
# Property 1 — every declared drain event kind carries an emitter
# ---------------------------------------------------------------------------


def test_every_drain_event_kind_has_an_emitter() -> None:
    from lore_core.drain import EVENT_VOCAB

    sources = _lib_sources(exclude=frozenset({"drain.py"}))
    orphaned = sorted(
        kind
        for kind in EVENT_VOCAB
        if not any(
            re.search(rf"""\.emit\(\s*["']{re.escape(kind)}["']""", body)
            for body in sources.values()
        )
    )
    assert orphaned == [], f"drain event kinds with no emitter: {orphaned}"


# ---------------------------------------------------------------------------
# Property 2 — every spine error code is reachable from a caller
# ---------------------------------------------------------------------------


def test_every_spine_error_code_is_reachable() -> None:
    from lore_core.spine import ErrorCode

    sources = _lib_sources(exclude=frozenset({"spine.py"}))
    unreachable = sorted(
        code.name
        for code in ErrorCode
        if not any(f"ErrorCode.{code.name}" in body for body in sources.values())
    )
    assert unreachable == [], f"spine error codes no caller raises: {unreachable}"


# ---------------------------------------------------------------------------
# Property 3 — every capture row `lore status` prints has a writer
# ---------------------------------------------------------------------------

#: Rows whose backing record lost its writer with the compose pipeline
#: (#361) and the surfaces (#131).
_PRODUCERLESS_ROWS = ("Last note", "Last run", "Last flush", "Lock", "Pending")


def _seed_vault(tmp_path: Path) -> tuple[Path, Path]:
    """Return ``(lore_root, attached_project_dir)`` for a minimal vault."""
    from lore_core.state.attachments import Attachment, AttachmentsFile

    lore_root = tmp_path / "vault"
    (lore_root / ".lore").mkdir(parents=True)
    (lore_root / "wiki" / "private" / "sessions").mkdir(parents=True)

    project = tmp_path / "project"
    project.mkdir()
    af = AttachmentsFile(lore_root)
    af.load()
    af.add(
        Attachment(
            path=project,
            wiki="private",
            scope="proj:test",
            attached_at=datetime.now(UTC),
            source="manual",
        )
    )
    af.save()
    return lore_root, project


def _seed_zero_note_run(lore_root: Path, *, ago: timedelta, suffix: str) -> None:
    """Write one hygiene-shaped run: a run that files no note."""
    from lore_core.spine import SpineWriter

    run_ts = _NOW - ago
    run_id = run_ts.strftime("%Y-%m-%dT%H-%M-%S") + f"-{suffix}"
    writer = SpineWriter(lore_root)
    for record_type in ("run-start", "run-end"):
        writer.emit(
            source="curator",
            event=record_type,
            run_id=run_id,
            data={"type": record_type, "notes_new": 0, "notes_merged": 0, "errors": 0},
        )


def _status(lore_root: Path, project: Path, *extra: str, monkeypatch) -> str:
    from lore_cli.status_cmd import app

    monkeypatch.setenv("LORE_ROOT", str(lore_root))
    monkeypatch.setenv("_LORE_STATUS_NOW", _iso(_NOW))
    result = runner.invoke(app, ["--plain", "--cwd", str(project), *extra], catch_exceptions=False)
    return result.output


def test_status_prints_no_capture_row_without_a_writer(tmp_path: Path, monkeypatch) -> None:
    lore_root, project = _seed_vault(tmp_path)
    out = _status(lore_root, project, monkeypatch=monkeypatch)

    printed = [label for label in _PRODUCERLESS_ROWS if label in out]
    assert printed == [], f"`lore status` still prints rows nothing writes: {printed}\n{out}"


def test_status_prints_no_flushes_panel(tmp_path: Path, monkeypatch) -> None:
    lore_root, project = _seed_vault(tmp_path)
    out = _status(lore_root, project, monkeypatch=monkeypatch)

    assert "\nflushes\n" not in out, f"flushes panel survives:\n{out}"
    assert "dead-lettered" not in out, f"flush counts survive:\n{out}"


def test_status_raises_no_zero_note_alert_for_hygiene_runs(tmp_path: Path, monkeypatch) -> None:
    lore_root, project = _seed_vault(tmp_path)
    _seed_zero_note_run(lore_root, ago=timedelta(hours=2), suffix="aaa111")
    _seed_zero_note_run(lore_root, ago=timedelta(hours=1), suffix="bbb222")

    out = _status(lore_root, project, monkeypatch=monkeypatch)
    assert "filed 0 notes" not in out, f"zero-note alert fires on ordinary runs:\n{out}"


def test_status_json_carries_no_flush_counts(tmp_path: Path, monkeypatch) -> None:
    from lore_cli.status_cmd import app

    lore_root, project = _seed_vault(tmp_path)
    monkeypatch.setenv("LORE_ROOT", str(lore_root))
    monkeypatch.setenv("_LORE_STATUS_NOW", _iso(_NOW))
    result = runner.invoke(app, ["--json", "--cwd", str(project)], catch_exceptions=False)
    payload = json.loads(result.output)
    assert "flushes" not in payload
    assert "pending_transcripts" not in payload


# ---------------------------------------------------------------------------
# The drain
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("kind", ["note-filed", "note-appended", "surface-proposed"])
def test_drain_rejects_a_producerless_kind(tmp_path: Path, kind: str) -> None:
    from lore_core.drain import DrainStore

    with pytest.raises(ValueError):
        DrainStore(tmp_path, "sid").emit(kind)


def test_drain_banner_module_is_gone() -> None:
    with pytest.raises(ModuleNotFoundError):
        importlib.import_module("lore_core.drain_banner")


def test_heartbeat_module_is_gone() -> None:
    with pytest.raises(ModuleNotFoundError):
        importlib.import_module("lore_curator.heartbeat")


def test_user_prompt_submit_runs_no_drain_read() -> None:
    """The prompt hook registers transcripts; it opens no drain store."""
    from lore_cli import hooks

    body = inspect.getsource(hooks.cmd_user_prompt_submit)
    assert "_heartbeat" not in body, body
    assert "drain" not in body.lower(), body


# ---------------------------------------------------------------------------
# The flush store — deleted whole (PRD 0013); see test_dead_code_gone.py.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("selector", ["dead", "last"])
def test_trace_reports_an_unknown_flush_selector(tmp_path: Path, selector: str) -> None:
    from lore_core.trace import TraceNotFound, resolve_selector

    with pytest.raises(TraceNotFound):
        resolve_selector(tmp_path, selector)


# ---------------------------------------------------------------------------
# The unreferenced modules
# ---------------------------------------------------------------------------


def test_run_render_module_is_gone() -> None:
    with pytest.raises(ModuleNotFoundError):
        importlib.import_module("lore_core.run_render")


def test_curator_lock_leaves_no_trace_in_the_code() -> None:
    """The mkdir work-lock is gone; ``.lore/curator.lock`` can never exist.

    Scoped to the shipped code and the agent-facing prose. This guard
    names the symbol itself, and the changelog records history, so
    neither counts as a surviving use.
    """
    hits = subprocess.run(
        ["git", "grep", "-l", "curator_lock", "--", "lib", "skills", "lore-workflow"],
        cwd=str(REPO),
        capture_output=True,
        text=True,
    ).stdout.split()
    assert hits == [], f"`curator_lock` still named in: {hits}"


@pytest.mark.parametrize(
    ("module_name", "symbol"),
    [
        ("lore_core.lockfile", "read_lock_holder"),
        ("lore_core.lockfile", "curator_lock"),
        ("lore_core.ledger", "WikiLedger"),
        ("lore_core.ledger", "WikiLedgerEntry"),
    ],
)
def test_symbol_is_gone(module_name: str, symbol: str) -> None:
    module = importlib.import_module(module_name)
    assert not hasattr(module, symbol), f"{module_name}.{symbol} survives"


@pytest.mark.parametrize(
    "attribute", ["overdue", "work_lock_held", "last_note_filed", "last_briefing_ts"]
)
def test_capture_state_drops_the_readerless_field(attribute: str) -> None:
    from lore_core.capture_state import CaptureState

    assert attribute not in CaptureState.__dataclass_fields__


# ---------------------------------------------------------------------------
# The transcript ledger
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "field_name",
    [
        "digested_hash",
        "digested_index_hint",
        "synthesised_hash",
        "noteworthy",
        "session_note",
        "curator_a_run",
    ],
)
def test_ledger_entry_drops_the_retired_field(field_name: str) -> None:
    from lore_core.ledger import TranscriptLedgerEntry

    assert field_name not in TranscriptLedgerEntry.__dataclass_fields__


@pytest.mark.parametrize("method", ["advance", "pending", "pending_by_wiki", "_is_pending"])
def test_ledger_drops_the_pending_set(method: str) -> None:
    from lore_core.ledger import TranscriptLedger

    assert not hasattr(TranscriptLedger, method), f"TranscriptLedger.{method} survives"


def test_doctor_reports_no_pending_count() -> None:
    from lore_cli.doctor_cmd import _CHECKS

    names = {name for name, _fn, _fails in _CHECKS}
    assert "pending" not in names
    assert "ledger buckets" not in names


# ---------------------------------------------------------------------------
# The backfill command
# ---------------------------------------------------------------------------


def test_backfill_reports_an_unknown_command() -> None:
    from lore_cli.__main__ import app

    result = runner.invoke(app, ["backfill"])
    assert result.exit_code != 0


def test_backfill_module_is_gone() -> None:
    with pytest.raises(ModuleNotFoundError):
        importlib.import_module("lore_cli.backfill_cmd")


# ---------------------------------------------------------------------------
# The duplicate helpers
# ---------------------------------------------------------------------------


def test_the_package_holds_one_timestamp_parser() -> None:
    copies = sorted(
        str(path.relative_to(REPO))
        for path, body in _lib_sources().items()
        if re.search(r"^\s*def _parse_(ts|iso)\(", body, re.MULTILINE)
    )
    assert copies == [], f"private timestamp parsers survive in: {copies}"


def test_the_one_timestamp_parser_stamps_utc_on_a_z_suffix() -> None:
    from lore_core.timefmt import parse_ts

    parsed = parse_ts("2026-04-21T12:00:00Z")
    assert parsed is not None
    assert parsed.tzinfo is not None
    assert parsed.utcoffset() == timedelta(0)


def test_the_package_holds_one_wiki_enumerator() -> None:
    copies = sorted(
        str(path.relative_to(REPO))
        for path, body in _lib_sources().items()
        if re.search(r"^\s*def (_wiki_dirs|_list_wikis)\(", body, re.MULTILINE)
    )
    assert copies == [], f"private wiki enumerators survive in: {copies}"


def test_the_one_wiki_enumerator_returns_empty_for_a_vault_with_no_wiki(tmp_path: Path) -> None:
    from lore_core.config import list_wikis

    assert list_wikis(tmp_path) == []


# ---------------------------------------------------------------------------
# Property 4 — no declaration outlives its producer
#
# The session-note teardown left surfaces whose producer went with it, plus
# two that predate it. Each assertion pins one removal or one correction so
# the surface cannot creep back silently.
# ---------------------------------------------------------------------------


def _repo_text(rel: str) -> str:
    return (REPO / rel).read_text(errors="replace")


def test_briefing_cmd_docstring_marks_the_one_shot_parked() -> None:
    """`gather()` yields no sessions, so `_run_oneshot` returns before compose,
    publish and mark on every real call. PRD 0011 parks that path rather than
    reviving it; the module docstring has to say so."""
    import lore_cli.briefing_cmd as mod

    doc = (mod.__doc__ or "").lower()
    assert "parked" in doc, "briefing_cmd's docstring must name the parked one-shot path"


def test_run_log_declares_no_session_note_record_type() -> None:
    from lore_core.run_log import RunLogger

    assert "session-note" not in RunLogger.RECORD_TYPES


def test_no_module_reads_the_session_note_record_type() -> None:
    """No emitter is left, so no branch, docstring example or trace label may
    keep matching on it."""
    hits = sorted(
        str(path.relative_to(REPO))
        for path, body in _lib_sources().items()
        if '"session-note"' in body
    )
    assert hits == [], f"the retired 'session-note' record type survives in: {hits}"


def test_retention_config_declares_no_readerless_cap() -> None:
    """`dead_letter_hard_cap` lost its reader with the flush store. An unknown
    key still parses — `root_config` warns and falls back to defaults — so the
    field's only remaining effect was to make a dead knob look live."""
    from lore_core.root_config import RetentionConfig

    assert not hasattr(RetentionConfig(), "dead_letter_hard_cap")


def test_context_format_names_no_absent_note_document_symbol() -> None:
    import lore_core.note_document as nd

    referenced = set(
        re.findall(r"`(render_note|read_note|append_\w+)`", _repo_text("CONTEXT-FORMAT.md"))
    )
    missing = sorted(name for name in referenced if not hasattr(nd, name))
    assert missing == [], f"CONTEXT-FORMAT.md names absent note_document symbols: {missing}"


def test_the_brief_skill_claims_no_session_note() -> None:
    """The skill justified having no brief file by pointing at session notes.
    No code writes one, so the justification names a surface that is gone.

    Scoped to this one file on purpose: widening
    ``test_no_skill_claims_lore_writes_a_session_note`` to the whole
    ``lore-workflow/skills/`` tree is a separate change, and a blanket scan
    here would also flag the *correct* negative claims other skills make."""
    text = _repo_text("lore-workflow/skills/brief/SKILL.md")
    assert not re.search(r"(?<!no )session notes? (do|does)\b", text)
    assert not re.search(r"(?<!Nothing )writes a session note", text)


def test_troubleshooting_names_the_recent_txt_leftover() -> None:
    """`lint.generate_recent_txt` is gone, so a wiki that already holds
    `sessions/_recent.txt` keeps it forever. The session-note section has to
    name it alongside the notes themselves."""
    assert "_recent.txt" in _repo_text("docs/how-to/troubleshooting.md")


def test_errors_declares_no_constant_no_module_reads() -> None:
    from lore_core import errors as errors_mod

    sources = _lib_sources(exclude=frozenset({"errors.py"}))
    unread = sorted(
        name
        for name, value in vars(errors_mod).items()
        if name.isupper()
        and isinstance(value, str)
        and not any(name in body for body in sources.values())
    )
    assert unread == [], f"error-code constants no module imports: {unread}"


def test_the_mcp_server_imports_every_error_code_it_emits() -> None:
    """The module comment promises centralised client-facing strings. A code
    written as a literal breaks that promise silently — renaming the constant
    leaves the literal behind."""
    from lore_core import errors as errors_mod

    server = _repo_text("lib/lore_mcp/server.py")
    declared = {v for n, v in vars(errors_mod).items() if n.isupper() and isinstance(v, str)}
    literals = sorted(code for code in declared if f'"{code}"' in server)
    assert literals == [], f"server.py writes declared error codes as literals: {literals}"


def test_gh_declares_no_function_without_a_production_caller() -> None:
    from lore_core import gh

    sources = _lib_sources(exclude=frozenset({"gh.py"}))
    orphaned = sorted(
        name
        for name, obj in vars(gh).items()
        if inspect.isfunction(obj)
        and obj.__module__ == "lore_core.gh"
        and not any(name in body for body in sources.values())
    )
    assert orphaned == [], f"gh helpers no production caller reaches: {orphaned}"


def test_the_offer_schema_declares_no_key_without_a_reader() -> None:
    """`issues:`/`prs:` fed the SessionStart gh counts. The banner dropped those
    counts; the keys stayed, so a user's filter had no effect and no warning.

    ``Offer`` is the schema for a repo's lore block and its `.lore.yml` — a
    field here is a key `lore config edit` offers and `validate_offer_raw`
    accepts, so an unread field advertises a knob that does nothing."""
    import dataclasses

    from lore_core.offer import Offer

    names = {f.name for f in dataclasses.fields(Offer)}
    assert "issues" not in names
    assert "prs" not in names


@pytest.mark.parametrize("doc", ["CLAUDE.md", "AGENTS.md"])
def test_this_repos_lore_block_sets_no_readerless_key(doc: str) -> None:
    assert not re.search(r"^- (issues|prs):", _repo_text(doc), re.MULTILINE)
