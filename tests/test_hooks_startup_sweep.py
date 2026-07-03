"""SessionStart spawns the singleton startup sweep.

lore closes dead sessions' notes at start. SessionStart stays fast by
spawning the sweep detached (``lore curator sweep``); the command itself
holds the global curator lock so concurrent starts race safely.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import patch

from lore_cli.hooks import hook_app
from typer.testing import CliRunner


def _make_attached_project(root: Path) -> Path:
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
            attached_at=datetime.now(tz=UTC),
            source="manual",
        )
    )
    af.save()
    return project


runner = CliRunner()


def test_session_start_spawns_the_startup_sweep(tmp_path: Path) -> None:
    project = _make_attached_project(tmp_path)
    calls = []

    def _mock_spawn(role, lore_root, **kw):
        calls.append(role)
        return True

    with (
        patch("lore_cli.hooks.spawn", side_effect=_mock_spawn),
        patch("lore_cli.hooks._spawn_detached_transcript_sync", return_value=True),
    ):
        result = runner.invoke(
            hook_app,
            ["session-start", "--cwd", str(project), "--plain"],
            env={"LORE_ROOT": str(project)},
            catch_exceptions=False,
        )

    assert result.exit_code == 0, result.output
    assert "sweep" in calls, f"SessionStart must spawn the sweep; got {calls}"


def test_probe_session_start_does_not_spawn_sweep(tmp_path: Path) -> None:
    project = _make_attached_project(tmp_path)
    calls = []

    with patch("lore_cli.hooks.spawn", side_effect=lambda role, *a, **k: calls.append(role)):
        result = runner.invoke(
            hook_app,
            ["session-start", "--cwd", str(project), "--plain", "--probe"],
            env={"LORE_ROOT": str(project)},
            catch_exceptions=False,
        )

    assert result.exit_code == 0, result.output
    assert "sweep" not in calls
