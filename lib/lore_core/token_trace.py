"""Token totals per phase of one Claude Code session transcript.

Reads the transcript JSONL and splits token usage into phases. Never writes.

Rules:

- Each assistant message id counts once. Claude Code writes one line per
  content block and repeats the same ``usage`` on each line.
- A phase starts at an assistant ``tool_use`` block named ``Skill``
  (``skill:<input.skill>``) or ``Task`` / ``Agent``
  (``agent:<input.description or input.subagent_type>``).
- A message counts in the phase that is active when its first line appears.
  The message that calls a Skill or Agent belongs to the phase before it.
  The phase change applies to the messages after it.
- Tokens before the first phase count as ``main``.
- A line with ``isSidechain: true`` (subagent work) counts in the most recent
  agent phase. It never starts or ends a phase. With no agent phase yet, it
  counts in the active phase.
- Each phase start makes a new row, even when a name repeats.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

_AGENT_TOOLS = {"Task", "Agent"}


@dataclass
class PhaseTokens:
    name: str
    messages: int = 0
    input: int = 0
    output: int = 0
    cache_read: int = 0
    cache_write: int = 0

    @property
    def total(self) -> int:
        return self.input + self.output + self.cache_read + self.cache_write

    def as_dict(self) -> dict:
        return {
            "name": self.name,
            "messages": self.messages,
            "input": self.input,
            "output": self.output,
            "cache_read": self.cache_read,
            "cache_write": self.cache_write,
            "total": self.total,
        }


def _phase_name(block: dict) -> str | None:
    if block.get("type") != "tool_use":
        return None
    tool, inp = block.get("name"), block.get("input") or {}
    if tool == "Skill":
        return f"skill:{inp.get('skill', '?')}"
    if tool in _AGENT_TOOLS:
        return f"agent:{inp.get('description') or inp.get('subagent_type') or '?'}"
    return None


def _int(value: object) -> int:
    return value if isinstance(value, int) else 0


def trace_tokens(path: Path) -> list[PhaseTokens]:
    """Phases of the transcript at ``path``, in order of appearance."""
    phases = [PhaseTokens("main")]
    current = phases[0]
    last_agent: PhaseTokens | None = None
    seen: set[str] = set()

    for raw in Path(path).read_text(encoding="utf-8").splitlines():
        try:
            entry = json.loads(raw)
        except json.JSONDecodeError:
            continue
        message = entry.get("message")
        if entry.get("type") != "assistant" or not isinstance(message, dict):
            continue
        sidechain = bool(entry.get("isSidechain"))

        mid = message.get("id")
        usage = message.get("usage")
        if isinstance(usage, dict) and (mid is None or mid not in seen):
            if mid is not None:
                seen.add(mid)
            target = last_agent if sidechain and last_agent else current
            target.messages += 1
            target.input += _int(usage.get("input_tokens"))
            target.output += _int(usage.get("output_tokens"))
            target.cache_read += _int(usage.get("cache_read_input_tokens"))
            target.cache_write += _int(usage.get("cache_creation_input_tokens"))

        if sidechain:
            continue
        content = message.get("content")
        for block in content if isinstance(content, list) else []:
            name = _phase_name(block) if isinstance(block, dict) else None
            if name:
                current = PhaseTokens(name)
                phases.append(current)
                if name.startswith("agent:"):
                    last_agent = current
    return phases


def find_transcript(lore_root: Path | None, session: str) -> Path | None:
    """Resolve a direct .jsonl path or a session id to a transcript file.

    A session id is looked up in the transcript ledger first, then as
    ``~/.claude/projects/*/<session>.jsonl``.
    """
    direct = Path(session).expanduser()
    if session.endswith(".jsonl") and direct.is_file():
        return direct
    if lore_root is not None:
        from lore_core.ledger import TranscriptLedger

        for entry in TranscriptLedger(lore_root).all_entries():
            if entry.transcript_id == session and entry.path.is_file():
                return entry.path
    for hit in sorted((Path.home() / ".claude" / "projects").glob(f"*/{session}.jsonl")):
        return hit
    return None
