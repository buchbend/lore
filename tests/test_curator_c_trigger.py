"""Kill switch: SessionStart no longer auto-triggers Curator C's weekly defrag.

The ISO-week + per-user-jitter SessionStart trigger is a severed entry
point — `cmd_session_start` never calls `_spawn_detached_curator_c`,
regardless of `curator_c.enabled`/`mode` or how stale `last_curator_c`
is. The underlying spawn-lock concurrency guarantee in
`lore_cli.spawn._spawn_detached_curator_c` is untouched and still
covered directly below.
"""

from __future__ import annotations

import multiprocessing as mp
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import patch

from lore_cli.hooks import hook_app
from lore_core.ledger import WikiLedger, WikiLedgerEntry
from typer.testing import CliRunner

runner = CliRunner()


def _make_attached_project(root: Path, *, enabled: bool = True, mode: str = "local") -> Path:
    from datetime import UTC, datetime

    from lore_core.state.attachments import Attachment, AttachmentsFile

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
            attached_at=datetime.now(UTC),
            source="manual",
        )
    )
    af.save()
    wiki_cfg = project / "wiki" / "testwiki" / ".lore-wiki.yml"
    wiki_cfg.write_text(
        f"curator:\n  curator_c:\n    enabled: {str(enabled).lower()}\n    mode: {mode}\n"
    )
    return project


# ---------------------------------------------------------------------------
# SessionStart never spawns Curator C, in any config shape
# ---------------------------------------------------------------------------


def test_session_start_never_spawns_curator_c_when_enabled_and_stale(tmp_path: Path) -> None:
    """Even enabled + wildly stale last_curator_c, no spawn happens."""
    project = _make_attached_project(tmp_path, enabled=True, mode="local")
    lore_root = project
    now = datetime(2026, 4, 21, 23, 0, 0, tzinfo=UTC)

    wledger = WikiLedger(lore_root, "testwiki")
    wledger.write(WikiLedgerEntry(wiki="testwiki", last_curator_c=now - timedelta(days=365)))

    spawns: list = []
    with (
        patch(
            "lore_cli.hooks._spawn_detached_curator_c",
            side_effect=lambda *a, **kw: spawns.append(1) or True,
        ),
        patch("lore_cli.hooks._now_utc", return_value=now),
    ):
        result = runner.invoke(
            hook_app,
            ["session-start", "--cwd", str(project), "--plain"],
            env={"LORE_ROOT": str(lore_root)},
            catch_exceptions=False,
        )

    assert result.exit_code == 0, result.output
    assert spawns == [], f"Curator C entry point must be severed; got {spawns}"


def test_session_start_never_spawns_curator_c_when_disabled(tmp_path: Path) -> None:
    """Disabled config also never spawns (trivially — belt and suspenders)."""
    project = _make_attached_project(tmp_path, enabled=False)
    lore_root = project
    now = datetime(2026, 4, 21, 23, 0, 0, tzinfo=UTC)

    wledger = WikiLedger(lore_root, "testwiki")
    wledger.write(WikiLedgerEntry(wiki="testwiki", last_curator_c=now - timedelta(days=30)))

    spawns: list = []
    with (
        patch(
            "lore_cli.hooks._spawn_detached_curator_c",
            side_effect=lambda *a, **kw: spawns.append(1) or True,
        ),
        patch("lore_cli.hooks._now_utc", return_value=now),
    ):
        runner.invoke(
            hook_app,
            ["session-start", "--cwd", str(project), "--plain"],
            env={"LORE_ROOT": str(lore_root)},
            catch_exceptions=False,
        )
    assert spawns == [], f"expected no spawn; got {spawns}"


def test_session_start_never_spawns_curator_c_never_run(tmp_path: Path) -> None:
    """last_curator_c=None (never run) also never spawns."""
    project = _make_attached_project(tmp_path, enabled=True)
    lore_root = project

    wledger = WikiLedger(lore_root, "testwiki")
    wledger.write(WikiLedgerEntry(wiki="testwiki", last_curator_c=None))

    spawns: list = []
    with patch(
        "lore_cli.hooks._spawn_detached_curator_c",
        side_effect=lambda *a, **kw: spawns.append(1) or True,
    ):
        result = runner.invoke(
            hook_app,
            ["session-start", "--cwd", str(project), "--plain"],
            env={"LORE_ROOT": str(lore_root)},
            catch_exceptions=False,
        )
    assert result.exit_code == 0, result.output
    assert spawns == [], f"expected no spawn; got {spawns}"


# ---------------------------------------------------------------------------
# Concurrency (flock regression guard) — exercises the surviving spawn.py
# mechanism directly, independent of the (now severed) SessionStart trigger.
# ---------------------------------------------------------------------------


def _worker_c_spawn_attempt(lore_root_str: str, results_q, barrier) -> None:
    """Child process attempting to acquire the Curator C spawn lock."""
    from unittest.mock import patch

    with patch("subprocess.Popen"):
        from lore_cli.hooks import _spawn_detached_curator_c

        barrier.wait(timeout=10)
        spawned = _spawn_detached_curator_c(Path(lore_root_str), cooldown_s=60)
    results_q.put(spawned)


def test_trigger_concurrent_sessions_coordinate(tmp_path: Path) -> None:
    """Four concurrent processes attempting Curator C spawn → exactly one wins."""
    lore_root = tmp_path / "root"
    (lore_root / ".lore").mkdir(parents=True)

    n = 4
    ctx = mp.get_context("spawn")
    barrier = ctx.Barrier(n)
    results_q: mp.Queue = ctx.Queue()

    procs = [
        ctx.Process(target=_worker_c_spawn_attempt, args=(str(lore_root), results_q, barrier))
        for _ in range(n)
    ]
    for p in procs:
        p.start()
    for p in procs:
        p.join(timeout=30)

    results = []
    while not results_q.empty():
        results.append(results_q.get_nowait())
    winners = [r for r in results if r]
    assert len(winners) == 1, f"exactly one process must spawn; got {results}"
