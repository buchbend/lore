"""Cursor agent-transcripts adapter.

Reads Cursor's first-party Agent Mode transcripts from disk:

    ~/.cursor/projects/<slug>/agent-transcripts/<agent-uuid>/<agent-uuid>.jsonl

Where ``<slug>`` is the workspace path with ``/`` → ``-`` and the leading
``/`` stripped. Each JSONL file is one agent session; each line is one
message record of shape::

    {"id": "<seq>",
     "role": "user" | "assistant",
     "content": [
         {"type": "text",      "text": "..."},
         {"type": "tool-call", "toolCallId": "...", "toolName": "...", "args": {...}},
         {"type": "tool-result","toolCallId": "...", "result": "..."}
     ]}

The slug → workspace-absolute-path mapping is ambiguous in principle
(hyphens in original paths look identical to separators), but lore's
scope resolver can match an attached wiki against slug prefixes.

Cursor also exposes a second, older surface at
``~/.cursor/chats/<ws>/<agent>/store.db`` and a legacy bundle in
``~/.config/Cursor/User/globalStorage/state.vscdb``. Both are deferred
to Plan 3.5.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path

from lore_core.types import ToolCall, ToolResult, TranscriptHandle, Turn


def _slug_for_cwd(cwd: Path) -> str:
    """Encode a workspace absolute path to Cursor's project-dir slug."""
    resolved = str(cwd.resolve())
    if resolved.startswith("/"):
        resolved = resolved[1:]
    return resolved.replace("/", "-")


def _cursor_projects_dir() -> Path:
    return Path.home() / ".cursor" / "projects"


def _slug_matches_cwd(slug: str, cwd: Path) -> bool:
    """True if a slug could correspond to cwd.

    Slug encoding is lossy (both ``/`` and ``-`` become ``-``), so we use
    prefix-match against the exact encoding of cwd.
    """
    return slug == _slug_for_cwd(cwd)


class CursorAgentAdapter:
    """Adapter for Cursor Agent Mode transcripts.

    One TranscriptHandle per ``<uuid>.jsonl`` file under the project's
    ``agent-transcripts/`` directory. Messages are streamed line-by-line;
    each content block within a message produces one ``Turn`` with a
    monotonically-increasing ``index``, matching the Claude adapter's
    convention.
    """

    host = "cursor"

    def list_transcripts(self, directory: Path) -> list[TranscriptHandle]:
        slug = _slug_for_cwd(Path(directory))
        project_dir = _cursor_projects_dir() / slug
        transcripts_dir = project_dir / "agent-transcripts"
        if not transcripts_dir.is_dir():
            return []
        out: list[TranscriptHandle] = []
        for agent_dir in sorted(transcripts_dir.iterdir()):
            if not agent_dir.is_dir():
                continue
            # Expected: one JSONL named after the agent UUID.
            for jsonl in agent_dir.glob("*.jsonl"):
                try:
                    mtime = datetime.fromtimestamp(jsonl.stat().st_mtime, tz=UTC)
                except OSError:
                    continue
                out.append(
                    TranscriptHandle(
                        host=self.host,
                        id=agent_dir.name,
                        path=jsonl,
                        cwd=Path(directory),
                        mtime=mtime,
                    )
                )
        return out

    def read_slice(
        self, handle: TranscriptHandle, from_index: int = 0
    ) -> Iterator[Turn]:
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
                yield from all_turns[index_hint + 1:]
                return
        for i, t in enumerate(all_turns):
            if t.content_hash() == after_hash:
                yield from all_turns[i + 1:]
                return
        # Hash not found — yield everything rather than dropping data silently.
        yield from all_turns

    def is_complete(self, handle: TranscriptHandle) -> bool:
        try:
            return any(True for _ in self._iter_turns(handle))
        except Exception:
            return False

    def _iter_turns(self, handle: TranscriptHandle) -> Iterator[Turn]:
        path = handle.path
        if not path.exists():
            return
        index = 0
        try:
            raw_text = path.read_text(errors="replace")
        except OSError:
            return
        for line in raw_text.splitlines():
            stripped = line.strip()
            if not stripped:
                continue
            try:
                msg = json.loads(stripped)
            except json.JSONDecodeError:
                continue
            if not isinstance(msg, dict):
                continue

            role = msg.get("role", "system")
            ts = _parse_ts(msg.get("timestamp") or msg.get("createdAt"))
            content = msg.get("content")

            if isinstance(content, str):
                yield Turn(
                    index=index, timestamp=ts, role=role, text=content,
                    host_extras={},
                )
                index += 1
                continue

            if not isinstance(content, list):
                yield Turn(
                    index=index, timestamp=ts, role=role,
                    host_extras={"cursor.raw_content": content},
                )
                index += 1
                continue

            for block in content:
                if not isinstance(block, dict):
                    yield Turn(
                        index=index, timestamp=ts, role=role,
                        host_extras={"cursor.raw_block": block},
                    )
                    index += 1
                    continue
                btype = block.get("type")
                if btype == "text":
                    yield Turn(
                        index=index, timestamp=ts, role=role,
                        text=block.get("text"),
                        host_extras={},
                    )
                elif btype in ("tool-call", "tool_use"):
                    yield Turn(
                        index=index, timestamp=ts, role="assistant",
                        tool_call=ToolCall(
                            name=block.get("toolName") or block.get("name", ""),
                            input=block.get("args") or block.get("input", {}),
                            id=block.get("toolCallId") or block.get("id"),
                        ),
                        host_extras={},
                    )
                elif btype in ("tool-result", "tool_result"):
                    yield Turn(
                        index=index, timestamp=ts, role="tool_result",
                        tool_result=ToolResult(
                            tool_call_id=block.get("toolCallId") or block.get("tool_use_id"),
                            output=_stringify(block.get("result") or block.get("content")),
                            is_error=bool(block.get("isError") or block.get("is_error", False)),
                        ),
                        host_extras={},
                    )
                elif btype in ("thinking", "reasoning"):
                    yield Turn(
                        index=index, timestamp=ts, role="assistant",
                        reasoning=block.get("text") or block.get("thinking"),
                        host_extras={},
                    )
                else:
                    yield Turn(
                        index=index, timestamp=ts, role=role,
                        host_extras={"cursor.unknown_block": block},
                    )
                index += 1


def _parse_ts(raw) -> datetime | None:
    if raw is None:
        return None
    if isinstance(raw, (int, float)):
        # Epoch ms (common in VSCode-family)
        if raw > 1e12:
            return datetime.fromtimestamp(raw / 1000, tz=UTC)
        return datetime.fromtimestamp(raw, tz=UTC)
    if isinstance(raw, str):
        try:
            parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        except ValueError:
            return None
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)
    return None


def _stringify(content) -> str:
    if content is None:
        return ""
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
    return str(content)
