"""Matrix briefing sink.

Publishes a markdown briefing to a Matrix room. Credentials live at
``~/.local/share/lore/matrix-credentials.json`` after a one-time login
(``lore-sink-matrix login``). Room/homeserver config in env vars:

    LORE_MATRIX_HOMESERVER  — e.g. https://matrix.example.org
    LORE_MATRIX_USER_ID     — e.g. @lore-bot:matrix.example.org
    LORE_MATRIX_ROOM_ID     — e.g. !abc123:matrix.example.org

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
from pathlib import Path

from lore_core.briefing.sinks import register

STATE_DIR = Path.home() / ".local" / "share" / "lore"
CREDENTIALS_FILE = STATE_DIR / "matrix-credentials.json"


def _get_room_config() -> tuple[str, str, str]:
    homeserver = os.environ.get("LORE_MATRIX_HOMESERVER", "")
    user_id = os.environ.get("LORE_MATRIX_USER_ID", "")
    room_id = os.environ.get("LORE_MATRIX_ROOM_ID", "")
    if not all([homeserver, user_id, room_id]):
        raise RuntimeError(
            "matrix sink requires env vars LORE_MATRIX_HOMESERVER, "
            "LORE_MATRIX_USER_ID, LORE_MATRIX_ROOM_ID."
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


async def _send_async(text: str) -> None:
    from nio import AsyncClient, RoomSendResponse  # type: ignore[import-untyped]

    homeserver, _, room_id = _get_room_config()
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


def _send(target: str, text: str) -> None:
    """Send ``text`` to the configured Matrix room. ``target`` ignored."""
    if not text.strip():
        return
    asyncio.run(_send_async(text))


register("matrix", _send)


# ---------------------------------------------------------------------------
# `lore-sink-matrix login` standalone CLI — kept for one-time auth setup
# ---------------------------------------------------------------------------


async def _login() -> None:
    from getpass import getpass
    from nio import AsyncClient, LoginResponse  # type: ignore[import-untyped]

    homeserver, user_id, _ = _get_room_config()
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
