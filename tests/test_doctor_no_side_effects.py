"""Task 1: `lore doctor` must not have side-effects.

The hook probe used by `lore doctor` to verify the SessionStart hook is
reachable was unconditionally running the calendar-rollover Curator B
spawn. A diagnostic that mutates the thing it diagnoses is a bug.

Plan A / Task 1: add a hidden `--probe` flag to `lore hook session-start`
that suppresses ALL spawn side-effects. `lore doctor` invokes with
`--probe`.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from typer.testing import CliRunner

from lore_cli import doctor_cmd
from lore_cli.hooks import hook_app
from lore_core.ledger import WikiLedger, WikiLedgerEntry


runner = CliRunner()


LORE_BLOCK = """\
# Project

## Lore

<!-- managed by /lore:attach -->

- wiki: testwiki
- scope: testscope
- backend: none
"""


def _make_attached_project(root: Path) -> Path:
    from lore_core.state.attachments import Attachment, AttachmentsFile
    from lore_core.state.scopes import ScopesFile

    project = root / "project"
    project.mkdir()
    (project / "wiki" / "testwiki").mkdir(parents=True)
    (project / ".lore").mkdir(parents=True, exist_ok=True)

    af = AttachmentsFile(project); af.load()
    af.add(Attachment(
        path=project, wiki="testwiki", scope="testscope",
        attached_at=datetime.now(tz=timezone.utc), source="manual",
    ))
    af.save()

    sf = ScopesFile(project); sf.load()
    sf.ingest_chain("testscope", "testwiki")
    sf.save()

    return project


def _yesterday() -> datetime:
    now = datetime.now(tz=timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    return now - timedelta(days=1)


def _snapshot_dir(path: Path) -> dict[str, tuple[int, bytes]]:
    """Return {relative_path: (mtime_ns, content_hash_bytes)} for every file under path."""
    import hashlib
    out: dict[str, tuple[int, bytes]] = {}
    if not path.exists():
        return out
    for p in sorted(path.rglob("*")):
        if p.is_file():
            rel = str(p.relative_to(path))
            h = hashlib.sha256(p.read_bytes()).digest()
            out[rel] = (p.stat().st_mtime_ns, h)
    return out


# ---------------------------------------------------------------------------
# Hook-level: --probe suppresses spawns
# ---------------------------------------------------------------------------


def test_session_start_probe_suppresses_curator_b_spawn(tmp_path: Path) -> None:
    """With --probe, calendar-rollover does NOT spawn Curator B."""
    project = _make_attached_project(tmp_path)
    lore_root = project

    wledger = WikiLedger(lore_root, "testwiki")
    wledger.write(WikiLedgerEntry(wiki="testwiki", last_curator_b=_yesterday()))

    calls: list[tuple] = []

    def mock_spawn(lore_root_: Path, wiki: str, **kw):
        calls.append((lore_root_, wiki))
        return True

    with patch("lore_cli.hooks._spawn_detached_curator_b", side_effect=mock_spawn):
        result = runner.invoke(
            hook_app,
            ["session-start", "--cwd", str(project), "--plain", "--probe"],
            env={"LORE_ROOT": str(lore_root)},
            catch_exceptions=False,
        )

    assert result.exit_code == 0, result.output
    assert len(calls) == 0, (
        f"--probe must suppress Curator B spawn, got {len(calls)} calls: {calls}"
    )


def test_session_start_no_probe_still_spawns_curator_b(tmp_path: Path) -> None:
    """Baseline regression guard: without --probe, calendar-rollover still spawns."""
    project = _make_attached_project(tmp_path)
    lore_root = project

    wledger = WikiLedger(lore_root, "testwiki")
    wledger.write(WikiLedgerEntry(wiki="testwiki", last_curator_b=_yesterday()))

    calls: list[tuple] = []

    def mock_spawn(lore_root_: Path, wiki: str, **kw):
        calls.append((lore_root_, wiki))
        return True

    with patch("lore_cli.hooks._spawn_detached_curator_b", side_effect=mock_spawn):
        result = runner.invoke(
            hook_app,
            ["session-start", "--cwd", str(project), "--plain"],
            env={"LORE_ROOT": str(lore_root)},
            catch_exceptions=False,
        )

    assert result.exit_code == 0, result.output
    assert len(calls) == 1, f"Without --probe, spawn must still happen: {calls}"


def test_session_start_probe_suppresses_curator_a_spawn(tmp_path: Path) -> None:
    """--probe also suppresses Curator A (defense in depth for future spawn paths).

    Curator A is SessionEnd-triggered today, so SessionStart doesn't call
    _spawn_detached_curator_a. But the --probe contract is "suppress all
    spawn paths," and this test guards against accidentally adding a
    SessionStart-triggered A spawn that would leak side-effects into doctor.
    """
    project = _make_attached_project(tmp_path)
    lore_root = project

    wledger = WikiLedger(lore_root, "testwiki")
    wledger.write(WikiLedgerEntry(wiki="testwiki", last_curator_b=_yesterday()))

    a_calls: list = []

    def mock_spawn_a(*args, **kw):
        a_calls.append((args, kw))
        return True

    with patch("lore_cli.hooks._spawn_detached_curator_a", side_effect=mock_spawn_a), \
         patch("lore_cli.hooks._spawn_detached_curator_b", return_value=True):
        result = runner.invoke(
            hook_app,
            ["session-start", "--cwd", str(project), "--plain", "--probe"],
            env={"LORE_ROOT": str(lore_root)},
            catch_exceptions=False,
        )

    assert result.exit_code == 0, result.output
    assert len(a_calls) == 0, f"--probe must suppress Curator A spawn too: {a_calls}"


# ---------------------------------------------------------------------------
# Integration: `lore doctor` end-to-end produces no side-effects
# ---------------------------------------------------------------------------


def test_doctor_probe_writes_no_state_files(tmp_path, monkeypatch) -> None:
    """`lore doctor` must not write any files under $LORE_ROOT/.lore/.

    Byte-for-byte snapshot of the .lore/ directory before and after a doctor
    invocation. Any spawn, stamp, lock, or ledger write would change the
    snapshot and fail this test.
    """
    project = _make_attached_project(tmp_path)
    lore_root = project

    # Pre-populate WikiLedger with yesterday's last_curator_b so that WITHOUT
    # --probe the hook would spawn Curator B. With --probe (via doctor) it
    # must NOT.
    wledger = WikiLedger(lore_root, "testwiki")
    wledger.write(WikiLedgerEntry(wiki="testwiki", last_curator_b=_yesterday()))

    monkeypatch.setenv("LORE_ROOT", str(lore_root))
    monkeypatch.setenv("LORE_CACHE", str(tmp_path / "cache"))

    before = _snapshot_dir(lore_root / ".lore")
    assert before, "precondition: .lore/ has the WikiLedger we just wrote"

    rc = doctor_cmd.main(["--cwd", str(project), "--json"])
    assert rc == 0

    after = _snapshot_dir(lore_root / ".lore")
    assert before == after, (
        "doctor must not mutate .lore/. Diff:\n"
        f"  added:   {set(after) - set(before)}\n"
        f"  removed: {set(before) - set(after)}\n"
        f"  changed: {[k for k in before if k in after and before[k] != after[k]]}"
    )


# ---------------------------------------------------------------------------
# --probe is a hidden flag
# ---------------------------------------------------------------------------


def test_probe_flag_is_hidden_in_help() -> None:
    """`lore hook session-start --help` must not advertise --probe."""
    result = runner.invoke(hook_app, ["session-start", "--help"])
    assert result.exit_code == 0, result.output
    assert "--probe" not in result.output, (
        f"--probe should be hidden from help output:\n{result.output}"
    )
