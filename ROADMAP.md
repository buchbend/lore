# Roadmap

One paragraph: where the code is heading.

## Not placed yet

- **Next-run queue after a build.** A build run that files several follow-ups ends with a list of next runs: one command per issue, in an order. Today the list lives only in the final chat message. The handover section names the follow-up issues, not the order or the commands, and the archived ledger offers nothing after /clear. Lore should keep the queue in one home (the handover section or a seed issue) and name the next run at SessionStart, so a fresh session steps through the issues without the person remembering the list. Seen at the end of epic #555 (index-first context and the lab notebook) with follow-ups #564 to #567.
- **OpenCode as a host.** Lore runs fully in Claude Code only, so a person who switches to OpenCode, or a colleague on OpenCode, loses the banner, the MCP server, the skills and capture. lore setup installs Lore into OpenCode through a generated plugin that calls the existing lore hook commands. PRD [0022](docs/prd/0022-opencode-host.md) (OpenCode as a host), epic [#615](https://github.com/buchbend/lore/issues/615).
- **OpenCode toast through a TUI plugin entry point.** The @opencode/plugin 2.0.32 server Context has no toast; only the TUI plugin context (ui.toast.show) has one, and a TUI plugin needs its own tui entry point, not a single file in plugins/. Ship a small TUI entry so OpenCode shows a one-line 'Lore loaded' toast (PRD 0022, OpenCode as a host).
- **OpenCode token record: nested subagents.** The OpenCode token trace reads direct child sessions only. A subagent spawned by a subagent gets no row. Match grandchildren through their parentID chain.
