# ADR 0022: Lore runs in OpenCode through a generated plugin

- **Status:** Proposed
- **Date:** 2026-11-14
- **Deciders:** Christof Buchbender
- **Relates to:** epic [#615](https://github.com/buchbend/lore/issues/615) (OpenCode as a host);
  sub-issue [#610](https://github.com/buchbend/lore/issues/610) (verify OpenCode hook behaviour);
  PRD [0022](../prd/0022-opencode-host.md) (OpenCode as a host)
- **See also:** ADR [0021](0021-lore-ships-as-one-plugin.md) (Lore ships as one plugin)

## Holds

- **Default:** Lore targets OpenCode 2.x. The plugin is one generated file that exports
  `default { id, setup(ctx) }` from the `@opencode/plugin` 2.x API. Lore does not support
  the OpenCode 1.x plugin shape.
- **Default:** the plugin holds no Lore logic. Each hook calls a `lore hook` command with
  `--plain`, and passes `LORE_HOST=opencode` and `LORE_SESSION_ID`.
- **Default:** the session-start banner and the per-prompt text reach the model through
  `ctx.session.hook("context")`, as a system part. The plugin adds the text to main
  sessions only.
- **Default:** the pre-compact text reaches the model through
  `ctx.session.hook("compaction")`.
- **Default:** the spawn gate hooks `ctx.tool.hook("execute.before")` on the tool
  `subagent`. The gate denies a call when the agent in `input.agent` pins no model. The
  rule is the Claude Code rule: no spawn inherits the session model. The gate keeps no
  allow list of agents.
- **Default:** skills and `/` commands keep the name `lore:<name>`, as ADR 0021 (Lore
  ships as one plugin) sets. OpenCode 2.x accepts the colon in both names.
- **Default:** in OpenCode 2.x a `/` command and a skill are separate. The installer writes
  one command per entry-point skill. The command template loads the skill.
- **Default:** agent instructions are text inside `AGENTS.md`. OpenCode does not expand an
  `@file` line, so an instruction behind `@file` does not reach an OpenCode session.
- **Incidental:** the probe ran OpenCode 2.0.32 (`@opencode/cli`) with the model
  `qwen3-8-27b` through an OpenAI-compatible provider.

## Revisit if

- OpenCode changes the plugin API of `@opencode/plugin` in a new major version.
- OpenCode adds a hidden text part to the `prompt` hook, so the per-prompt text can sit
  next to the user message.
- A person needs Lore in an OpenCode 1.x install.

## Alternatives considered

- **Target OpenCode 1.x, as the first PRD draft did.** Rejected: OpenCode 2.0.32 does not
  load a 1.x plugin. It reports "Plugin must export a default definition with an id and an
  effect or setup function".
- **Support 1.x and 2.x.** Rejected: two plugin shapes double the generator and the tests
  for a version that the team does not run.
- **Per-prompt text through the `prompt` hook.** Rejected: the 2.x prompt is
  `{text, files, agents}` and has no hidden part. The appended text shows in the person's
  message. The probe model also read the text as an injected instruction and ignored it.
- **Session-start text through the config key `instructions`.** Rejected: the key reads
  files at load time. The banner changes per folder and per session.
- **A spawn gate with an allow list of agents.** Rejected: the Claude Code gate checks the
  model, not the agent. An allow list blocks a built-in agent that pins a model, and adds
  a second rule.
- **Rename the skills to `lore-<name>` for OpenCode.** Rejected: the probe showed that
  `/lore:build` runs, so a rename buys nothing and needs an amendment to ADR 0021.

## Background

Sub-issue #610 asked five questions. The person ran a probe plugin in a throwaway project
on 2026-11-13 and 2026-11-14. The plugin logged each hook call to a file. Each hook
added a different code word. The model reply then named the channel that reached it.

| # | Question | Answer on OpenCode 2.0.32 |
|---|---|---|
| 1 | Does a skill named `lore:build` load, and does `/lore:build` run it? | Yes. A skill ID and a command name accept the colon. A skill is not a `/` command; the config key `commands` adds the command. |
| 2 | Does the model read a synthetic text part from the prompt hook? | The 2.x prompt has no parts. Text appended to `prompt.text` reaches the model and shows in the person's message. |
| 3 | Does an error thrown before a spawn stop the spawn, and does the model see the text? | Yes, both. The 2.x spawn tool is `subagent`, and the agent name is in `input.agent`. |
| 4 | Does the system hook reach the main session and the subagent sessions? | Yes, both. `session.hook("context")` runs on each model request, with the agent name of the session. |
| 5 | Does OpenCode expand an `@file` line inside `AGENTS.md`? | No. OpenCode loads `AGENTS.md`, and the `@file` line stays plain text. |

The probe also showed:

- `session.hook("compaction")` runs before compaction, and text that the hook adds stays in
  the summary.
- The built-in agents are the primary agents `build`, `plan`, `compaction`, `title` and
  `summary`, and the subagents `general` and `explore`. A project agent in
  `.opencode/agents/` loads as a subagent.
- OpenCode did not read `SKILL.md` folders from `.opencode/skills/`, `.agents/skills/` or
  `.claude/skills/` without config. `ctx.skill.transform` adds a skill when the skill
  carries `id`, `name`, `path` and `content`.
- A subscriber to `ctx.event.subscribe` received no events in the probe.
