"""LLM-driven summary/description merge for Curator A appends.

When a chunk merges into an existing session note, the writer needs to
update the body ``## Summary`` paragraph and the frontmatter
``description`` so they reflect the *combined* arc of the session — not
just the latest chunk's framing (which would erase the morning's
context) and not just the existing summary (which would silently drop
the afternoon's progress).

The merge prompt is constrained: keep the existing summary as the
anchor, weave in the new chunk's information, stay 1-2 sentences. On
any LLM failure the caller falls back to the existing summary —
additive contract, never blank out what's already there.
"""
from __future__ import annotations

import time
from typing import TYPE_CHECKING, Any

from lore_curator.llm_client import LlmClient, LlmClientError

if TYPE_CHECKING:
    from lore_core.run_log import RunLogger


_MAX_OUTPUT_TOKENS = 512


def merge_descriptions(
    *,
    existing: str,
    new: str,
    new_bullets: list[str] | None = None,
    new_decisions: list[str] | None = None,
    llm_client: LlmClient,
    model: str,
    logger: "RunLogger | None" = None,
    transcript_id: str | None = None,
) -> str:
    """Compose a merged session-note summary that retains the existing
    framing as the anchor and works the new chunk's context in.

    Returns the merged 1-2 sentence summary string. Both the body
    ``## Summary`` paragraph and the frontmatter ``description`` are set
    to this value (passive capture mirrors the two — see
    ``session_filer._sections_from_noteworthy`` where ``summary`` is
    sourced from ``noteworthy.description``).

    Short-circuits without an LLM call when there's nothing to merge:

    * Empty ``new`` → existing wins (no signal to add).
    * Empty ``existing`` → new wins (nothing to anchor against; this is
      effectively a first-chunk filing path that masquerades as merge).
    * Identical strings → existing wins (no change to make).

    On LLM failure (``LlmClientError``, malformed tool_use response,
    empty merged output) returns ``existing`` unchanged. Callers should
    not treat a returned-existing as a signal that merging happened —
    it's the safe fallback that preserves the additive contract.
    """
    if not new:
        return existing
    if not existing:
        return new
    if existing.strip() == new.strip():
        return existing

    bullets = new_bullets or []
    decisions = new_decisions or []

    prompt_text = _build_prompt_text(
        existing=existing,
        new=new,
        new_bullets=bullets,
        new_decisions=decisions,
    )
    tool_schema = _merge_tool_schema()

    if logger is not None:
        logger.emit(
            "llm-prompt",
            call="summary-merge",
            transcript_id=transcript_id,
            prompt_chars=len(prompt_text),
            existing_chars=len(existing),
            new_chars=len(new),
        )

    t_before = time.monotonic()
    try:
        resp = llm_client.messages.create(
            model=model,
            max_tokens=_MAX_OUTPUT_TOKENS,
            tools=[tool_schema],
            tool_choice={"type": "tool", "name": "merge_summary"},
            messages=[{"role": "user", "content": prompt_text}],
        )
    except LlmClientError as exc:
        if logger is not None:
            logger.emit(
                "warning",
                call="summary-merge",
                message=f"merge LLM call failed: {exc}",
            )
        return existing
    except Exception as exc:  # noqa: BLE001 — never blank a summary on transport hiccups
        if logger is not None:
            logger.emit(
                "warning",
                call="summary-merge",
                message=f"merge LLM call raised: {type(exc).__name__}: {exc}",
            )
        return existing

    latency_ms = int((time.monotonic() - t_before) * 1000)

    merged = _extract_merged(resp)
    if logger is not None:
        logger.emit(
            "llm-response",
            call="summary-merge",
            transcript_id=transcript_id,
            latency_ms=latency_ms,
            merged_chars=len(merged or ""),
        )

    if not merged:
        return existing
    return merged.strip()


def _build_prompt_text(
    *,
    existing: str,
    new: str,
    new_bullets: list[str],
    new_decisions: list[str],
) -> str:
    """Render the merge prompt.

    The instructions lean on three invariants the writer already enforces:

    1. The existing summary is the *anchor* — the reader's first impression
       of the note formed when it was created and shouldn't be erased.
    2. The new chunk continues the same topic (the topic-merge gate already
       filtered to same-day, same-scope, file-set-overlapping work).
    3. Output is 1-2 sentences — same shape as the noteworthy ``description``
       field that seeded both the existing and new values.
    """
    lines = [
        "You are merging two summaries that describe successive chunks of "
        "ONE session note. The session-note writer already decided these "
        "chunks belong together (same day, same scope, overlapping files); "
        "your job is to compose a single 1-2-sentence summary that reflects "
        "the combined arc of the work.",
        "",
        "Rules:",
        "- Treat the EXISTING summary as the anchor. Keep its framing.",
        "- Weave the NEW chunk's information in — do not drop it, do not "
        "let it overwrite the existing framing.",
        "- Stay 1-2 sentences. No bullet points, no headings, no quotes.",
        "- Read as one coherent narrative, not two glued together.",
        "- If the new chunk is just a continuation that adds no new arc "
        "(refinements, more files of the same kind), the existing summary "
        "is fine — return it unchanged.",
        "",
        "Return the merged summary via the `merge_summary` tool.",
        "",
        "EXISTING summary (anchor — preserve framing):",
        existing.strip(),
        "",
        "NEW chunk's summary (weave in):",
        new.strip(),
    ]
    if new_bullets:
        lines.append("")
        lines.append("NEW chunk's bullets (additional context):")
        for b in new_bullets[:8]:
            lines.append(f"- {b}")
    if new_decisions:
        lines.append("")
        lines.append("NEW chunk's decisions (additional context):")
        for d in new_decisions[:6]:
            lines.append(f"- {d}")
    return "\n".join(lines)


def _merge_tool_schema() -> dict[str, Any]:
    return {
        "name": "merge_summary",
        "description": (
            "Emit the merged 1-2 sentence summary for the session note."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "merged": {
                    "type": "string",
                    "description": (
                        "1-2 sentences. Anchored on the EXISTING summary, "
                        "with the NEW chunk's information woven in. No "
                        "bullets, no headings, no quotes, no Markdown."
                    ),
                },
            },
            "required": ["merged"],
        },
    }


def _extract_merged(resp: Any) -> str:
    """Pull the ``merged`` string out of a tool_use response.

    Mirrors the shape ``noteworthy._extract_tool_input`` walks: list of
    content blocks, find the first ``type == "tool_use"`` block, return
    its ``input["merged"]``. Defensive against missing fields — bad
    output collapses to an empty string and the caller falls back to the
    existing summary.
    """
    content = getattr(resp, "content", None) or []
    for block in content:
        block_type = getattr(block, "type", None)
        if block_type != "tool_use":
            continue
        inp = getattr(block, "input", None)
        if not isinstance(inp, dict):
            continue
        merged = inp.get("merged")
        if isinstance(merged, str):
            return merged
    return ""
