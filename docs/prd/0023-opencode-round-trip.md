---
title: "Claude Code and OpenCode round trip"
status: shipped
epic: https://github.com/buchbend/lore/issues/632
repos:
  - buchbend/lore
---

# PRD 0023: Claude Code and OpenCode round trip

> Historical. Current decisions: ADR 0022.

> Source of truth for this epic. Tracker: [epic issue](https://github.com/buchbend/lore/issues/632).
> The epic links here; this file is not embedded in the issue body.

## Problem

PRD 0022 (OpenCode as a host) shipped in release 0.107.0. A person can run Lore in Claude Code or in OpenCode. A person who moves between the two hosts on one repo and one vault still meets four defects:

- `lore off` does not mute the `lore` MCP tools in OpenCode. Issue #626 (MCP mute in OpenCode) holds the details.
- A Claude Code session that starts from an OpenCode agent shell inherits `LORE_HOST=opencode` and the OpenCode session id. The session picks OpenCode agent names as tier models and writes state under the wrong session. Issue #627 (nested Claude Code environment) holds the details.
- The OpenCode plugin writes one session file per OpenCode session, and no code removes the files. Issue #628 (old OpenCode session files) holds the details.
- The whole-epic review of epic #615 (OpenCode as a host) found five small defects. Issue #629 (five small defects) lists them.

Nobody ran the full round trip on a repo under a Lore scope. The live check of epic #615 ran in a folder with no scope, so capture reported `no-scope`.

## Solution

- Lore behaves the same in both hosts on one repo and one vault.
- A handover note written in one host shows in the session-start banner of the other host.
- The transcript index and `lore trace tokens` list the sessions of both hosts.
- `lore off` mutes all of Lore in an OpenCode session, the MCP tools included.
- A person runs one live round trip and confirms the points above.

## Implementation decisions

- **MCP mute (#626).** The generated OpenCode plugin refuses a `lore` MCP tool call in its tool `execute.before` hook. The hook receives the session id. The hook throws when the mute file of the session exists. The MCP server process does not need the session id. The plugin already uses the same hook for the spawn gate.
- **Nested Claude Code (#627).** Host detection checks the Claude Code markers before it reads `LORE_HOST`. The session id of a Claude Code hook comes from the hook payload or from `CLAUDE_SESSION_ID` before `LORE_SESSION_ID`. The change touches the session id lookup in the toggles, the session id lookup in the context cache, and host detection in the tier resolver.
- **Old session files (#628).** OpenCode capture removes a session file when two conditions hold. The file is older than 7 days, and the transcript index holds its session. Capture keeps every other file.
- **Five defects (#629).** Each defect gets a local fix in its own module. The fix for one session-start per session and the fix for #628 can both touch the plugin generator. Each edit stays local to its function.
- ADR 0022 (Lore runs in OpenCode through a generated plugin) still holds. The spawn gate checks the model, not the agent, and keeps no allow list. Target OpenCode 2.x only.

## Testing decisions

- Each acceptance criterion maps to one test of external behaviour: CLI output, a file on disk, or the answer of a plugin hook.
- Plugin hook tests run in Node against a fake context. The fake context uses the real OpenCode 2.x reply shape `{data}`. A fake context with a different shape hides defects, as the review of issue #613 (OpenCode capture) showed.
- Prior art: the existing tests of the plugin generator, the OpenCode adapter and the OpenCode token trace.
- The live round trip is a manual check that a person runs. The HITL (human in the loop) row of the epic holds its checklist.

## Out of scope

- An OpenCode toast at session start. The roadmap holds it.
- Token records for subagents of subagents. The roadmap holds it.
- Hosts other than Claude Code and OpenCode.
- OpenCode 1.x.
