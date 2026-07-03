"""Kill switch: `lore surface` is no longer a top-level CLI command.

Curator B's surface-extraction pass is retired and PRD 0001's Deletions
list names the surface CLI among the entry points to sever first (code
stays on disk for a later physical-deletion slice). `surface_cmd.app`
itself is untouched and still directly testable — see
test_cli_surface.py — only its mount on the top-level `lore` dispatcher
is removed.
"""

from __future__ import annotations

from lore_cli.__main__ import app
from typer.testing import CliRunner

runner = CliRunner()


def test_surface_absent_from_top_level_app():
    result = runner.invoke(app, ["surface", "lint"])
    assert result.exit_code != 0
    assert "no such command" in result.output.lower()
