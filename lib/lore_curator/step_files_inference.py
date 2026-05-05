"""LLM-judged step_files inference for legacy plan backfill.

Plans authored before the ``Files:`` directive convention have no
parseable file lists in their step bodies — but the prose almost
always names paths (``"Update lib/foo.py to..."``, ``"Add
tests/test_bar.py covering..."``). This module wraps a single LLM
tool-use call that reads one plan's body and emits the
``{step_id: [paths]}`` mapping.

Used by ``lore plan migrate-step-files --llm`` to backfill
``step_files`` frontmatter on legacy plans so the closure pipeline
(commit attribution, edit-flip writeback) can attach to them.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from lore_curator.llm_client import LlmClient, LlmClientError

__all__ = ["StepFilesInference", "LlmClientError", "infer_step_files"]


@dataclass(frozen=True)
class StepFilesInference:
    """LLM verdict for one plan's per-step file lists.

    ``step_files`` maps each step ID the model returned to its inferred
    path list. Steps the model omitted are absent (treat as empty).
    ``confidence`` is the per-step self-reported confidence in [0, 1];
    callers gate on ``_INFERENCE_CONFIDENCE_FLOOR`` to drop low-signal
    rows. ``reason`` is a single-sentence overall rationale.
    """

    step_files: dict[str, list[str]]
    confidence: dict[str, float]
    reason: str


def infer_step_files(
    *,
    plan_slug: str,
    plan_title: str,
    plan_body: str,
    step_ids: list[str],
    llm_client: LlmClient,
    model: str,
) -> StepFilesInference:
    """Ask the LLM to extract per-step file paths from a plan body.

    The plan body is sent verbatim — the model is expected to
    generalise from prose mentions of paths (``lib/foo.py``,
    ``tests/test_bar.py``) per step section.

    Raises:
        LlmClientError: propagated from the underlying client.
        ValueError: when the response contains no ``tool_use`` block.
    """
    prompt = _build_prompt(
        plan_slug=plan_slug,
        plan_title=plan_title,
        plan_body=plan_body,
        step_ids=step_ids,
    )
    schema = _tool_schema()

    resp = llm_client.messages.create(
        model=model,
        max_tokens=2048,
        tools=[schema],
        tool_choice={"type": "tool", "name": "step_files"},
        messages=[{"role": "user", "content": prompt}],
    )

    data = _extract_tool_input(resp)
    return _data_to_inference(data)


# ---------------------------------------------------------------------------
# Prompt + schema
# ---------------------------------------------------------------------------


def _build_prompt(
    *,
    plan_slug: str,
    plan_title: str,
    plan_body: str,
    step_ids: list[str],
) -> str:
    step_id_list = ", ".join(step_ids)
    return (
        "You are extracting which file paths each step of an "
        "implementation plan describes touching.\n"
        "\n"
        "Read the plan body below. For each step ID listed, identify "
        "the file paths that the step describes creating, editing, "
        "deleting, or otherwise modifying. The plan was written before "
        "an explicit ``Files:`` convention existed — paths appear in "
        "prose, often backticked.\n"
        "\n"
        "## Rules\n"
        "- Use repo-relative paths exactly as written in the plan "
        "(e.g. ``lib/foo.py``, not absolute).\n"
        "- Include test files when the step adds or modifies tests.\n"
        "- **Skip** files mentioned only as context ("
        "*\"see lib/x.py for the existing pattern\"*) or as "
        "reference docs the step doesn't modify.\n"
        "- Use an **empty list** for steps that are purely design, "
        "analysis, telemetry-naming, rollout-flag-flipping, or "
        "otherwise have no concrete file edits in the plan body.\n"
        "- Confidence is your self-assessment in [0, 1]; lean "
        "conservative when prose is ambiguous about whether a path "
        "is being modified vs. read.\n"
        "- Only include step IDs that appear in the provided list. "
        "Do not invent steps.\n"
        "\n"
        "## Plan\n"
        f"slug: {plan_slug}\n"
        f"title: {plan_title}\n"
        "\n"
        f"{plan_body}\n"
        "\n"
        "## Step IDs to analyse\n"
        f"{step_id_list}\n"
        "\n"
        "Respond via the ``step_files`` tool. Provide one entry per "
        "step ID (use an empty ``files`` list for steps with no "
        "concrete file work)."
    )


def _tool_schema() -> dict[str, Any]:
    return {
        "name": "step_files",
        "description": (
            "Emit per-step file lists inferred from the plan body."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "step_files": {
                    "type": "array",
                    "description": (
                        "One entry per step ID present in the plan."
                    ),
                    "items": {
                        "type": "object",
                        "properties": {
                            "step_id": {
                                "type": "string",
                                "description": (
                                    "Canonical step ID, e.g. step-1."
                                ),
                            },
                            "files": {
                                "type": "array",
                                "items": {"type": "string"},
                                "description": (
                                    "Repo-relative file paths the step "
                                    "describes modifying. Empty list "
                                    "for design/rollout/no-code steps."
                                ),
                            },
                            "confidence": {
                                "type": "number",
                                "minimum": 0,
                                "maximum": 1,
                                "description": (
                                    "Confidence in [0, 1] that the "
                                    "files list is accurate."
                                ),
                            },
                        },
                        "required": ["step_id", "files", "confidence"],
                        "additionalProperties": False,
                    },
                },
                "reasoning": {
                    "type": "string",
                    "description": (
                        "One sentence overall rationale — what signals "
                        "you keyed off (e.g. backticked paths in prose)."
                    ),
                },
            },
            "required": ["step_files", "reasoning"],
            "additionalProperties": False,
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
    raise ValueError("step_files: no tool_use block in response")


def _data_to_inference(data: dict[str, Any]) -> StepFilesInference:
    raw = data.get("step_files") or []
    if not isinstance(raw, list):
        return StepFilesInference(step_files={}, confidence={}, reason="invalid shape")

    step_files: dict[str, list[str]] = {}
    confidence: dict[str, float] = {}

    for entry in raw:
        if not isinstance(entry, dict):
            continue
        step_id = entry.get("step_id")
        if not isinstance(step_id, str) or not step_id:
            continue
        files = entry.get("files") or []
        if not isinstance(files, list):
            files = []
        cleaned = [str(f).strip() for f in files if isinstance(f, str) and str(f).strip()]
        try:
            conf = float(entry.get("confidence", 0.0))
        except (TypeError, ValueError):
            conf = 0.0
        conf = max(0.0, min(1.0, conf))
        step_files[step_id] = cleaned
        confidence[step_id] = conf

    reason = data.get("reasoning") or ""
    if not isinstance(reason, str):
        reason = str(reason)

    return StepFilesInference(
        step_files=step_files,
        confidence=confidence,
        reason=reason,
    )
