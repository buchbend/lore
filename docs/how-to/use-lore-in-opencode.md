# Use Lore in OpenCode

**Goal:** run Lore in OpenCode 2.x sessions: the session-start banner, the
`lore` MCP server, the `lore:*` skills, capture and model tiers.

ADR 0022 (Lore runs in OpenCode through a generated plugin) records the
design. PRD 0022 (OpenCode as a host) holds the event table.

## Before you start

- OpenCode 2.x on `PATH` as `opencode`. Lore does not support OpenCode 1.x.
- The `lore` CLI, installed as the README describes.
- Linux or macOS. The OpenCode installer refuses Windows.

## Install

1. Run `lore setup`. When `opencode` is on `PATH`, the `agents` step lists
   an OpenCode install in its plan.
2. Accept the plan. The installer writes these files under
   `${XDG_CONFIG_HOME:-~/.config}/opencode/`:

   | Path | What it holds |
   |---|---|
   | `plugins/lore.js` | The generated plugin, with the lore-managed marker and the Lore version. |
   | `opencode.jsonc`, else `opencode.json` | The MCP server `mcp.servers.lore` and one `lore:<name>` command per skill. The installer keeps comments and other entries. |
   | `agents/lore-<tier>.md` | One tier subagent per tier. Each pins the model of its tier. |

3. Start a new OpenCode session. The model context holds the Lore banner.

The installer asks before it replaces a `plugins/lore.js` without the
lore-managed marker. The installer leaves a `lore-<tier>.md` without the
marker as it is.

## Update

Run `lore update`. The update rewrites `plugins/lore.js` and the tier
subagents when the Lore version or the tier table changes. The update
touches OpenCode only when `plugins/lore.js` exists.

## Pin a model for the built-in subagents

The spawn gate denies a `subagent` call when the target agent pins no
model. OpenCode's built-in `general` and `explore` pin no model, so the
gate denies them. To allow one, set its `model` in the OpenCode config:

```jsonc
{
  "agent": {
    "general": { "model": "<provider>/<model>" }
  }
}
```

Lore's skills spawn the tier subagents, which always pass the gate. See
`docs/model-tiers.md` for the tier table.

## Mute Lore in a session

Run `lore off` in the agent shell, or `/lore:off`. The plugin sets
`LORE_SESSION_ID` for the agent shell, so the mute covers the session.
TODO: does the `lore` MCP server see `LORE_SESSION_ID`? OpenCode starts
the MCP server once per server process, not once per session.

## Where capture writes

The plugin writes one session file per OpenCode session to
`$LORE_OPENCODE_DIR`, else `${XDG_STATE_HOME:-~/.local/state}/lore/opencode/`.
`lore trace tokens` and the transcript index read the session files.

## Uninstall

Run `lore uninstall --integration opencode`. The uninstall removes
`plugins/lore.js`, the tier subagents and the config entries that carry
Lore's names.

## Known limits

- OpenCode shows no toast when Lore loads. The server plugin API offers no
  toast.
- The plugin captures a session when OpenCode stops or reloads the
  plugin. A 15-second timer keeps the session file current between turns.
- See issues #623 (repair missing OpenCode config entries), #624 (spawn
  gate edge cases) and #625 (OpenCode token totals).
