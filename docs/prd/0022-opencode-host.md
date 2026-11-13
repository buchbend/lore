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

A review on 2026-11-13 read the OpenCode 1.19.5 extension surface from the
published `@opencode-ai/plugin` and `@opencode-ai/sdk` type definitions and
from the OpenCode binary. The OpenCode documentation site was not reachable
from that session.

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

- `lore setup` writes one TypeScript plugin file into the OpenCode global
  plugin folder. The file carries the lore-managed marker and the Lore
  version.
- The plugin holds no Lore logic. Each handler calls a `lore hook` command
  with `--plain` and `--cwd`. The handler passes the host and the session ID
  in the environment.
- The plugin uses the OpenCode `shell.env` hook to set the same two variables
  for the agent's shell commands. `lore tier resolve` and
  `lore hook context-log` then know the host and the session.
- Lore generates the plugin from the hook block of the Claude Code manifest,
  through one event table. The Cursor installer uses the same pattern.

### Event table

| Claude Code event | Lore command | OpenCode hook | How the text arrives |
|---|---|---|---|
| SessionStart | `session-start`, `capture` | `session.created` event | Cached per session and added through `experimental.chat.system.transform`; a toast shows the first line |
| UserPromptSubmit | `user-prompt-submit` | `chat.message` | A synthetic text part on the user message |
| PreCompact | `pre-compact`, `capture` | `experimental.session.compacting` | Pushed to the compaction context |
| Stop | `stop` | none | Not mapped: the Lore command writes nothing |
| SessionEnd | `capture`, `token-usage` | `session.idle` event, debounced, and plugin `dispose` | No text |
| PreToolUse on `Task` or `Agent` | `spawn-model-gate` | `tool.execute.before` on `task` | A thrown error denies the spawn |

- The plugin skips a session that has a parent session. Lore treats that
  session as a subagent, as `LORE_CHILD` does in Claude Code.
- Capture runs again on each debounced `session.idle`. Capture registers
  transcripts and is safe to run twice.

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
- TODO: does OpenCode expand an `@file` line inside `AGENTS.md`? Issue 1 of
  the epic answers this question.

### Skills and MCP

- The installer adds the Lore skills folder to `skills.paths` in the OpenCode
  config. OpenCode does not search the Claude Code plugin cache.
- The installer adds a local MCP server `lore` with the absolute path of the
  `lore` binary. OpenCode names its tools `lore_<tool>`.
- Skill text names a Lore MCP tool by its plain name, such as `lore_search`.
  Skill text does not use a host prefix such as `mcp__lore__`.
- TODO: does `/lore:build` work as an OpenCode command with the colon? Issue 1
  answers this question. A rename to `lore-build` changes ADR 0021 (Lore ships
  as one plugin), and needs an amendment.

### Model tiers as subagents

- An OpenCode `task` call has no model argument. OpenCode sets the model per
  agent definition.
- The installer writes four subagents, `lore-frontier`, `lore-strong`,
  `lore-mid` and `lore-cheap`. Each subagent carries the model of its tier
  from a new `opencode` column of the tier table.
- `lore tier resolve <tier> --host opencode` prints the subagent name.
- The OpenCode spawn gate denies a `task` call whose `subagent_type` is not a
  Lore tier subagent. The gate allows the built-in agents that a skill names
  on purpose.
- TODO: which built-in OpenCode agents does the gate allow? Issue 1 decides
  this in the ADR.

### Transcripts and token records

- OpenCode stores sessions in SQLite. The plugin reads each session through
  the SDK call `client.session.messages` and writes it as JSON lines to a
  Lore-owned folder. Lore then depends on the public OpenCode API, not on the
  database schema.
- A new `opencode` transcript adapter reads those files and implements the
  existing adapter protocol.
- The token record reads the token counts and the cost that OpenCode stores
  on each assistant message. Stage tags come from the `task` descriptions.
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
- The TypeScript plugin gets a smoke test that runs each handler against a
  fake client and a fake `lore` binary. TODO: does CI install Bun for this
  test, or does the test run only where Bun is present?

## Out of scope

- An npm package or a marketplace entry for OpenCode. `lore setup` writes the
  plugin file.
- A project-level OpenCode install. The installer writes the global OpenCode
  config only.
- Windows support for the OpenCode installer, as for the Cursor installer.
- Other hosts, such as Codex or Gemini CLI. The host-neutral hook commands make
  them easier later.
- Changes to the Claude Code behaviour of Lore.
