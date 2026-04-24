"""Noteworthy filter — classify a Turn slice via the LLM."""
from __future__ import annotations

import json
import os
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable, Literal, Protocol

from lore_core.noteworthy_features import CascadeVerdict, classify_cascade
from lore_core.redaction import redact
from lore_core.types import Turn

if TYPE_CHECKING:
    from lore_core.run_log import RunLogger


NoteworthyMode = Literal["llm_only", "cascade"]
# "offline" (Phase E) deliberately absent — no code path yet honours it,
# so accepting it from the env var would silently misbehave.
_VALID_MODES: frozenset[str] = frozenset({"llm_only", "cascade"})

# Default promoted from "llm_only" to "cascade" in v0.6.0 after real-
# traffic shadow-run agreement showed zero false-positives / false-
# negatives on a 15-slice sample. Operators can still flip back via
# LORE_NOTEWORTHY_MODE=llm_only or curator.noteworthy_mode in the
# root config.
_DEFAULT_MODE: NoteworthyMode = "cascade"


def _resolve_mode(lore_root: Path | None = None) -> NoteworthyMode:
    """Pick the cascade mode: env var > root config > default.

    Env var wins so operators can flip a single process without editing
    the config file. Config wins over the default so per-install policy
    can differ from the shipped default. Invalid values at any layer
    fall through to the next layer (never crash).
    """
    raw = os.environ.get("LORE_NOTEWORTHY_MODE", "").strip().lower()
    if raw in _VALID_MODES:
        return raw  # type: ignore[return-value]

    if lore_root is not None:
        try:
            from lore_core.root_config import load_root_config
            configured = load_root_config(lore_root).curator.noteworthy_mode
            if isinstance(configured, str) and configured.strip().lower() in _VALID_MODES:
                return configured.strip().lower()  # type: ignore[return-value]
        except Exception:
            pass

    return _DEFAULT_MODE


_SIMPLE_TIER_WARNING_ID = "noteworthy-simple-tier-v1"

# Prompt-size budgets. Exceeding the total triggers tail-biased truncation
# (older turns collapsed into an elision marker; most recent turns retained)
# so that a single long transcript can't produce an impossible prompt.
# Overridable via env for operator experimentation.
_DEFAULT_MAX_PROMPT_CHARS = 80_000   # ~20k tokens at a ~4 chars/token ratio
_DEFAULT_MAX_PER_TURN_CHARS = 4_000  # cap any single text/tool_result block


def _resolve_budget(name: str, default: int) -> int:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError:
        return default
    return value if value > 0 else default


