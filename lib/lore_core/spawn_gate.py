"""PreToolUse gate: block subagent spawns that omit an explicit model.

The tier contract (docs/model-tiers.md) says no delegation ever inherits
the session model implicitly — every spawn carries its semantic tier's
resolved model in the spawn call itself. Skills state that rule in
prose; prose can be ignored. This gate is the mechanical backstop:
wired as a PreToolUse hook on the spawn tools (Task/Agent), it denies
any spawn whose input has no explicit model and points the caller at
``lore tier resolve <tier>`` to pick one and retry.

Ported from ccat-agent-workflow's ``scripts/require_spawn_model.py``.
Host wiring differs (Claude Code only, for now — see
``lore_cli.hooks``); the deny/allow logic is host-agnostic and lives
here so other hosts can reuse it later.
"""

from __future__ import annotations

# Tool names under which Claude Code exposes the subagent spawn.
SPAWN_TOOL_NAMES = frozenset({"Task", "Agent"})

_DENY_MESSAGE = (
    "Spawn blocked: this subagent call carries no explicit model, so it "
    "would implicitly inherit the session model — the tier contract "
    "forbids that (docs/model-tiers.md). Resolve the step's semantic tier "
    "with `lore tier resolve <frontier|strong|mid|cheap>` and retry the "
    "spawn with the model parameter set to that command's output. For a "
    "deliberate frontier-tier delegation, pass the session's own model "
    "explicitly.\n"
)


def check_spawn(payload: dict) -> str | None:
    """Return a deny message if `payload` is a model-less subagent spawn, else None.

    Fail-open by construction: any shape that isn't recognizably a
    Task/Agent spawn (wrong tool, missing/malformed tool_input) returns
    None rather than guessing. Forks always inherit the parent model by
    design — the model parameter is documented as ignored for them, so
    requiring it would be noise.
    """
    if not isinstance(payload, dict):
        return None
    if payload.get("tool_name") not in SPAWN_TOOL_NAMES:
        return None
    tool_input = payload.get("tool_input")
    if not isinstance(tool_input, dict):
        tool_input = {}
    if tool_input.get("subagent_type") == "fork":
        return None
    model = tool_input.get("model")
    if isinstance(model, str) and model.strip():
        return None
    return _DENY_MESSAGE
