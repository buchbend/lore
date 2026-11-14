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

OpenCode lists the MCP tools under the server name, such as
`lore.lore_search`. Lore's skills name the tools without the server name,
and the agent finds them.

The installer asks before it replaces a `plugins/lore.js` without the
lore-managed marker. The installer leaves a `lore-<tier>.md` without the
marker as it is, and `lore setup` prints its path.

The installer treats `mcp.servers.lore` as Lore's entry when its command
runs a `lore` binary with `mcp`. A `lore:<name>` command is Lore's when
its template matches the one Lore writes. The installer updates Lore's
entries and keeps the keys you added. The installer asks before it
replaces any other entry under those names.

## Update

Run `lore update`. The update rewrites `plugins/lore.js` and the tier
subagents when the Lore version or the tier table changes. The update
touches OpenCode only when `plugins/lore.js` exists.

## Set the models of the tier subagents

The tier subagents pin the models of the `opencode` column in the tier
table, such as `anthropic/claude-sonnet-5`. A tier subagent fails when
your OpenCode providers do not offer its model. To use your own models,
set them in `~/.config/lore/config.yml`:

```yaml
tiers:
  overrides:
    opencode:
      frontier: <provider>/<model>
      strong: <provider>/<model>
      mid: <provider>/<model>
      cheap: <provider>/<model>
```

Then run `lore setup`. Setup rewrites each `lore-<tier>.md` whose model
changed.

## Pin a model for the built-in subagents

The spawn gate denies a `subagent` call when the target agent pins no
model. OpenCode's built-in `general` and `explore` pin no model, so the
gate denies them. To allow one, set its `model` in the OpenCode config:

```jsonc
{
  "agents": {
    "general": { "model": "<provider>/<model>" }
  }
}
```

Lore's skills spawn the tier subagents, which always pass the gate. See
`docs/model-tiers.md` for the tier table.

## Mute Lore in a session

Run `lore off` in the agent shell. The plugin sets `LORE_SESSION_ID` for
the agent shell, so the mute covers the hooks of the session. The plugin
also refuses each `lore` MCP tool call of a muted session. Run
`lore on` to end the mute. OpenCode has no slash command for the mute.

## Where capture writes

The plugin writes one session file per OpenCode session to
`$LORE_OPENCODE_DIR`, else `${XDG_STATE_HOME:-~/.local/state}/lore/opencode/`.
`lore trace tokens` and the transcript index read the session files.
OpenCode capture removes a session file when the file is older than 7 days
and the transcript index holds its session. Capture keeps every other file.

## Run Claude Code from an OpenCode shell

You can run `claude` from the agent shell of an OpenCode session. The
nested Claude Code session detects Claude Code as its host. It uses its own
session id and the Claude Code tier models.

The reverse case works too. The plugin removes the Claude Code variables
from the commands and agent shells that it starts. Lore in an OpenCode
session keeps OpenCode as its host, even when OpenCode starts from a
Claude Code shell.

## Uninstall

Run `lore uninstall --integration opencode`. The uninstall removes
`plugins/lore.js`, the tier subagents and the config entries that carry
Lore's names.

## Known limits

- OpenCode shows no toast when Lore loads. The server plugin API offers no
  toast.
- The plugin captures a session when OpenCode stops or reloads the
  plugin. A 15-second timer keeps the session file current between turns.
- `lore off` does not mute the `lore` MCP tool calls of an OpenCode
  subagent. The mute file belongs to the parent session.
- After `pipx upgrade lore` without `lore update`, the old plugin lets a
  denied spawn run. Run `lore update` or `lore setup` after an upgrade.
- A comment inside `mcp.servers.lore` or inside a `lore:<name>` command is
  lost when the installer updates the entry.
- `lore off` in the Bash tool of a nested Claude Code session mutes the
  parent OpenCode session, not the nested session.
