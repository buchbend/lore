"""Core types for passive-capture: Turn, TranscriptHandle, Scope, BlastRadius.

These are the common vocabulary every downstream module speaks.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any, Literal

Role = Literal["user", "assistant", "system", "tool_result"]


@dataclass(frozen=True)
class ToolCall:
    """A single tool invocation in an assistant turn."""

    name: str
    input: dict[str, Any]
    id: str | None = None


@dataclass(frozen=True)
class ToolResult:
    """Result of a tool invocation, returned to the model."""

    tool_call_id: str | None
    output: str
    is_error: bool = False


@dataclass(frozen=True)
class Turn:
    """A single conversational turn (user, assistant, system, or tool_result)."""

    index: int
    timestamp: datetime | None
    role: Role
    text: str | None = None
    tool_call: ToolCall | None = None
    tool_result: ToolResult | None = None
    reasoning: str | None = None
    host_extras: dict[str, Any] = field(default_factory=dict)

    def content_hash(self) -> str:
        """SHA256 of role + text + tool_call.input — deterministic across runs.

        Used by the sidecar ledger as a watermark so host-side edits to
        earlier turns don't silently desync the Kafka-style offset.
        """
        parts: list[str] = [
            self.role,
            "\0",
            self.text or "",
            "\0",
        ]

        if self.tool_call:
            parts.append(json.dumps(self.tool_call.input, sort_keys=True))
            parts.append("\0")
            parts.append(self.tool_call.name)

        canonical = "".join(parts)
        digest = hashlib.sha256(canonical.encode()).hexdigest()
        return f"sha256:{digest}"


@dataclass(frozen=True)
class TranscriptHandle:
    """Metadata + location of a transcript file on the host."""

    host: str
    id: str
    path: Path
    cwd: Path
    mtime: datetime


@dataclass(frozen=True)
class Scope:
    """A wiki scope with backend and local path configuration."""

    wiki: str
    scope: str  # colon-separated, e.g. "ccat:data-center:data-transfer"
    backend: str  # "github" | "none"
    claude_md_path: Path


class BlastRadius(Enum):
    """Curator actions classified by how hard they are to undo."""

    CREATE = "create"  # draft-true new note — safe
    EDIT_FRONTMATTER = "edit-fm"  # frontmatter-only — safe
    EDIT_BODY = "edit-body"  # body changes — medium
    SUPERSEDE = "supersede"  # Curator C — highest blast radius
