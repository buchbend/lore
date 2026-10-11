"""The Check step of `lore setup` must not have side-effects.

The hook probe the checks use to verify the SessionStart hook is reachable
once ran the calendar-rollover Curator B spawn. A diagnostic that mutates
the thing it diagnoses is a bug: `lore hook session-start --probe`
suppresses every spawn side-effect.
"""

from __future__ import annotations

from pathlib import Path

from lore_cli.hooks import hook_app
from lore_cli.setup_cmd import step_check
from typer.testing import CliRunner

runner = CliRunner()


def _make_attached_project(root: Path) -> Path:
    from conftest import seed_scope

    project = root / "project"
    (project / "wiki" / "testwiki").mkdir(parents=True)
    (project / ".lore").mkdir(parents=True, exist_ok=True)
    seed_scope(project, "testwiki", name="testscope")
    return project


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
def test_check_step_writes_no_state_files(tmp_path, monkeypatch) -> None:
    """The Check step must not write any files under $LORE_ROOT/.lore/.

    Byte-for-byte snapshot of the .lore/ directory before and after a doctor
    invocation. Any spawn, stamp, lock, or ledger write would change the
    snapshot and fail this test.
    """
    project = _make_attached_project(tmp_path)
    lore_root = project

    # Seed one file under .lore/ so the snapshot has something to compare.
    (lore_root / ".lore").mkdir(parents=True, exist_ok=True)
    (lore_root / ".lore" / "seed.json").write_text("{}")

    monkeypatch.setenv("LORE_ROOT", str(lore_root))
    monkeypatch.setenv("LORE_CACHE", str(tmp_path / "cache"))

    before = _snapshot_dir(lore_root / ".lore")
    assert before, "precondition: .lore/ has the file we just wrote"

    _, events = step_check(project)
    assert not [msg for ok, msg in events if ok is False]

    after = _snapshot_dir(lore_root / ".lore")
    assert before == after, (
        "the checks must not mutate .lore/. Diff:\n"
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
