"""OpenCode adapter — reads the session files the Lore OpenCode plugin writes.

OpenCode keeps sessions in SQLite. The generated plugin (PRD 0022) reads a
session through the public 2.x plugin API (``ctx.session.context``) and
writes ``<sessions_dir()>/<sessionID>.jsonl``: a header line, then one
``SessionMessageInfo`` per line.

.. code-block:: json

    {"type": "session", "host": "opencode", "sessionID": "ses_..",
     "directory": "/repo", "parentID": null}

Only ``user`` and ``assistant`` messages produce turns. Each assistant
content part (``text``, ``reasoning``, ``tool``) is one turn; a finished
tool call adds a ``tool_result`` turn. Subagent sessions (``parentID`` set)
are not transcripts of their own: the token record reads them.
"""

from __future__ import annotations

import json
import os
import time
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path

from lore_core.tool_categories import classify_tool_name
from lore_core.types import ToolCall, ToolResult, TranscriptHandle, Turn

INTEGRATION = "opencode"


def sessions_dir() -> Path:
    """The Lore-owned folder the plugin writes into (``$LORE_OPENCODE_DIR`` wins)."""
    override = os.environ.get("LORE_OPENCODE_DIR")
    if override:
        return Path(override).expanduser()
    state = os.environ.get("XDG_STATE_HOME")
    base = Path(state).expanduser() if state else Path.home() / ".local" / "state"
    return base / "lore" / "opencode"


def read_header(path: Path) -> dict:
    """The header line of one session file, or ``{}``. Reads one line only."""
    try:
        with Path(path).open(encoding="utf-8", errors="replace") as fp:
            obj = json.loads(fp.readline())
    except (OSError, json.JSONDecodeError):
        return {}
    return obj if isinstance(obj, dict) and obj.get("type") == "session" else {}


def read_session(path: Path) -> tuple[dict, list[dict]]:
    """``(header, messages)`` of one session file; bad lines are skipped."""
    header: dict = {}
    messages: list[dict] = []
    try:
        text = Path(path).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return header, messages
    for raw in text.splitlines():
        try:
            obj = json.loads(raw)
        except json.JSONDecodeError:
            continue
        if not isinstance(obj, dict):
            continue
        if obj.get("type") == "session":
            header = obj
        else:
            messages.append(obj)
    return header, messages


def _ts(message: dict) -> datetime | None:
    created = (message.get("time") or {}).get("created")
    if not isinstance(created, (int, float)):
        return None
    try:
        return datetime.fromtimestamp(created / 1000, tz=UTC)
    except (OSError, OverflowError, ValueError):
        return None


def _tool_text(state: dict) -> str:
    parts = state.get("content")
    if isinstance(parts, list):
        return "".join(
            p.get("text", "") for p in parts if isinstance(p, dict) and p.get("type") == "text"
        )
    return ""


SESSION_MAX_AGE_DAYS = 7


class OpenCodeAdapter:
    """Filesystem adapter for the plugin's OpenCode session files."""

    integration = INTEGRATION

    def transcript_path_for_id(self, session_id: str, cwd: Path) -> Path | None:
        path = sessions_dir() / f"{session_id}.jsonl"
        return path if path.is_file() else None

    def prune_captured(self, ledger) -> int:
        """Remove session files older than the age limit that the index holds.

        Files the index lacks stay. Index entries are not touched.
        """
        cutoff = time.time() - SESSION_MAX_AGE_DAYS * 86400
        removed = 0
        for p in sessions_dir().glob("*.jsonl"):
            try:
                if p.stat().st_mtime >= cutoff or ledger.get(INTEGRATION, p.stem) is None:
                    continue
                p.unlink()
                removed += 1
            except OSError:
                continue
        return removed

    def list_transcripts(self, directory: Path) -> list[TranscriptHandle]:
        wanted = Path(directory).resolve()
        out: list[TranscriptHandle] = []
        for p in sorted(sessions_dir().glob("*.jsonl")):
            header = read_header(p)
            if not header or header.get("parentID"):
                continue
            where = header.get("directory")
            if not isinstance(where, str) or Path(where).resolve() != wanted:
                continue
            try:
                mtime = datetime.fromtimestamp(p.stat().st_mtime, tz=UTC)
            except OSError:
                continue
            out.append(
                TranscriptHandle(
                    integration=INTEGRATION, id=p.stem, path=p, cwd=Path(directory), mtime=mtime
                )
            )
        return out

    def read_slice(self, handle: TranscriptHandle, from_index: int = 0) -> Iterator[Turn]:
        for turn in self._iter_turns(handle):
            if turn.index >= from_index:
                yield turn

    def read_slice_after_hash(
        self,
        handle: TranscriptHandle,
        after_hash: str | None,
        index_hint: int | None = None,
    ) -> Iterator[Turn]:
        turns = list(self._iter_turns(handle))
        if after_hash is None:
            yield from turns
            return
        if (
            index_hint is not None
            and 0 <= index_hint < len(turns)
            and turns[index_hint].content_hash() == after_hash
        ):
            yield from turns[index_hint + 1 :]
            return
        for i, t in enumerate(turns):
            if t.content_hash() == after_hash:
                yield from turns[i + 1 :]
                return
        yield from turns  # hash not found: the host rewrote history; better than losing turns

    def is_complete(self, handle: TranscriptHandle) -> bool:
        return any(True for _ in self._iter_turns(handle))

    def _iter_turns(self, handle: TranscriptHandle) -> Iterator[Turn]:
        _, messages = read_session(handle.path)
        index = 0
        for msg in messages:
            ts = _ts(msg)
            kind = msg.get("type")
            if kind == "user":
                yield Turn(index=index, timestamp=ts, role="user", text=msg.get("text"))
                index += 1
            elif kind == "assistant":
                for part in msg.get("content") or []:
                    if not isinstance(part, dict):
                        continue
                    for turn in self._part_turns(part, ts):
                        yield Turn(index=index, **turn)
                        index += 1

    def _part_turns(self, part: dict, ts: datetime | None) -> Iterator[dict]:
        kind = part.get("type")
        if kind == "text":
            yield {"timestamp": ts, "role": "assistant", "text": part.get("text")}
        elif kind == "reasoning":
            yield {"timestamp": ts, "role": "assistant", "reasoning": part.get("text")}
        elif kind == "tool":
            state = part.get("state") if isinstance(part.get("state"), dict) else {}
            name = part.get("name") or ""
            yield {
                "timestamp": ts,
                "role": "assistant",
                "tool_call": ToolCall(
                    name=name,
                    input=state.get("input") if isinstance(state.get("input"), dict) else {},
                    id=part.get("id"),
                    category=classify_tool_name(INTEGRATION, name),
                ),
            }
            if state.get("status") in ("completed", "error"):
                yield {
                    "timestamp": ts,
                    "role": "tool_result",
                    "tool_result": ToolResult(
                        tool_call_id=part.get("id"),
                        output=_tool_text(state)
                        or str((state.get("error") or {}).get("message", "")),
                        is_error=state.get("status") == "error",
                    ),
                }
