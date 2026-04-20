"""Noteworthy filter — classify a Turn slice via the LLM."""
from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable, Protocol

from lore_core.types import Turn

if TYPE_CHECKING:
    from lore_core.run_log import RunLogger


_SIMPLE_TIER_WARNING_ID = "noteworthy-simple-tier-v1"


@dataclass(frozen=True)
class NoteworthyResult:
    noteworthy: bool
    reason: str                     # short — "single-shot bash question" | "substantive refactor"
    title: str                      # 5-10 words
    bullets: list[str] = field(default_factory=list)   # 3-5 items, short phrases
    files_touched: list[str] = field(default_factory=list)
    entities: list[str] = field(default_factory=list)  # wikilink candidates
    decisions: list[str] = field(default_factory=list) # one-liners; empty list if none


class AnthropicClientProtocol(Protocol):
    """Minimal shape needed; real anthropic.Anthropic satisfies it.

    Only the `messages.create(...)` method is used, with `tools` for
    structured output via tool_use.
    """
    messages: Any


def classify_slice(
    turns: list[Turn],
    *,
    tier: str = "middle",                         # "middle" | "simple"
    model_resolver: Callable[[str], str],         # e.g. lambda t: cfg.models.middle
    anthropic_client: AnthropicClientProtocol,
    lore_root: Path | None = None,
    logger: "RunLogger | None" = None,
    transcript_id: str | None = None,
) -> NoteworthyResult:
    """Classify a Turn slice via the LLM.

    Middle tier by default (recall > cost). Simple tier emits a loud
    first-run warning and is recommended only for backfill.

    Prompt-side truncation: thinking blocks dropped; long tool results
    replaced with `<tool {name} returned {n} lines>` placeholders.
    """
    if tier == "simple":
        _emit_simple_tier_warning_once(lore_root)
    elif tier != "middle":
        raise ValueError(f"noteworthy: unknown tier {tier!r} (expected 'middle' or 'simple')")

    model = model_resolver(tier)
    prompt_text = _build_prompt_text(turns)
    prompt_messages = [{"role": "user", "content": prompt_text}]

    # Request structured output via tool_use
    tool_schema = _classify_tool_schema()

    if logger is not None and logger.trace_enabled:
        logger.emit(
            "llm-prompt",
            call="noteworthy",
            tier=tier,
            token_count=0,
            messages=prompt_messages,
        )

    t_before = time.monotonic()
    resp = anthropic_client.messages.create(
        model=model,
        max_tokens=1024,
        tools=[tool_schema],
        tool_choice={"type": "tool", "name": "classify"},
        messages=prompt_messages,
    )
    latency_ms = int((time.monotonic() - t_before) * 1000)

    # Extract the tool_use block's input — that's our structured result
    data = _extract_tool_input(resp)
    result = _data_to_result(data)

    if logger is not None and logger.trace_enabled:
        try:
            body = resp.content[0].text if resp.content else ""
        except Exception:
            body = ""
        logger.emit(
            "llm-response",
            call="noteworthy",
            token_count=len(body),
            body=body,
        )

    if logger is not None:
        logger.emit(
            "noteworthy",
            transcript_id=transcript_id,
            verdict=result.noteworthy,
            reason=result.reason,
            tier=tier,
            latency_ms=latency_ms,
        )

    return result


def _build_prompt_text(turns: list[Turn]) -> str:
    """Build a prompt summarising the slice — text, tool calls, tool results.

    Drops thinking blocks. Truncates tool results to a line-count summary.
    """
    lines: list[str] = [
        "Classify whether this conversation slice contains substantive "
        "work worth capturing as a session note. Return JSON via the "
        "`classify` tool. Be conservative about `noteworthy`: only false "
        "for single-shot tool questions or trivial edits without decisions.",
        "",
        "--- transcript slice ---",
    ]
    for t in turns:
        if t.reasoning is not None:
            continue  # drop thinking
        if t.text is not None:
            lines.append(f"[{t.role}] {t.text}")
        elif t.tool_call is not None:
            lines.append(f"[tool_call:{t.tool_call.name}] {json.dumps(t.tool_call.input)[:200]}")
        elif t.tool_result is not None:
            n = t.tool_result.output.count("\n") + 1
            lines.append(f"[tool_result] <{n} lines>")
    return "\n".join(lines)


def _classify_tool_schema() -> dict[str, Any]:
    return {
        "name": "classify",
        "description": "Emit the classification for this slice.",
        "input_schema": {
            "type": "object",
            "properties": {
                "noteworthy": {"type": "boolean"},
                "reason": {"type": "string"},
                "title": {"type": "string"},
                "bullets": {"type": "array", "items": {"type": "string"}},
                "files_touched": {"type": "array", "items": {"type": "string"}},
                "entities": {"type": "array", "items": {"type": "string"}},
                "decisions": {"type": "array", "items": {"type": "string"}},
            },
            "required": ["noteworthy", "reason", "title"],
        },
    }


def _extract_tool_input(resp: Any) -> dict[str, Any]:
    """Pull the classify tool's input dict from an Anthropic Messages response."""
    for block in getattr(resp, "content", []):
        btype = getattr(block, "type", None) or (block.get("type") if isinstance(block, dict) else None)
        if btype == "tool_use":
            inp = getattr(block, "input", None)
            if inp is None and isinstance(block, dict):
                inp = block.get("input")
            if isinstance(inp, dict):
                return inp
    raise ValueError("noteworthy: no tool_use block in response")


def _data_to_result(data: dict[str, Any]) -> NoteworthyResult:
    return NoteworthyResult(
        noteworthy=bool(data.get("noteworthy")),
        reason=str(data.get("reason", "")),
        title=str(data.get("title", "")),
        bullets=list(data.get("bullets", [])),
        files_touched=list(data.get("files_touched", [])),
        entities=list(data.get("entities", [])),
        decisions=list(data.get("decisions", [])),
    )


def _emit_simple_tier_warning_once(lore_root: Path | None) -> None:
    """Write a one-time warning to `<lore_root>/.lore/warnings.log` if not seen."""
    if lore_root is None:
        return
    log_path = lore_root / ".lore" / "warnings.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    if log_path.exists():
        text = log_path.read_text()
        if _SIMPLE_TIER_WARNING_ID in text:
            return
    msg = (
        f"[{_SIMPLE_TIER_WARNING_ID}] Using simple tier for noteworthy filter "
        "— some substantive session slices may be silently dropped as "
        "not-noteworthy. Recommended only for bulk backfill where cost dominates."
    )
    with log_path.open("a") as f:
        f.write(msg + "\n")
