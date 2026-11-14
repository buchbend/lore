---
title: "OpenCode as a host"
status: draft
epic: https://github.com/buchbend/lore/issues/615
repos:
  - buchbend/lore
---

# PRD 0022: OpenCode as a host

> Source of truth for this epic. Tracker: [epic issue](https://github.com/buchbend/lore/issues/615).
> The epic links here; this file is not embedded in the issue body.

## Problem

Lore runs fully in one host, Claude Code. Cursor gets the MCP server, the
skills and a rules file. OpenCode gets nothing. A person who switches between
Claude Code and OpenCode, or a colleague who uses OpenCode, loses Lore in
OpenCode sessions:

- No SessionStart banner, so the agent does not see the repo index or the
  neighbours.
- No `lore` MCP server, so `lore_search` and `lore_context_pack` are missing.
- No `lore:*` skills.
- No transcript capture and no token record, so `lore trace tokens` and the
  transcript index miss the OpenCode sessions.

Lore's state lives in the vault, the wiki and the repo artifacts. Nothing in
that state depends on the host. Five places tie Lore to one host:

- The hook commands write the Claude Code JSON output and pick the Claude Code
  transcript adapter by name.
- The context log finds its cache by the Claude Code process ID.
- Tier resolution detects only the Claude Code and Cursor hosts.
- The transcript adapter and the token record read only Claude Code
  transcripts.
- `lore setup` installs only into Claude Code and Cursor.

A review on 2026-11-13 read the OpenCode 1.19.5 type definitions. A probe on
2026-11-13 and 2026-11-14 then ran OpenCode 2.0.32, the version the team runs.
OpenCode 2.x has a new plugin API and does not load a 1.x plugin. ADR
[0022](../adr/0022-lore-runs-in-opencode-through-a-generated-plugin.md) (Lore
runs in OpenCode through a generated plugin) records the probe and the
decisions. This PRD targets OpenCode 2.x.

## Solution

`lore setup` detects OpenCode and installs Lore into the OpenCode config. A
person then gets the same Lore in both hosts:

- The same banner at session start. A one-line toast tells the person that
  Lore loaded.
- The same `lore` MCP server and the same skills.
- The same capture, so OpenCode sessions enter the transcript index and the
  token records.
- The same model tiers. In OpenCode, a tier resolves to a Lore subagent, not
  to a model name.

`lore update` rewrites the OpenCode plugin file. The OpenCode install then
stays at the installed Lore version, with no second version to bump.

## Implementation decisions

### The OpenCode plugin is a generated shim

- `lore setup` writes one JavaScript plugin file into the OpenCode global
  plugin folder `~/.config/opencode/plugins/`. The file carries the
  lore-managed marker and the Lore version.
- The file exports `default { id: "lore", setup(ctx) }`, the shape of the
  `@opencode/plugin` 2.x API.
- The plugin holds no Lore logic. Each hook calls a `lore hook` command with
  `--plain` and `--cwd`. The hook passes `LORE_HOST=opencode` and
  `LORE_SESSION_ID` in the environment.
- The plugin sets the same two variables for the agent's shell commands
  through `ctx.shell.hook("create.before")`. `lore tier resolve` and
  `lore hook context-log` then know the host and the session.
- Lore generates the plugin from the hook block of the Claude Code manifest,
  through one event table. The Cursor installer uses the same pattern.

### Event table

| Claude Code event | Lore command | OpenCode 2.x hook | How the text arrives |
|---|---|---|---|
| SessionStart | `session-start`, `capture` | First `ctx.session.hook("context")` call of a session | Cached per session; pushed as a system part on each request of the session |
| UserPromptSubmit | `user-prompt-submit` | `ctx.session.hook("prompt")` runs the command; `context` adds the text | A system part on the next request, not text in the person's message |
| PreCompact | `pre-compact`, `capture` | `ctx.session.hook("compaction")` | Pushed as a system part of the compaction request |
| Stop | `stop` | none | Not mapped: the Lore command writes nothing |
| SessionEnd | `capture`, `token-usage` | TODO: the probe saw no event through `ctx.event.subscribe`. Issue 613 (OpenCode capture) picks the trigger: an idle event, or the plugin cleanup function | No text |
| PreToolUse on `Task` or `Agent` | `spawn-model-gate` | `ctx.tool.hook("execute.before")` on the tool `subagent` | A thrown error denies the spawn; the model reads the error text |

- The plugin adds Lore text to main sessions only. The `context` hook names
  the agent of the session. A session of a subagent gets no Lore text, as
  `LORE_CHILD` does in Claude Code.
- Capture registers transcripts and is safe to run twice.
- TODO: does OpenCode 2.x offer a toast to the plugin? Issue 612 (OpenCode
  install) checks the `@opencode/plugin` 2.x TUI API. Without a toast, the
  banner has no visible line.

### Host-neutral hook commands

- The hook commands read `LORE_HOST` and `LORE_SESSION_ID`. Without them, the
  commands detect Claude Code and Cursor as they do today.
- The capture commands pick the transcript adapter by host.
- The context log keys its cache by `LORE_SESSION_ID` when the variable is
  set. Otherwise it keeps the Claude Code process lookup.
- `lore tier resolve` accepts `opencode` as a host. It reads `LORE_HOST`
  before the host detection that exists today.

### Instruction files

- OpenCode loads the first file it finds of `AGENTS.md`, `CLAUDE.md` and
  `CONTEXT.md`, and only that file.
- The content of an agent instruction file goes into `AGENTS.md`. `CLAUDE.md`
  holds only `@AGENTS.md`. This rule covers this repo, the vault root that
  `lore setup` writes, and the workflow scaffold.
- OpenCode does not expand an `@file` line inside `AGENTS.md`. An
  instruction stays in `AGENTS.md` as text, not behind an `@file` line.

### Skills, commands and MCP

- OpenCode 2.x does not search the Claude Code plugin cache. The plugin adds
  each Lore skill through `ctx.skill.transform`, with `id`, `name`, `path` and
  `content`.
- In OpenCode 2.x a skill is not a `/` command. The installer merges one entry
  per entry-point skill into the config key `commands`. The command template
  tells the agent to use the skill.
- Skills and commands keep the name `lore:<name>`. OpenCode 2.x accepts the
  colon in both names.
- The installer adds a local MCP server `lore` with the absolute path of the
  `lore` binary to the config key `mcp`. TODO: the shape of the 2.x `mcp`
  entry and the MCP tool names. Issue 612 reads `config/mcp` in
  `@opencode/schema` 2.x.
- Skill text names a Lore MCP tool by its plain name, such as `lore_search`.
  Skill text does not use a host prefix such as `mcp__lore__`.

### Model tiers as subagents

- An OpenCode `subagent` call has no model argument. OpenCode sets the model
  per agent definition. The call names the agent in `input.agent`.
- The installer writes four subagents, `lore-frontier`, `lore-strong`,
  `lore-mid` and `lore-cheap`, into `~/.config/opencode/agents/`. Each
  subagent carries the model of its tier from a new `opencode` column of the
  tier table.
- `lore tier resolve <tier> --host opencode` prints the subagent name.
- The OpenCode spawn gate keeps the Claude Code rule. The gate denies a
  `subagent` call when the named agent pins no model, because the spawn then
  runs on the session model. The gate keeps no allow list of agents.
- The built-in subagents `general` and `explore` pin no model unless the
  person sets one in the OpenCode config. The gate denies them until then.
- TODO: the field of the 2.x agent definition that holds the model. Issue 614
  (OpenCode tiers) reads `Agent.Info` in `@opencode/schema` 2.x.

### Transcripts and token records

- OpenCode stores sessions in SQLite. The plugin reads each session through
  the public 2.x plugin API and writes it as JSON lines to a Lore-owned
  folder. Lore then depends on the public OpenCode API, not on the database
  schema. TODO: the 2.x call that returns the messages of a session. Issue
  613 reads `SessionApi` in `@opencode/sdk` 2.x.
- A new `opencode` transcript adapter reads those files and implements the
  existing adapter protocol.
- The token record reads the token counts and the cost that OpenCode stores
  on each assistant message. Stage tags come from the `subagent` call descriptions.
- The tool category table gets an OpenCode column.

## Testing decisions

- A guard test checks that every hook in the Claude Code manifest has a row
  in the OpenCode event table. A row can say "not mapped" with a reason. The
  version-sync test is the prior art.
- Installer tests follow the Cursor installer tests. They plan actions
  against a temporary home folder and check the written config, plugin file
  and subagent files.
- The config merge reads and writes JSON with comments, because OpenCode
  accepts `opencode.jsonc`. A test covers a config that holds comments and
  other MCP servers.
- Hook command tests set `LORE_HOST=opencode` and check the plain output and
  the adapter choice.
- Adapter tests read recorded OpenCode session files. The Claude Code adapter
  tests are the prior art.
- The plugin file gets a smoke test that runs each hook against a fake
  `ctx` and a fake `lore` binary. The test runs the plugin file with Node,
  which the GitHub runner image holds.

## Out of scope

- An npm package or a marketplace entry for OpenCode. `lore setup` writes the
  plugin file.
- A project-level OpenCode install. The installer writes the global OpenCode
  config only.
- Windows support for the OpenCode installer, as for the Cursor installer.
- Other hosts, such as Codex or Gemini CLI. The host-neutral hook commands make
  them easier later.
- Changes to the Claude Code behaviour of Lore.
