"""The MCP server binds to a decorator API that `mcp` 2.0 removed.

`lore_mcp.server.start_server` registers its handlers with
`@server.list_tools()` and `@server.call_tool()`. Those decorators exist on
`mcp.server.Server` in the 1.x line only; 2.0 replaced them with the
`add_request_handler` interface, so a server built against 1.x raises
`AttributeError` at startup under 2.x and every tool goes dark.

The dependency was declared open-ended (`mcp>=1.0`), so an existing
environment kept a working 1.x while any fresh install resolved 2.0 — the
failure never reached CI, only users. Both tests below guard that gap: the
declared floor/ceiling, and the runtime attributes the module actually calls.
"""

from __future__ import annotations

import tomllib
from pathlib import Path

from packaging.requirements import Requirement

PYPROJECT = Path(__file__).resolve().parents[1] / "pyproject.toml"


def _mcp_requirement() -> Requirement:
    data = tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))
    for raw in data["project"]["dependencies"]:
        req = Requirement(raw)
        if req.name == "mcp":
            return req
    raise AssertionError("mcp is not declared in [project].dependencies")


def test_declared_mcp_range_excludes_the_2_0_api_break() -> None:
    """A fresh install must not resolve an mcp release without the decorators."""
    specifier = _mcp_requirement().specifier

    assert not specifier.contains("2.0.0"), (
        "pyproject allows mcp 2.0.0, which removed @server.list_tools() and "
        "@server.call_tool(); a fresh install gets a server that dies at startup"
    )
    assert specifier.contains("1.27.0"), "the supported 1.x line must stay installable"


def test_installed_server_exposes_the_decorators_the_module_calls() -> None:
    """Guards the same break arriving inside the allowed range."""
    from mcp.server import Server

    for attribute in ("list_tools", "call_tool"):
        assert hasattr(Server, attribute), (
            f"mcp.server.Server has no {attribute!r}; lore_mcp.server.start_server "
            f"calls @server.{attribute}() and would raise AttributeError"
        )
