"""Task 1: `lore doctor` must not have side-effects.

The hook probe used by `lore doctor` to verify the SessionStart hook is
reachable was unconditionally running the calendar-rollover Curator B
spawn. A diagnostic that mutates the thing it diagnoses is a bug.

Plan A / Task 1: add a hidden `--probe` flag to `lore hook session-start`
that suppresses ALL spawn side-effects. `lore doctor` invokes with
`--probe`.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import patch

from lore_cli import doctor_cmd
from lore_cli.hooks import hook_app
from lore_core.ledger import WikiLedger, WikiLedgerEntry
from typer.testing import CliRunner

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

    af = AttachmentsFile(project)
    af.load()
    af.add(
        Attachment(
            path=project,
            wiki="testwiki",
            scope="testscope",
            attached_at=datetime.now(tz=UTC),
            source="manual",
        )
    )
    af.save()

    sf = ScopesFile(project)
    sf.load()
    sf.ingest_chain("testscope", "testwiki")
    sf.save()

    return project


def _yesterday() -> datetime:
    now = datetime.now(tz=UTC).replace(hour=0, minute=0, second=0, microsecond=0)
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
def test_doctor_probe_writes_no_state_files(tmp_path, monkeypatch) -> None:
    """`lore doctor` must not write any files under $LORE_ROOT/.lore/.

    Byte-for-byte snapshot of the .lore/ directory before and after a doctor
    invocation. Any spawn, stamp, lock, or ledger write would change the
    snapshot and fail this test.
    """
    project = _make_attached_project(tmp_path)
    lore_root = project

    # Pre-populate WikiLedger with yesterday's last_curator_a so that WITHOUT
    # --probe the hook would spawn Curator A. With --probe (via doctor) it
    # must NOT.
    wledger = WikiLedger(lore_root, "testwiki")
    wledger.write(WikiLedgerEntry(wiki="testwiki", last_curator_a=_yesterday()))

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
