"""Matrix briefing sink.

Publishes a markdown briefing to a Matrix room. Credentials live at
`~/.local/share/lore/matrix-credentials.json` (after one-time login).
Room / homeserver config lives in each wiki's `.lore-briefing.yml` or
environment variables.

    # One-time login (interactive)
    python -m lore_sinks.matrix login

    # Publish briefing from stdin
    echo "## Briefing ..." | python -m lore_sinks.matrix send

    # Publish briefing from file
    python -m lore_sinks.matrix send --file briefing.md

Environment variables (override config):
    LORE_MATRIX_HOMESERVER  — e.g. https://matrix.example.org
    LORE_MATRIX_USER_ID     — e.g. @lore-bot:matrix.example.org
    LORE_MATRIX_ROOM_ID     — e.g. !abc123:matrix.example.org
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from getpass import getpass
from pathlib import Path

STATE_DIR = Path.home() / ".local" / "share" / "lore"
CREDENTIALS_FILE = STATE_DIR / "matrix-credentials.json"


def _get_config() -> tuple[str, str, str]:
    homeserver = os.environ.get("LORE_MATRIX_HOMESERVER", "")
    user_id = os.environ.get("LORE_MATRIX_USER_ID", "")
    room_id = os.environ.get("LORE_MATRIX_ROOM_ID", "")
    if not all([homeserver, user_id, room_id]):
        print(
            "Error: set LORE_MATRIX_HOMESERVER, LORE_MATRIX_USER_ID, "
            "LORE_MATRIX_ROOM_ID in environment. "
            "(Load from .lore-briefing.yml via your wrapper if needed.)",
            file=sys.stderr,
        )
        sys.exit(1)
    return homeserver, user_id, room_id


def _load_credentials() -> dict:
    if not CREDENTIALS_FILE.exists():
        print(
            f"No credentials at {CREDENTIALS_FILE}. "
            "Run: python -m lore_sinks.matrix login",
            file=sys.stderr,
        )
        sys.exit(1)
    return json.loads(CREDENTIALS_FILE.read_text())


def _make_client():
    from nio import AsyncClient  # type: ignore[import-untyped]

    homeserver, _, _ = _get_config()
    creds = _load_credentials()
    client = AsyncClient(homeserver, creds["user_id"])
    client.access_token = creds["access_token"]
    client.device_id = creds["device_id"]
    return client


async def _login() -> None:
    from nio import AsyncClient, LoginResponse  # type: ignore[import-untyped]

    homeserver, user_id, _ = _get_config()
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


def _markdown_to_html(md: str) -> str:
    """Best-effort markdown → HTML with safe fallback."""
    try:
        import markdown  # type: ignore[import-untyped]

        return markdown.markdown(md, extensions=["extra", "sane_lists"])
    except ImportError:
        import html

        return f"<pre>{html.escape(md)}</pre>"


async def _send(text: str) -> None:
    from nio import RoomSendResponse  # type: ignore[import-untyped]

    _, _, room_id = _get_config()
    client = _make_client()
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
            print(f"Send failed: {response}", file=sys.stderr)
            sys.exit(1)
        print(f"Published to {room_id}", file=sys.stderr)
    finally:
        await client.close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="lore-sink-matrix", description=__doc__)
    parser.add_argument("command", choices=["login", "send"])
    parser.add_argument("--file", help="Input file (default: stdin, for send)")
    args = parser.parse_args(argv)

    if args.command == "login":
        asyncio.run(_login())
        return 0

    if args.file:
        text = Path(args.file).read_text()
    else:
        text = sys.stdin.read()

    if not text.strip():
        print("Nothing to send (empty input).", file=sys.stderr)
        return 1

    asyncio.run(_send(text))
    return 0


if __name__ == "__main__":
    sys.exit(main())
