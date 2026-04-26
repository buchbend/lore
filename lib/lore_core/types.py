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

ToolCategory = Literal[
    "file_edit",       # write/modify source — Edit, Write, NotebookEdit, Cursor-Edit, …
    "file_read",       # read a file — Read, Cursor-Open, LSP definition, …
    "search",          # structural/text search — Grep, Glob, Cursor-Find, LSP symbols, …
    "shell_exec",      # command execution — Bash, Cursor terminal, …
    "agent_spawn",     # delegate work — Task, MCP-agent-invoke, …
    "plan_exit",       # explicit plan-approval transition — ExitPlanMode, …
    "version_control", # commits/branches/PRs when surfaced as a dedicated tool
    "other",           # unclassified — neutral signal
]


@dataclass(frozen=True)
class ToolCall:
    """A single tool invocation in an assistant turn.

    ``category`` is an integration-agnostic classification populated by the
    adapter from ``name`` via
    :func:`lore_core.tool_categories.classify_tool_name`.
    Downstream consumers (noteworthy cascade, surface gen, cross-host
    knowledge graph) operate on ``category`` so they don't need to know
    that Claude Code says ``Edit`` while Cursor says ``edit_file``.
    """

    name: str
    input: dict[str, Any]
    id: str | None = None
    category: ToolCategory = "other"


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
    integration_extras: dict[str, Any] = field(default_factory=dict)

    def content_hash(self) -> str:
        """SHA256 of role + text + tool_call.input — deterministic across runs.

        Used by the sidecar ledger as a watermark so integration-side edits
        to earlier turns don't silently desync the Kafka-style offset.
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
    """Metadata + location of a transcript file on the host machine."""

    integration: str
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
