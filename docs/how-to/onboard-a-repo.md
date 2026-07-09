# Onboard a repo

**Goal:** prepare a repository to adopt `lore-workflow`, once, before you run
any of the chain in it.

Onboarding is `lore attach --scaffold-workflow` — the `ccat-workflow-init`
skill (`lore-workflow:ccat-workflow-init`) is a thin pointer at the same
command, for when you'd rather trigger it from a skill invocation than a
shell. It is idempotent — safe to re-run — and additive: it never overwrites
an existing file.

## Before you start

- The target repo checked out locally, ideally a git repository.
- `lore` installed and the repo already `lore attach`ed (a plain `lore
  attach` without the flag also works — the workflow scaffold layers on top).

## Steps

1. **Run the scaffold in the repo.**

   ```
   lore attach --scaffold-workflow
   ```

   Or, from a Claude Code session in the repo:

   ```
   /lore-workflow:ccat-workflow-init
   ```

2. **Read what it reports.** It tells you which files it created and which
   it skipped because they already existed.

## What it sets up

- **`AGENTS.md` as the canonical per-repo agent guide**, migrated from any
  existing `CLAUDE.md`; `CLAUDE.md` becomes a one-line `@AGENTS.md` import
  shim so Claude Code still auto-loads it.
- **`docs/prd/index.md` and `docs/adr/index.md`** — stubs for the PRD and
  ADR homes — and a root **`docs/index.md`** that wires them in.

The code-map (`CODEMAP.md`, refreshed via `lore`'s own `SessionStart` hook)
and the `lore tier resolve` / spawn-model gate come from `lore` itself —
nothing workflow-specific to wire up for either. The autonomy-permissions
allowlist and the `## Epic merge policy` section in `AGENTS.md` (the
`epic-merge-policy: confirm` marker `orchestrate-epic` reads before its
final merge — see [Conventions](../conventions.md)) are not yet part of the
automated scaffold; add the marker to `AGENTS.md` by hand if your repo's
target-branch merge triggers a deployment.

## Done when

The scaffold reports the agent guide and the docs stubs present (created or
already there). The repo is ready for the
[epic chain](run-an-epic.md) and the [fast path](use-the-fast-path.md).
