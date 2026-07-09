---
name: lore-workflow:ccat-workflow-init
description: Onboard a target repository into the workflow conventions — docs/prd/, docs/adr/, and the AGENTS.md shim. Idempotent, a thin pointer at `lore attach --scaffold-workflow`. Use when setting up a repo to adopt the workflow skills.
argument-hint: [target-repo-path]
disable-model-invocation: true
---

# /lore-workflow:ccat-workflow-init

Onboard a target repository into the workflow conventions this plugin's skills
assume: `docs/prd/`, `docs/adr/`, and an `AGENTS.md` (with a `CLAUDE.md` shim).

## One onboarding command, not a separate flow

This used to be a standalone script bundled with the workflow plugin. It no
longer is — `lore` (the plugin this one depends on) owns repo onboarding, and
workflow scaffolding is one flag on that single entry point:

```
lore attach --scaffold-workflow [target-repo-path]
```

Idempotent, same as before: re-running never overwrites an existing
`index.md`, existing ADR/PRD files, or an already-correct `AGENTS.md`. See
`lore attach --help` for the full onboarding flow (wiki attachment, scope,
consent) that this flag rides alongside.

## What it scaffolds

1. **Migrate `CLAUDE.md` → `AGENTS.md`** — moves existing agent instructions
   into `AGENTS.md` (the canonical per-repo guide), then replaces `CLAUDE.md`
   with a one-line `@AGENTS.md` import shim so Claude Code still auto-loads it.
2. **`docs/prd/index.md`** — MyST toctree stub for Product Requirements
   Documents, if it does not already exist.
3. **`docs/adr/index.md`** — MyST toctree stub for Architecture Decision
   Records, if it does not already exist.
4. **Wires both into `docs/index.md`**, creating it with a minimal root
   toctree if it does not exist.

## What moved elsewhere

The permissions allowlist, the code-map `SessionStart` hook, and the
spawn-model `PreToolUse` hook (the mechanical enforcement of the
no-implicit-inherit tier rule — see
[TIER-DELEGATION.md](../../TIER-DELEGATION.md)) are no longer this skill's
concern: `lore`'s own installer wires them for every attached repo, once, so
there is nothing workflow-specific left to scaffold here.

## Usage

```
/lore-workflow:ccat-workflow-init [target-repo-path]
```

Run `lore attach --scaffold-workflow` (targeting `target-repo-path`, or the
current directory if omitted) and report which files were created versus
already present, and any warning if the target is not a git repository.
