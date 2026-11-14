"""Synthesized OpenCode 2.x session files for the adapter and token tests.

Shapes follow ``SessionMessageInfo`` in ``@opencode/client`` 2.0.32 (the
return type of ``ctx.session.context``). No live OpenCode run produced them.
The first line is the header the Lore plugin writes.
"""

from __future__ import annotations

import json
from pathlib import Path


def user(text: str, created: int = 1_700_000_000_000, mid: str = "msg_u1") -> dict:
    return {"type": "user", "id": mid, "time": {"created": created}, "text": text}


def assistant(
    mid: str,
    content: list[dict],
    *,
    tokens: tuple[int, int, int, int, int] = (0, 0, 0, 0, 0),
    cost: float | None = None,
    model: str = "claude-sonnet-5-5",
    created: int = 1_700_000_001_000,
) -> dict:
    inp, out, reasoning, read, write = tokens
    msg = {
        "type": "assistant",
        "id": mid,
        "agent": "build",
        "model": {"id": model, "providerID": "anthropic"},
        "time": {"created": created, "completed": created + 500},
        "content": content,
        "tokens": {
            "input": inp,
            "output": out,
            "reasoning": reasoning,
            "cache": {"read": read, "write": write},
        },
    }
    if cost is not None:
        msg["cost"] = cost
    return msg


def text(t: str) -> dict:
    return {"type": "text", "text": t}


def tool(name: str, call_id: str, tool_input: dict, output: str = "ok") -> dict:
    return {
        "type": "tool",
        "id": call_id,
        "name": name,
        "state": {
            "status": "completed",
            "input": tool_input,
            "content": [{"type": "text", "text": output}],
        },
        "time": {"created": 1_700_000_002_000},
    }


def write_session(
    folder: Path,
    session_id: str,
    directory: Path,
    messages: list[dict],
    *,
    parent: str | None = None,
) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    header = {
        "type": "session",
        "host": "opencode",
        "sessionID": session_id,
        "directory": str(directory),
        "parentID": parent,
    }
    path = folder / f"{session_id}.jsonl"
    path.write_text("\n".join(json.dumps(x) for x in [header, *messages]) + "\n")
    return path


def idle(mid: str = "msg_i1") -> dict:
    return {
        "type": "idle",
        "id": mid,
        "time": {"created": 1_700_000_003_000},
        "outcome": "succeeded",
    }
