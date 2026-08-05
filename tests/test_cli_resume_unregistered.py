"""`lore resume` is not a CLI command.

Telemetry over 307 sessions recorded zero calls to `lore_resume` (ADR 0007,
issue #359) — the verb, its MCP twin, and the gather module are retired.
This guards against a future re-mount silently reintroducing it.
"""

from __future__ import annotations

from lore_cli.__main__ import app
from typer.testing import CliRunner

runner = CliRunner()


def test_resume_absent_from_top_level_app():
    result = runner.invoke(app, ["resume"])
    assert result.exit_code != 0
    assert "no such command" in result.output.lower()
