"""Claude Code adapter — reads sessions via the Claude Agent SDK.

SDK APIs used (code.claude.com/docs/en/agent-sdk/sessions):
  - list_sessions(directory: Path) -> iterable[Session]
  - get_session_messages(session_id: str) -> iterable[dict]

The ``claude_agent_sdk`` package is imported *lazily* inside each method so:
  (a) this module loads cleanly even when the SDK isn't installed,
  (b) tests can monkeypatch the SDK without the real package being present,
  (c) a clear ``ImportError`` (with an install hint) surfaces only when a
      method is actually called without the SDK available.

SDK message shape assumed (Anthropic SDK convention):

.. code-block:: python

    {
        "role": "user" | "assistant" | "system",
        "content": [
            {"type": "text",        "text": "..."},
            {"type": "thinking",    "thinking": "..."},
            {"type": "tool_use",    "id": "...", "name": "...", "input": {...}},
            {"type": "tool_result", "tool_use_id": "...", "content": "...",
             "is_error": bool},
        ]
    }

One ``Turn`` is emitted per *content block*; a single assistant message that
contains ``[text, tool_use]`` therefore produces two consecutive ``Turn``\\s.
"""
from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path

from lore_core.types import ToolCall, ToolResult, TranscriptHandle, Turn


def _require_sdk():
    """Lazy import with a clear error message if the SDK isn't installed."""
    try:
        import claude_agent_sdk  # noqa: F401
    except ImportError as e:
        raise ImportError(
            "claude-agent-sdk is required for the claude-code adapter. "
            "Install with: pip install lore[capture]"
        ) from e
    import claude_agent_sdk

    return claude_agent_sdk


def _encode_project_dir(cwd: Path) -> str:
    """Encode a cwd to Claude Code's project-directory naming convention.

    Claude Code stores session .jsonl files under
    ``~/.claude/projects/<encoded-cwd>/`` where the encoding replaces
    path separators with hyphens (e.g. ``/home/x/proj`` → ``-home-x-proj``).
    """
    return str(Path(cwd).resolve()).replace("/", "-")


def _session_file_path(cwd: Path, session_id: str) -> Path:
    return Path.home() / ".claude" / "projects" / _encode_project_dir(cwd) / f"{session_id}.jsonl"


def _as_message_dict(raw) -> dict:
    """Coerce an SDK yield from get_session_messages into the dict shape curators expect.

    Current SDK yields ``SessionMessage`` dataclass objects whose ``.message``
    attribute holds the real ``{"role": ..., "content": [...]}`` dict; legacy
    SDKs yielded that dict directly. Tolerates both.
    """
    if isinstance(raw, dict):
        return raw
    inner = getattr(raw, "message", None)
    if isinstance(inner, dict):
        return inner
    return {}


def _extract_session_fields(s) -> tuple[str, Path | None, datetime | None]:
    """Extract (id, path, mtime) from an SDK session info object.

    Tolerates both the legacy field names (``id`` / ``path`` / ``mtime``)
    and the current ones (``session_id`` / computed path / ``last_modified``
    as epoch milliseconds).
    """
    session_id = getattr(s, "session_id", None) or getattr(s, "id", None)

    path = getattr(s, "path", None)
    path = Path(path) if path is not None else None

    mtime = getattr(s, "mtime", None)
    if mtime is None:
        last_modified = getattr(s, "last_modified", None)
        if isinstance(last_modified, (int, float)):
            mtime = datetime.fromtimestamp(last_modified / 1000, tz=UTC)
        else:
            mtime = last_modified

    return session_id, path, mtime


class ClaudeCodeAdapter:
    """Adapter reading Claude Code sessions via the Claude Agent SDK.

    SDK APIs used (code.claude.com/docs/en/agent-sdk/sessions):
      - list_sessions(directory: Path) -> iterable[Session]
      - get_session_messages(session_id: str) -> iterable[dict]

    Lazy import of claude_agent_sdk means this module can be imported
    without the SDK installed; calling a method raises ImportError with
    an installation hint.
    """

    host = "claude-code"

    def list_transcripts(self, directory: Path) -> list[TranscriptHandle]:
        sdk = _require_sdk()
        out = []
        for s in sdk.list_sessions(directory=directory):
            session_id, path, mtime = _extract_session_fields(s)
            if session_id is None:
                continue
            if path is None:
                path = _session_file_path(Path(directory), session_id)
            out.append(
                TranscriptHandle(
                    host=self.host,
                    id=session_id,
                    path=path,
                    cwd=Path(directory),
                    mtime=mtime,
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
        # Try hint first
        if index_hint is not None and 0 <= index_hint < len(all_turns):
            if all_turns[index_hint].content_hash() == after_hash:
                yield from all_turns[index_hint + 1 :]
                return
        # Fallback: content scan
        for i, t in enumerate(all_turns):
            if t.content_hash() == after_hash:
                yield from all_turns[i + 1 :]
                return
        # Hash not found — host mutated; yield everything (better than silent data loss)
        yield from all_turns

    def is_complete(self, handle: TranscriptHandle) -> bool:
        """True if the last message in the transcript carries a terminal signal.

        Heuristic: assume complete if we can reach the end of the message
        stream without error and the last assistant message is followed
        by either a ``ResultMessage``-like ``stop_reason`` field or no more
        messages are being appended (mtime is stable). For v1 we treat
        any successfully readable transcript as complete — live-session
        edge cases are handled via mtime comparison at the ledger layer.
        """
        try:
            turns = list(self._iter_turns(handle))
            return len(turns) > 0
        except Exception:
            return False

    def _iter_turns(self, handle: TranscriptHandle) -> Iterator[Turn]:
        """Normalise SDK messages → Turns.

        Emits one Turn per *content block* within a message, so a single
        assistant message with ``[text, tool_use]`` produces two Turns with
        consecutive indices. Role for ``tool_use`` blocks is ``"assistant"``
        with ``tool_call`` populated; role for ``tool_result`` blocks is
        ``"tool_result"`` with ``tool_result`` populated.
        """
        sdk = _require_sdk()
        index = 0
        for raw in sdk.get_session_messages(handle.id):
            msg = _as_message_dict(raw)
            role = msg.get("role", "system")
            ts = msg.get("timestamp")  # may be None; SDK may or may not provide
            content = msg.get("content")
            if isinstance(content, str):
                # Some SDKs return plain-string content
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
                # Unknown shape — stash in host_extras, skip
                yield Turn(
                    index=index,
                    timestamp=ts,
                    role=role,
                    host_extras={"raw": content},
                )
                index += 1
                continue
            for block in content:
                t = block.get("type")
                if t == "text":
                    yield Turn(
                        index=index,
                        timestamp=ts,
                        role=role,
                        text=block.get("text"),
                        host_extras={},
                    )
                elif t == "thinking":
                    yield Turn(
                        index=index,
                        timestamp=ts,
                        role="assistant",
                        reasoning=block.get("thinking"),
                        host_extras={},
                    )
                elif t == "tool_use":
                    yield Turn(
                        index=index,
                        timestamp=ts,
                        role="assistant",
                        tool_call=ToolCall(
                            name=block.get("name", ""),
                            input=block.get("input", {}),
                            id=block.get("id"),
                        ),
                        host_extras={},
                    )
                elif t == "tool_result":
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
                    # Unknown block — preserve in host_extras
                    yield Turn(
                        index=index,
                        timestamp=ts,
                        role=role,
                        host_extras={"claude_code.unknown_block": block},
                    )
                index += 1


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
