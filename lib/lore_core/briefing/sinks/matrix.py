"""Matrix briefing sink.

Publishes a markdown briefing to a Matrix room. Credentials (access
token + device id) live at ``~/.local/share/lore/matrix-credentials.json``
after a one-time login (``lore-sink-matrix login``); that file is the
sole secret store and never enters the wiki repo.

Non-secret room identifiers resolve from ``.lore-briefing.yml`` (per-
wiki config) or environment variables. Resolution order, mirroring the
OpenAI backend in ``root_config.py``:

    1. env var  (one-shot debug override)
    2. yaml field
    3. error

YAML schema (nested form, recommended):

    sink: matrix
    matrix:
      homeserver: https://matrix.example.org
      user_id: "@lore-bot:matrix.example.org"
      room_id: "!abc123:matrix.example.org"

Flat top-level keys (``homeserver:`` / ``user_id:`` / ``room_id:`` at
the document root) are accepted as a transitional fallback with one
deprecation warning per process.

Env var names:

    LORE_MATRIX_HOMESERVER
    LORE_MATRIX_USER_ID
    LORE_MATRIX_ROOM_ID

The optional dependency is ``matrix-nio`` (in the ``[sinks]`` extras).
If it's not installed, ``_send`` raises ``ImportError``; the registry
keeps the sink listed (so users see "matrix" in the help) but a
``dispatch("matrix", …)`` call surfaces the missing dep.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import warnings
from pathlib import Path
from typing import Any

from lore_core.briefing.sinks import register

STATE_DIR = Path.home() / ".local" / "share" / "lore"
CREDENTIALS_FILE = STATE_DIR / "matrix-credentials.json"

_REQUIRED_FIELDS = ("homeserver", "user_id", "room_id")
_FLAT_DEPRECATION_WARNED = False


def _resolve_field(
    name: str,
    env_var: str,
    config: dict[str, Any] | None,
) -> str:
    """Resolve one room field via env > nested-yaml > flat-yaml > "".

    Caller is responsible for raising on empty.
    """
    global _FLAT_DEPRECATION_WARNED
    env_value = os.environ.get(env_var, "").strip()
    if env_value:
        return env_value
    if config:
        nested = config.get("matrix") or {}
        if isinstance(nested, dict):
            v = nested.get(name)
            if isinstance(v, str) and v.strip():
                return v.strip()
        v = config.get(name)
        if isinstance(v, str) and v.strip():
            if not _FLAT_DEPRECATION_WARNED:
                warnings.warn(
                    "matrix sink: flat top-level keys in .lore-briefing.yml "
                    "are deprecated; nest under `matrix:` instead "
                    "(homeserver/user_id/room_id).",
                    DeprecationWarning,
                    stacklevel=3,
                )
                _FLAT_DEPRECATION_WARNED = True
            return v.strip()
    return ""


def _resolve_room_config(
    config: dict[str, Any] | None,
) -> tuple[str, str, str]:
    """Resolve (homeserver, user_id, room_id), erroring on missing fields."""
    homeserver = _resolve_field("homeserver", "LORE_MATRIX_HOMESERVER", config)
    user_id = _resolve_field("user_id", "LORE_MATRIX_USER_ID", config)
    room_id = _resolve_field("room_id", "LORE_MATRIX_ROOM_ID", config)
    missing = [
        name for name, val in zip(
            _REQUIRED_FIELDS, (homeserver, user_id, room_id), strict=True,
        )
        if not val
    ]
    if missing:
        raise RuntimeError(
            "matrix sink: missing required field(s) "
            f"{', '.join(missing)}. Set them in <wiki>/.lore-briefing.yml "
            "(under `matrix:`) or as env vars LORE_MATRIX_HOMESERVER / "
            "LORE_MATRIX_USER_ID / LORE_MATRIX_ROOM_ID."
        )
    return homeserver, user_id, room_id


def _load_credentials() -> dict:
    if not CREDENTIALS_FILE.exists():
        raise RuntimeError(
            f"No matrix credentials at {CREDENTIALS_FILE}. "
            "Run: lore-sink-matrix login"
        )
    return json.loads(CREDENTIALS_FILE.read_text())


def _markdown_to_html(md: str) -> str:
    """Best-effort markdown → HTML with safe fallback."""
    try:
        import markdown  # type: ignore[import-untyped]
        return markdown.markdown(md, extensions=["extra", "sane_lists"])
    except ImportError:
        import html
        return f"<pre>{html.escape(md)}</pre>"


async def _send_async(text: str, config: dict[str, Any] | None) -> None:
    from nio import AsyncClient, RoomSendResponse  # type: ignore[import-untyped]

    homeserver, _, room_id = _resolve_room_config(config)
    creds = _load_credentials()
    client = AsyncClient(homeserver, creds["user_id"])
    client.access_token = creds["access_token"]
    client.device_id = creds["device_id"]

    html_body = _markdown_to_html(text)
    try:
        response = await client.room_send(
            room_id=room_id,
            message_type="m.room.message",
            content={
                "msgtype": "m.text",
                "body": text,
                "format": "org.matrix.custom.html",
                "formatted_body": html_body,
            },
        )
        if not isinstance(response, RoomSendResponse):
            raise RuntimeError(f"matrix send failed: {response}")
    finally:
        await client.close()


def _send(target: str, text: str, config: dict[str, Any] | None) -> None:
    """Send ``text`` to the configured Matrix room. ``target`` ignored."""
    if not text.strip():
        return
    asyncio.run(_send_async(text, config))


register("matrix", _send)


# ---------------------------------------------------------------------------
# `lore-sink-matrix login` standalone CLI — kept for one-time auth setup
# ---------------------------------------------------------------------------


async def _login() -> None:
    from getpass import getpass
    from nio import AsyncClient, LoginResponse  # type: ignore[import-untyped]

    homeserver, user_id, _ = _resolve_room_config(None)
    password = getpass(f"Password for {user_id}: ")
    client = AsyncClient(homeserver, user_id)
    response = await client.login(password)
    if not isinstance(response, LoginResponse):
        print(f"Login failed: {response}", file=sys.stderr)
        await client.close()
        sys.exit(1)
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    CREDENTIALS_FILE.write_text(
        json.dumps(
            {
                "access_token": response.access_token,
                "device_id": response.device_id,
                "user_id": response.user_id,
            },
            indent=2,
        )
    )
    await client.close()
    print(f"Credentials saved to {CREDENTIALS_FILE}")


def main(argv: list[str] | None = None) -> int:
    """Entry point for ``lore-sink-matrix`` console script (login only)."""
    import argparse
    parser = argparse.ArgumentParser(prog="lore-sink-matrix")
    parser.add_argument("command", choices=["login"])
    parser.parse_args(argv)
    asyncio.run(_login())
    return 0
