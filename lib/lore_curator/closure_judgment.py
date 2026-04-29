"""LLM-gated step-closure judgment for the Stop hook.

Given a commit (sha + message + diff summary) and a plan step that the
commit's files overlap with, ask the LLM whether the commit *closes*
the step or merely touches its files. Returns a structured verdict
(``done`` / ``in_progress`` / ``skip``) plus confidence and reason —
no side effects.

The Stop hook applies the close action and threshold logic; this
module is pure judgment.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from lore_curator.llm_client import LlmClient, LlmClientError

__all__ = ["ClosureJudgment", "LlmClientError", "judge_closure"]


_VALID_DECISIONS = ("done", "in_progress", "skip")


@dataclass(frozen=True)
class ClosureJudgment:
    """LLM verdict on whether a commit closes a plan step.

    ``decision`` is one of ``"done"`` | ``"in_progress"`` | ``"skip"``.
    ``confidence`` is the LLM's self-reported confidence in [0, 1].
    ``reason`` is the short rationale the model emitted; primarily for
    logging / pending-attribution audit trails.
    """

    decision: str
    confidence: float
    reason: str


def judge_closure(
    *,
    commit_sha: str,
    commit_msg: str,
    diff_summary: str,
    plan_slug: str,
    step_id: str,
    step_title: str,
    step_body: str,
    current_status: str,
    llm_client: LlmClient,
    model: str,
) -> ClosureJudgment:
    """Ask the LLM whether ``commit_sha`` closes ``plan_slug#step_id``.

    Forces the structured-output ``closure_judgment`` tool so the
    response shape is fixed.

    Raises:
        LlmClientError: propagated from the underlying client when the
            LLM call itself fails (timeout, exit-nonzero, malformed
            JSON, missing tool_use). The Stop hook catches and treats
            as a "skip" verdict so a transient backend issue can't
            break Stop.
        ValueError: when the response contains no ``tool_use`` block —
            a contract violation that should be surfaced rather than
            silently treated as no-op.
    """
    prompt = _build_prompt(
        commit_sha=commit_sha,
        commit_msg=commit_msg,
        diff_summary=diff_summary,
        plan_slug=plan_slug,
        step_id=step_id,
        step_title=step_title,
        step_body=step_body,
        current_status=current_status,
    )
    schema = _tool_schema()

    resp = llm_client.messages.create(
        model=model,
        max_tokens=512,
        tools=[schema],
        tool_choice={"type": "tool", "name": "closure_judgment"},
        messages=[{"role": "user", "content": prompt}],
    )

    data = _extract_tool_input(resp)
    return _data_to_judgment(data)


# ---------------------------------------------------------------------------
# Prompt + schema
# ---------------------------------------------------------------------------


def _build_prompt(
    *,
    commit_sha: str,
    commit_msg: str,
    diff_summary: str,
    plan_slug: str,
    step_id: str,
    step_title: str,
    step_body: str,
    current_status: str,
) -> str:
    return (
        "You are judging whether a single git commit *completes* a plan step.\n"
        "\n"
        "## Plan step\n"
        f"Plan: {plan_slug}\n"
        f"Step: {step_id} — {step_title}\n"
        f"Current status: {current_status}\n"
        "\n"
        "Step body:\n"
        f"{step_body}\n"
        "\n"
        "## Commit\n"
        f"SHA: {commit_sha}\n"
        f"Message: {commit_msg}\n"
        "\n"
        "Diff summary (`git show --stat`):\n"
        f"{diff_summary}\n"
        "\n"
        "## Decide\n"
        "Emit one of three decisions via the `closure_judgment` tool:\n"
        "- **done** — this commit substantively completes the step. The work the\n"
        "  step describes is now in the codebase. Confidence ≥ 0.7 expected.\n"
        "- **in_progress** — the commit is part of the step's work but does not\n"
        "  complete it. Partial implementation, WIP, just-tests-yet, etc.\n"
        "- **skip** — the commit touches the step's files but is unrelated work\n"
        "  (refactor, rename, tangential fix), or you can't tell from the\n"
        "  available signal. Use when uncertain — better to defer than wrongly\n"
        "  close a step.\n"
        "\n"
        "Lean on the commit message verbs: 'implement', 'complete', 'add' →\n"
        "lean done; 'wip', 'partial', 'progress' → lean in_progress; 'rename',\n"
        "'cleanup', 'fix typo' → lean skip. Your reason should be one short\n"
        "sentence."
    )


def _tool_schema() -> dict[str, Any]:
    return {
        "name": "closure_judgment",
        "description": (
            "Emit the closure verdict for one (commit, step) pair."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "decision": {
                    "type": "string",
                    "enum": list(_VALID_DECISIONS),
                    "description": (
                        "One of done / in_progress / skip. Pick skip when uncertain."
                    ),
                },
                "confidence": {
                    "type": "number",
                    "minimum": 0,
                    "maximum": 1,
                    "description": "Your confidence in the decision, in [0, 1].",
                },
                "reason": {
                    "type": "string",
                    "description": "One short sentence explaining the verdict.",
                },
            },
            "required": ["decision", "confidence", "reason"],
        },
    }


# ---------------------------------------------------------------------------
# Response extraction
# ---------------------------------------------------------------------------


def _extract_tool_input(resp: Any) -> dict[str, Any]:
    for block in getattr(resp, "content", []) or []:
        btype = getattr(block, "type", None) or (
            block.get("type") if isinstance(block, dict) else None
        )
        if btype != "tool_use":
            continue
        inp = getattr(block, "input", None)
        if inp is None and isinstance(block, dict):
            inp = block.get("input")
        if isinstance(inp, dict):
            return inp
    raise ValueError("closure_judgment: no tool_use block in response")


def _data_to_judgment(data: dict[str, Any]) -> ClosureJudgment:
    decision = data.get("decision")
    if not isinstance(decision, str) or decision not in _VALID_DECISIONS:
        # Defensive clamp: a model that returns an out-of-enum string
        # is treated as the conservative no-op rather than passed
        # through as garbage.
        return ClosureJudgment(decision="skip", confidence=0.0, reason="invalid decision")

    raw_conf = data.get("confidence", 0.0)
    try:
        confidence = float(raw_conf)
    except (TypeError, ValueError):
        confidence = 0.0
    confidence = max(0.0, min(1.0, confidence))

    reason = data.get("reason") or ""
    if not isinstance(reason, str):
        reason = str(reason)

    return ClosureJudgment(
        decision=decision,
        confidence=confidence,
        reason=reason,
    )
