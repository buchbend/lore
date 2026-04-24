"""Claude Code adapter — reads sessions directly from transcript JSONL files.

Claude Code persists every session as
``~/.claude/projects/<encoded-cwd>/<uuid>.jsonl`` where each line is one
JSON event. This adapter scans those files on disk with no runtime
dependency on ``claude-agent-sdk`` — which keeps the capture hook out
of a multi-second cold import (jsonschema / rfc3987 / lark grammar
compilation).

Per-line structure (as of Claude Code v0.2+):

.. code-block:: json

    {
        "type": "user" | "assistant" | "attachment" | "queue-operation" | ...,
        "timestamp": "2026-04-22T...",
        "sessionId": "<uuid>",
        "message": {
            "role": "user" | "assistant" | "system",
            "content": "<text>" | [<block>, ...]
        }
    }

Only events whose top-level ``type`` is ``"user"`` or ``"assistant"``
produce turns. Each content block emits one Turn, matching the shape
documented in the adapter protocol: an assistant message containing
``[text, tool_use]`` yields two consecutive Turns.
"""
from __future__ import annotations

import json
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path

from lore_core.tool_categories import classify_tool_name
from lore_core.types import ToolCall, ToolResult, TranscriptHandle, Turn


def _encode_project_dir(cwd: Path) -> str:
    """Encode a cwd to Claude Code's project-directory naming convention.

    Claude Code stores session .jsonl files under
    ``~/.claude/projects/<encoded-cwd>/`` where the encoding replaces
    path separators with hyphens (e.g. ``/home/x/proj`` → ``-home-x-proj``).
    """
    return str(Path(cwd).resolve()).replace("/", "-")


def _projects_dir_for(cwd: Path) -> Path:
    """Where Claude Code stores transcripts for ``cwd``."""
    return Path.home() / ".claude" / "projects" / _encode_project_dir(cwd)


def _session_file_path(cwd: Path, session_id: str) -> Path:
    return _projects_dir_for(Path(cwd)) / f"{session_id}.jsonl"


def _parse_timestamp(value) -> datetime | None:
    """Coerce a JSONL timestamp to an aware datetime, else None."""
    if value is None:
        return None
    if isinstance(value, datetime):
        return value
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    if isinstance(value, (int, float)):
        try:
            ts = value / 1000 if value > 1e12 else value
            return datetime.fromtimestamp(ts, tz=UTC)
        except (OSError, OverflowError, ValueError):
            return None
    return None


def _stringify(content) -> str:
    """Tool result content can be str or a list of text blocks; normalise."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for b in content:
            if isinstance(b, dict) and b.get("type") == "text":
                parts.append(b.get("text", ""))
            else:
                parts.append(str(b))
        return "".join(parts)
    return str(content) if content is not None else ""


class ClaudeCodeAdapter:
    """Filesystem adapter for Claude Code transcript JSONL files."""

    host = "claude-code"

    def list_transcripts(self, directory: Path) -> list[TranscriptHandle]:
        projects = _projects_dir_for(Path(directory))
        if not projects.exists():
            return []
        out: list[TranscriptHandle] = []
        for p in sorted(projects.glob("*.jsonl")):
            try:
                st = p.stat()
            except OSError:
                continue
            out.append(
                TranscriptHandle(
                    host=self.host,
                    id=p.stem,
                    path=p,
                    cwd=Path(directory),
                    mtime=datetime.fromtimestamp(st.st_mtime, tz=UTC),
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
        all_turns = list(self._iter_turns(handle))
        if after_hash is None:
            yield from all_turns
            return
        if index_hint is not None and 0 <= index_hint < len(all_turns):
            if all_turns[index_hint].content_hash() == after_hash:
                yield from all_turns[index_hint + 1 :]
                return
        for i, t in enumerate(all_turns):
            if t.content_hash() == after_hash:
                yield from all_turns[i + 1 :]
                return
        # Hash not found — host mutated; yield everything (better than silent data loss)
        yield from all_turns

    def is_complete(self, handle: TranscriptHandle) -> bool:
        """True if the transcript has at least one parseable user/assistant turn."""
        try:
            turns = list(self._iter_turns(handle))
            return len(turns) > 0
        except Exception:
            return False

    def _iter_turns(self, handle: TranscriptHandle) -> Iterator[Turn]:
        """Parse Claude Code JSONL; emit one Turn per content block.

        Only ``type == "user"`` and ``type == "assistant"`` events produce
        turns. Other event types (``attachment``, ``queue-operation``,
        ``last-prompt``, ...) are skipped — they are not part of the
        turn-level narrative.

        Malformed lines are skipped silently — a single broken line must
        never abort the stream.
        """
        path = handle.path
        if not path.exists():
            return
        try:
            fp = path.open("r", encoding="utf-8", errors="replace")
        except OSError:
            return
        index = 0
        with fp:
            for raw_line in fp:
                line = raw_line.strip()
                if not line:
                    continue
                try:
                    event = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if not isinstance(event, dict):
                    continue
                etype = event.get("type")
                if etype not in ("user", "assistant"):
                    continue
                message = event.get("message")
                if not isinstance(message, dict):
                    continue
                role = message.get("role", etype)
                ts = _parse_timestamp(
                    event.get("timestamp") or message.get("timestamp")
                )
                content = message.get("content")

                if isinstance(content, str):
                    yield Turn(
                        index=index,
                        timestamp=ts,
                        role=role,
                        text=content,
                        host_extras={},
                    )
                    index += 1
                    continue
                if not isinstance(content, list):
                    yield Turn(
                        index=index,
                        timestamp=ts,
                        role=role,
                        host_extras={"raw": content},
                    )
                    index += 1
                    continue
                for block in content:
                    if not isinstance(block, dict):
                        yield Turn(
                            index=index,
                            timestamp=ts,
                            role=role,
                            host_extras={"claude_code.unknown_block": block},
                        )
                        index += 1
                        continue
                    bt = block.get("type")
                    if bt == "text":
                        yield Turn(
                            index=index,
                            timestamp=ts,
                            role=role,
                            text=block.get("text"),
                            host_extras={},
                        )
                    elif bt == "thinking":
                        yield Turn(
                            index=index,
                            timestamp=ts,
                            role="assistant",
                            reasoning=block.get("thinking"),
                            host_extras={},
                        )
                    elif bt == "tool_use":
                        tool_name = block.get("name", "")
                        yield Turn(
                            index=index,
                            timestamp=ts,
                            role="assistant",
                            tool_call=ToolCall(
                                name=tool_name,
                                input=block.get("input", {}),
                                id=block.get("id"),
                                category=classify_tool_name(self.host, tool_name),
                            ),
                            host_extras={},
                        )
                    elif bt == "tool_result":
                        yield Turn(
                            index=index,
                            timestamp=ts,
                            role="tool_result",
                            tool_result=ToolResult(
                                tool_call_id=block.get("tool_use_id"),
                                output=_stringify(block.get("content")),
                                is_error=bool(block.get("is_error", False)),
                            ),
                            host_extras={},
                        )
                    else:
                        yield Turn(
                            index=index,
                            timestamp=ts,
                            role=role,
                            host_extras={"claude_code.unknown_block": block},
                        )
                    index += 1
