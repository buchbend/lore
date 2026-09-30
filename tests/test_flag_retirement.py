"""Guard test for the retired flag crossing (ADR 0012).

The flag was the only path from a session to the team wiki: an MCP tool,
a CLI verb, a review walk, a pending count on the SessionStart banner and
a counter section in `lore status` and `lore trace`. Agents file facts as
repo artifacts now, so none of it ships.
"""

from __future__ import annotations

import importlib
import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

REPO = Path(__file__).resolve().parent.parent
runner = CliRunner()

RETIRED_MODULES = [
    "lore_core.flag",
    "lore_core.flag_metrics",
    "lore_cli.flag_cmd",
    "lore_cli.flag_review_html",
]


@pytest.mark.parametrize("module_name", RETIRED_MODULES)
def test_the_flag_module_is_gone(module_name: str) -> None:
    with pytest.raises(ModuleNotFoundError):
        importlib.import_module(module_name)


# ---------------------------------------------------------------------------
# Entry points
# ---------------------------------------------------------------------------


def test_the_mcp_tool_list_holds_no_flag_tool() -> None:
    from lore_mcp.server import _tool_schema

    assert "lore_flag" not in {tool["name"] for tool in _tool_schema()}


def test_the_mcp_server_exposes_no_flag_handler() -> None:
    import lore_mcp.server as server

    assert not hasattr(server, "handle_flag")


def test_the_flag_cli_verb_is_unknown() -> None:
    from lore_cli.__main__ import app

    result = runner.invoke(app, ["flag"])

    assert result.exit_code != 0
    assert "No such command" in result.output


# ---------------------------------------------------------------------------
# Counters
# ---------------------------------------------------------------------------


def test_the_session_banner_carries_no_pending_flag_count() -> None:
    from lore_core import session_start

    assert not hasattr(session_start, "pending_flag_chip")
    assert "flag_chip" not in session_start.SessionFacts.__dataclass_fields__


def _lore_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("LORE_ROOT", str(tmp_path))
    monkeypatch.setenv("LORE_CACHE", str(tmp_path / "cache"))
    (tmp_path / "wiki" / "private").mkdir(parents=True)
    return tmp_path


def test_status_renders_no_flags_section() -> None:
    import lore_cli.status_cmd as status_cmd

    assert not hasattr(status_cmd, "_flags_lines")
    assert not hasattr(status_cmd, "_render_flags_line")


def test_the_status_json_payload_holds_no_flag_counters(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from lore_cli.status_cmd import app

    lore_root = _lore_root(tmp_path, monkeypatch)

    result = runner.invoke(app, ["--offline", "--json", "--cwd", str(lore_root)])
    payload = json.loads(result.output)

    assert "flags" not in payload


def test_trace_has_no_flag_selector() -> None:
    import lore_cli.trace_cmd as trace_cmd

    assert not hasattr(trace_cmd, "_print_flags")


def test_the_spine_producer_set_drops_the_flag_writer() -> None:
    from lore_core.spine import SOURCES

    assert "flag" not in SOURCES


# ---------------------------------------------------------------------------
# Prose
# ---------------------------------------------------------------------------


def test_the_glossary_drops_the_flag_architecture_section() -> None:
    text = (REPO / "CONTEXT.md").read_text(encoding="utf-8")

    assert "Flag architecture" not in text
