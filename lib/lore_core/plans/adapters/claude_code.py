"""Claude Code ``PostToolUse:ExitPlanMode`` hook-payload adapter.

Translates Claude Code's hook JSON into raw plan markdown. Claude
Code's harness has shipped two payload shapes:

1. **Tool-input form** — ``tool_input.plan`` carries the markdown.
2. **Tool-response form** — ``tool_response.plan`` carries the
   markdown when the model called ExitPlanMode without a ``plan``
   argument and the harness loaded it from the plan file. ``tool_input``
   is empty in that case.

The adapter searches both sections, the documented field names first,
then falls back to the longest string ≥100 characters in either
section (handles future schema renames without code changes).

The original logic lived inline in :mod:`parser` as ``parse_payload``.
Relocating it behind the :class:`adapters.Adapter` protocol lets us
add Cursor / Aider / Cline adapters without churning the parser, and
isolates Claude-Code-specific knowledge in one file.
"""
from __future__ import annotations

from typing import Any

#: Documented ``tool_input.<field>`` names tried in order. The first
#: non-empty string wins.
_PAYLOAD_FIELDS = ("plan", "plan_text", "content", "text", "markdown")

#: Threshold for the "longest string anywhere" fallback. Rejects short
#: slugs / IDs that happen to live alongside the plan text.
_FALLBACK_MIN_CHARS = 100


def detect(payload: dict) -> bool:
    """True if the payload looks Claude-Code-shaped.

    Conservative: any payload with a ``tool_input`` or ``tool_response``
    dict matches. Claude Code's hook envelope always carries one of
    these, and other producers we've seen do not.
    """
    return isinstance(payload.get("tool_input"), dict) or isinstance(
        payload.get("tool_response"), dict
    )


def extract(payload: dict[str, Any]) -> tuple[str | None, str]:
    """Extract plan markdown from a Claude Code hook payload.

    Returns ``(text, source_field)``. ``text`` is None if nothing
    extractable was found; ``source_field`` names which path matched
    (e.g. ``"tool_input.plan"``, ``"tool_response.markdown[fallback]"``).

    Search order:

    1. Documented ``tool_input.<field>`` names (``plan``, ``plan_text``, …).
    2. Longest string ≥100 chars anywhere in ``tool_input``.
    3. Same lookups against ``tool_response`` — Claude Code's actual
       hook payload puts the plan in ``tool_response.plan`` when the
       model calls ExitPlanMode without a ``plan`` argument.
    """
    for source_name in ("tool_input", "tool_response"):
        section = payload.get(source_name)
        if not isinstance(section, dict):
            continue

        for field_name in _PAYLOAD_FIELDS:
            value = section.get(field_name)
            if isinstance(value, str) and value.strip():
                return value, f"{source_name}.{field_name}"

        # Fallback: prefer the LONGEST string >= threshold. Dict
        # insertion order is determined by the JSON producer; picking
        # the longest is robust to future schema additions where an
        # extra string field happens to be listed before the real plan.
        longest_key: str | None = None
        longest_val: str | None = None
        longest_len = -1
        for key, value in section.items():
            if not isinstance(value, str):
                continue
            if len(value) < _FALLBACK_MIN_CHARS:
                continue
            if len(value) > longest_len:
                longest_len = len(value)
                longest_key = key
                longest_val = value
        if longest_val is not None:
            return longest_val, f"{source_name}.{longest_key}[fallback]"

    return None, "no-match"
