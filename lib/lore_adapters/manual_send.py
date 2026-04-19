"""Adapter for transcripts dumped from hosts without a native adapter.

Covers Cursor (when schema-unstable), Copilot-VSC, or any future
host. The user invokes `lore ingest --from <file> --host <declared>`
which calls `read_from(...)` below.

`list_transcripts` intentionally returns `[]` — manual-send is never
auto-discovered.

Input JSONL shape (one turn per line):
    {"index": 0, "role": "user", "text": "hi"}
    {"index": 1, "role": "assistant", "text": "hello"}
    {"index": 2, "role": "assistant", "reasoning": "think about X"}
    {"index": 3, "role": "assistant", "tool_call": {"name": "Read", "input": {"path": "a.py"}, "id": "t1"}}
    {"index": 4, "role": "tool_result", "tool_result": {"tool_call_id": "t1", "output": "text", "is_error": false}}

Optional first-line metadata (ignored for Turn building):
    {"_meta": {"schema_version": 1, "source_host": "cursor", "session_id": "abc"}}

Required fields per turn line: `index` (int), `role` (one of user/assistant/system/tool_result).
Other fields optional. Reject lines missing required fields with a clear `ValueError`.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from datetime import datetime
from pathlib import Path
from typing import IO, Any

from lore_core.types import ToolCall, ToolResult, TranscriptHandle, Turn


class ManualSendAdapter:
    """Adapter for transcripts dumped from hosts without a native adapter.

    Covers Cursor (when schema-unstable), Copilot-VSC, or any future
    host. The user invokes `lore ingest --from <file> --host <declared>`
    which calls `read_from(...)` below.

    `list_transcripts` intentionally returns `[]` — manual-send is never
    auto-discovered.
    """

    host = "manual-send"

    def read_from(
        self,
        source: Path | IO[str],
        cwd: Path,
        *,
        declared_host: str = "unknown",
        transcript_id: str | None = None,
    ) -> Iterator[Turn]:
        """Parse JSONL from a file path or text stream into a Turn stream.

        First line may be a metadata preamble `{"_meta": {...}}`; it is
        skipped for Turn construction. Subsequent lines must have
        `index` and `role`.
        """
        if isinstance(source, Path):
            with source.open() as fh:
                yield from self._parse(fh, declared_host)
        else:
            yield from self._parse(source, declared_host)

    def _parse(self, fh: IO[str], declared_host: str) -> Iterator[Turn]:
        for lineno, line in enumerate(fh, start=1):
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError as e:
                raise ValueError(f"manual-send: malformed JSON on line {lineno}: {e}") from e
            if "_meta" in obj:
                # metadata preamble — skip
                continue
            yield self._line_to_turn(obj, declared_host, lineno)

    def _line_to_turn(self, obj: dict[str, Any], declared_host: str, lineno: int) -> Turn:
        if "index" not in obj or "role" not in obj:
            raise ValueError(
                f"manual-send: line {lineno} missing required field (index or role)"
            )
        tc = obj.get("tool_call")
        tr = obj.get("tool_result")
        ts = obj.get("timestamp")
        timestamp = None
        if ts is not None:
            try:
                timestamp = datetime.fromisoformat(ts)
            except (TypeError, ValueError):
                timestamp = None
        return Turn(
            index=int(obj["index"]),
            timestamp=timestamp,
            role=obj["role"],
            text=obj.get("text"),
            tool_call=ToolCall(**tc) if isinstance(tc, dict) else None,
            tool_result=ToolResult(**tr) if isinstance(tr, dict) else None,
            reasoning=obj.get("reasoning"),
            host_extras={"manual_send.declared_host": declared_host},
        )

    # Protocol methods:

    def list_transcripts(self, directory: Path) -> list[TranscriptHandle]:
        return []

    def read_slice(self, handle: TranscriptHandle, from_index: int = 0) -> Iterator[Turn]:
        """Read from the path encoded in `handle.path`. Same parser as read_from."""
        for t in self.read_from(handle.path, handle.cwd, declared_host=handle.host):
            if t.index >= from_index:
                yield t

    def read_slice_after_hash(
        self,
        handle: TranscriptHandle,
        after_hash: str | None,
        index_hint: int | None = None,
    ) -> Iterator[Turn]:
        all_turns = list(self.read_from(handle.path, handle.cwd, declared_host=handle.host))
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
        yield from all_turns

    def is_complete(self, handle: TranscriptHandle) -> bool:
        """Manual-send transcripts are always complete (user-triggered dump)."""
        return True
