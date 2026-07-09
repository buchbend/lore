---
name: lore-workflow:ccat-workflow-init
description: Onboard a target repository into the ccat-agent-workflow conventions — migrates CLAUDE.md into AGENTS.md, scaffolds docs/prd/, docs/adr/, the permissions allowlist, and the code-map and spawn-model hooks. Idempotent. Use when setting up a new repo to adopt the CCAT agent workflow.
argument-hint: [target-repo-path]
disable-model-invocation: true
---

# /lore-workflow:ccat-workflow-init

Onboard a target repository into the **ccat-agent-workflow** conventions.

## What it does

1. **Migrate `CLAUDE.md` → `AGENTS.md`**: moves existing agent instructions
   into `AGENTS.md` (the canonical per-repo guide), then replaces `CLAUDE.md`
   with a one-line `@AGENTS.md` import shim so Claude Code still auto-loads it.

2. **Create `docs/prd/index.md`** — MyST toctree stub for Product Requirements
   Documents, if the file does not already exist.

3. **Create `docs/adr/index.md`** — MyST toctree stub for Architecture Decision
   Records, if the file does not already exist.

4. **Wire `prd/index` and `adr/index` into `docs/index.md`**, creating it with
   a minimal root toctree if it does not exist (idempotent either way).

5. **Scaffold the autonomy permissions allowlist** — merges the Bash/`gh`
   rules the autonomous `orchestrate-epic` loop needs into
   `.claude/settings.json`'s `permissions.allow`, so `git worktree`/`push`/
   `fetch`/`merge`/`rebase`/`branch`/`checkout` and `gh pr create`/`merge`/
   `view`/`checks`/`comment`, `gh issue view`/`comment`/`edit`/`close`, and
   `gh api` never trigger a harness approval prompt mid-run. `.claude/
   settings.json` (not `settings.local.json`) is used deliberately: this is a
   repo-wide, checked-in contract, not a per-developer preference. Merging is
   idempotent — existing keys and existing allow entries (including unrelated,
   user-added rules) are preserved verbatim; only missing required rules are
   appended.

6. **Append the `## Epic merge policy` section to `AGENTS.md`** — documents
   the `epic-merge-policy: confirm` marker: absent (the default), the final
   epic→target merge is fully autonomous like every other merge in the loop;
   present, `orchestrate-epic` asks for one human confirmation before that
   final merge only. Idempotent — if the heading is already present (from a
   prior run, or because the repo author hand-authored/customized the
   section, e.g. already opted into `confirm`), the file is left untouched.

7. **Scaffold the code-map `SessionStart` hook** — wires a background hook
   into `.claude/settings.json` that runs the plugin-bundled code-map generator
   (`scripts/code_map.py`) at every session start. It keeps the committed
   `CODEMAP.md` fresh at zero token cost (no model call): silent on the no-op
   fast path, non-interactive, and concurrency-safe (atomic write). Merging is
   idempotent — a re-run leaves exactly one wiring entry and preserves any
   pre-existing hooks, exactly like the permissions allowlist.

8. **Scaffold the spawn-model `PreToolUse` hook** — wires a gate
   (`scripts/require_spawn_model.py`) into `.claude/settings.json` that blocks
   any subagent spawn whose call carries no explicit model, feeding the
   tier-resolution instruction back so the session retries with the model
   parameter set. This is the mechanical enforcement of the `MODEL-TIERS.md`
   rule that no delegation ever inherits the session model implicitly —
   without it the rule is prose only, and exploration subagents silently run
   on the session's frontier-tier model. Merging is idempotent, same contract
   as the code-map hook.

All steps are additive and idempotent: re-running never overwrites an existing
`index.md`, existing ADR/PRD files, an already-correct `AGENTS.md`,
already-present permission rules, or already-wired hooks.

## Usage

```
/ccat-agent-workflow:ccat-workflow-init [target-repo-path]
```

`target-repo-path` defaults to the current working directory.

## Instructions

Run the scaffold script bundled with this plugin:

```bash
python "${CLAUDE_SKILL_DIR}/../../scripts/ccat_workflow_init.py" $ARGUMENTS
```

If `$ARGUMENTS` is empty, the script defaults to the current directory.

After the script exits, report:
- Which files were created (AGENTS.md, CLAUDE.md shim, docs/prd/index.md,
  docs/adr/index.md, .claude/settings.json).
- Which files were skipped because they already existed.
- Whether the Epic merge policy section was newly added to AGENTS.md or was
  already present.
- Whether the code-map SessionStart hook was newly wired into
  .claude/settings.json or was already present.
- Whether the spawn-model PreToolUse hook was newly wired into
  .claude/settings.json or was already present.
- Any warnings if the target directory is not a git repository.
