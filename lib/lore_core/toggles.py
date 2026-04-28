"""Per-session mute toggles for `/lore:on` and `/lore:off`.

Single source of truth for "is this session muted?" queries used by
hooks (`cmd_session_start`, `cmd_pre_compact`, `cmd_stop`) and the MCP
dispatcher (`lore_mcp.server._dispatch`).

Two scopes:

* ``"all"`` — mutes every Lore touchpoint (hooks short-circuit, MCP
  refuses, citation directive suppressed).
* ``"citations"`` — suppresses only the inline `› consulted [[note]]`
  affordance; hooks and MCP keep working.

State is one sentinel file per (scope, session-id) under ``$TMPDIR``.
File presence is the only state — no body, no timestamps. ``$TMPDIR``
makes the OS reap sentinels at session boundary so we don't have to
track lifetimes.

See ``docs/architecture/slash-toggles.md`` for the full contract.
"""

from __future__ import annotations

import os
from pathlib import Path

VALID_SCOPES = ("all", "citations")


def _check(scope: str, sid: str) -> None:
    if scope not in VALID_SCOPES:
        raise ValueError(
            f"toggle scope must be one of {VALID_SCOPES!r}, got {scope!r}"
        )
    if not sid:
        raise ValueError("toggle session id must be a non-empty string")


def _sentinel_path(scope: str, sid: str) -> Path:
    # Direct env read (not tempfile.gettempdir, which caches at first call
    # and won't reflect a mid-process TMPDIR change in tests).
    # Always include the scope prefix so future scopes can't collide with
    # a sid that happens to start with their name.
    base = Path(os.environ.get("TMPDIR", "/tmp"))
    return base / f"lore-off-{scope}-{sid}"


def is_off(scope: str, sid: str) -> bool:
    """True iff the sentinel for ``(scope, sid)`` exists."""
    _check(scope, sid)
    return _sentinel_path(scope, sid).exists()


def set_off(scope: str, sid: str) -> None:
    """Create the sentinel for ``(scope, sid)`` (idempotent)."""
    _check(scope, sid)
    path = _sentinel_path(scope, sid)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.touch(exist_ok=True)


def clear_off(scope: str, sid: str) -> None:
    """Remove the sentinel for ``(scope, sid)`` (no-op if absent)."""
    _check(scope, sid)
    path = _sentinel_path(scope, sid)
    try:
        path.unlink()
    except FileNotFoundError:
        pass
