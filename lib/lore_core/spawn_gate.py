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

# OpenCode 2.x spawn tool. It has no model argument: the model belongs to the
# agent named in ``input.agent``, so the plugin passes that agent's pinned
# model (empty when none) as ``tool_input.model``.
OPENCODE_SPAWN_TOOL = "subagent"

_OPENCODE_UNKNOWN_AGENT_MESSAGE = (
    "Spawn blocked: OpenCode does not know the agent {agent!r} named in this "
    "subagent call. Retry with an agent OpenCode lists; for a tier use "
    "`lore tier resolve <frontier|strong|mid|cheap>` (lore-<tier>).\n"
)

_OPENCODE_DENY_MESSAGE = (
    "Spawn blocked: the agent named in this subagent call pins no model, so it "
    "would implicitly inherit the session model — the tier contract forbids "
    "that (docs/model-tiers.md). Resolve the step's semantic tier with "
    "`lore tier resolve <frontier|strong|mid|cheap>` and retry the call with "
    "the agent set to that command's output (lore-<tier>). A built-in agent "
    "such as general or explore passes once your OpenCode config pins a model "
    "for it.\n"
)

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
    tool = payload.get("tool_name")
    if tool != OPENCODE_SPAWN_TOOL and tool not in SPAWN_TOOL_NAMES:
        return None
    tool_input = payload.get("tool_input")
    if not isinstance(tool_input, dict):
        tool_input = {}
    deny = _OPENCODE_DENY_MESSAGE if tool == OPENCODE_SPAWN_TOOL else _DENY_MESSAGE
    if tool == OPENCODE_SPAWN_TOOL and tool_input.get("agent_known") is False:
        deny = _OPENCODE_UNKNOWN_AGENT_MESSAGE.format(agent=tool_input.get("agent"))
    if tool_input.get("subagent_type") == "fork":
        return None
    model = tool_input.get("model")
    if isinstance(model, str) and model.strip():
        return None
    return deny