@dataclass(frozen=True)
class NoteworthyResult:
    noteworthy: bool
    reason: str                     # short — "single-shot bash question" | "substantive refactor"
    title: str                      # 5-10 words
    summary: str = ""               # 2-3 sentences of substance
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

    # Feature cascade always runs — its verdict is emitted for shadow-run
    # calibration even in llm_only mode. Only in "cascade" mode does a
    # trivial verdict actually skip the LLM call; substantive and uncertain
    # still hit the LLM (substantive for summary quality, uncertain for the
    # verdict itself). Thresholds live in lore_core.noteworthy_features.
    mode = _resolve_mode(lore_root=lore_root)
    verdict = classify_cascade(turns)
    if logger is not None:
        logger.emit(
            "cascade-verdict",
            transcript_id=transcript_id,
            label=verdict.label,
            reason=verdict.reason,
            mode=mode,
            features=asdict(verdict.features),
        )

    if mode == "cascade" and verdict.label == "trivial":
        # Hard-skip: no LLM call, no summary. Ledger advances as not-noteworthy
        # via the caller's noteworthy=False path.
        return NoteworthyResult(
            noteworthy=False,
            reason=f"cascade_trivial:{verdict.reason}",
            title="",
            summary="",
        )

    model = model_resolver(tier)
    redaction_log_path = (
        (lore_root / ".lore" / "redaction.log") if lore_root is not None else None
    )
    prompt_text = _build_prompt_text(turns, redaction_log_path=redaction_log_path)
    prompt_chars = len(prompt_text)
    prompt_messages = [{"role": "user", "content": prompt_text}]

    # Request structured output via tool_use
    tool_schema = _classify_tool_schema()

    if logger is not None:
        logger.emit(
            "llm-prompt",
            call="noteworthy",
            tier=tier,
            prompt_chars=prompt_chars,
            turns_in_slice=len(turns),
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

    if logger is not None:
        try:
            body = resp.content[0].text if resp.content else ""
        except Exception:
            body = ""
        logger.emit(
            "llm-response",
            call="noteworthy",
            prompt_chars=prompt_chars,
            usage=_usage_to_dict(getattr(resp, "usage", None)),
            cost_usd=getattr(resp, "total_cost_usd", None),
            body=body,
            result=data,
        )

    if logger is not None:
        logger.emit(
            "noteworthy",
            transcript_id=transcript_id,
            verdict=result.noteworthy,
            reason=result.reason,
            tier=tier,
            latency_ms=latency_ms,
            prompt_chars=prompt_chars,
        )

    return result


def _usage_to_dict(usage: Any) -> dict[str, int]:
    """Coerce an Anthropic/OpenAI-style usage object into a plain dict."""
    if usage is None:
        return {}
    if isinstance(usage, dict):
        return {k: v for k, v in usage.items() if isinstance(v, int)}
    out: dict[str, int] = {}
    for attr in ("input_tokens", "output_tokens", "total_tokens",
                 "prompt_tokens", "completion_tokens"):
        value = getattr(usage, attr, None)
        if isinstance(value, int):
            out[attr] = value
    return out


def _build_prompt_text(
    turns: list[Turn],
    *,
    redaction_log_path: Path | None = None,
    max_prompt_chars: int | None = None,
    max_per_turn_chars: int | None = None,
) -> str:
    """Build a prompt summarising the slice — text, tool calls, tool results.

    Drops thinking blocks, collapses tool results to line counts, runs
    :func:`redact` over free-form text so secrets never reach the LLM, caps
    individual turns at ``max_per_turn_chars``, and — when the total would
    exceed ``max_prompt_chars`` — keeps the most recent turns that fit and
    summarises the rest with a single elision marker. Tail-biased because
    noteworthy classification leans on recency.
    """
    total_budget = max_prompt_chars or _resolve_budget(
        "LORE_NOTEWORTHY_MAX_PROMPT_CHARS", _DEFAULT_MAX_PROMPT_CHARS
    )
    per_turn_budget = max_per_turn_chars or _resolve_budget(
        "LORE_NOTEWORTHY_MAX_PER_TURN_CHARS", _DEFAULT_MAX_PER_TURN_CHARS
    )

    header = [
        "Classify whether this conversation slice contains substantive "
        "work worth capturing as a session note. Return JSON via the "
        "`classify` tool. Be conservative about `noteworthy`: only false "
        "for single-shot tool questions or trivial edits without decisions.",
        "",
        "--- transcript slice ---",
    ]

    turn_lines: list[str] = []
    for t in turns:
        line = _format_turn_line(t, redaction_log_path, per_turn_budget)
        if line is not None:
            turn_lines.append(line)

    body = _tail_biased_truncate(turn_lines, total_budget)
    return "\n".join(header + body)


def _format_turn_line(
    t: Turn,
    redaction_log_path: Path | None,
    per_turn_budget: int,
) -> str | None:
    """Render one Turn as a single prompt line, or None to skip it."""
    if t.reasoning is not None:
        return None  # drop thinking
    if t.text is not None:
        redacted, _ = redact(t.text, log_path=redaction_log_path)
        return f"[{t.role}] {_cap(redacted, per_turn_budget)}"
    if t.tool_call is not None:
        return (
            f"[tool_call:{t.tool_call.name}] "
            f"{json.dumps(t.tool_call.input)[:200]}"
        )
    if t.tool_result is not None:
        output = t.tool_result.output or ""
        redact(output, log_path=redaction_log_path)  # log-only; output collapsed below
        n = output.count("\n") + 1
        return f"[tool_result] <{n} lines>"
    return None


def _cap(text: str, limit: int) -> str:
    """Truncate text to ``limit`` chars, appending a marker when elided."""
    if len(text) <= limit:
        return text
    keep = max(limit - 40, 0)
    elided = len(text) - keep
    return text[:keep] + f" ...<+{elided} chars elided>"


def _tail_biased_truncate(turn_lines: list[str], budget: int) -> list[str]:
    """Keep the most recent turns that fit in ``budget``; marker for the rest.

    Returns ``turn_lines`` unchanged when already under budget. Otherwise
    walks the list from the tail, accumulating lines until the next one
    would overflow, then prepends a single ``[... N earlier turns elided ...]``
    line so the model knows context was dropped.
    """
    total = sum(len(x) + 1 for x in turn_lines)  # +1 for join newline
    if total <= budget:
        return turn_lines

    marker_template = "[... {n} earlier turns elided for prompt budget ...]"
    marker_reserve = len(marker_template.format(n=len(turn_lines)))
    effective = max(budget - marker_reserve, 0)

    tail: list[str] = []
    size = 0
    for line in reversed(turn_lines):
        line_cost = len(line) + 1
        if size + line_cost > effective and tail:
            break
        tail.insert(0, line)
        size += line_cost

    elided = len(turn_lines) - len(tail)
    if elided <= 0:
        return turn_lines
    return [marker_template.format(n=elided)] + tail


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
                "summary": {
                    "type": "string",
                    "description": "2-3 sentence summary of what was accomplished, decided, or changed. Focus on substance, not mechanics.",
                },
                "bullets": {"type": "array", "items": {"type": "string"}},
                "files_touched": {"type": "array", "items": {"type": "string"}},
                "entities": {"type": "array", "items": {"type": "string"}},
                "decisions": {"type": "array", "items": {"type": "string"}},
            },
            "required": ["noteworthy", "reason", "title", "summary"],
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
        summary=str(data.get("summary", "")),
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
