---
name: lore:plan-resume
description: Show a plan's current step status, in-progress + next-pending
  steps, and recent breadcrumb signals. Single MCP call, read-only,
  renders as markdown. Run with "/lore:plan-resume <slug>".
argument-hint: <slug>
user_invocable: true
---

# Plan resume — focused view of one plan via `lore_plan_status`

Renders a single plan's status by calling the `lore_plan_status` MCP
tool exactly once. The output is what the user (or another agent)
needs to pick the work back up: title, where things stand on each
step, and any breadcrumb signals that suggest progress hasn't been
recorded yet.

**Do not** Glob, Read, or Grep the vault. The plan-as-authority
contract means `step_status` in the plan's frontmatter is the single
source of truth — query it via MCP, render it, done.

## Workflow

### 1. Parse the argument

The user always passes a slug (the plan's filename without `.md`):

```
/lore:plan-resume refactor-auth
/lore:plan-resume migrate-oidc
```

If the argument is missing, ask for the slug or suggest
`/lore:resume` (the broader context loader) instead.

### 2. Call the MCP tool — exactly one call

```
mcp__lore__lore_plan_status({"slug": "<slug>"})
```

If the user is in an attached repo, optionally pass `repo_root` so the
breadcrumb scan picks up commit trailers (`Plan: <slug>#s<N>`):

```
mcp__lore__lore_plan_status({"slug": "<slug>", "repo_root": "<cwd>"})
```

### 3. Render the result

Lead with the plan's human title, then a one-line summary of step
state, then the per-step list, then breadcrumbs (if any).

```
## Plan: <title> · <N>/<M> done [· K in-progress] [· stale (Nd)]

| step | status |
| :--- | :--- |
| s1 — <title> | done |
| s2 — <title> | in_progress |
| s3 — <title> | pending |

[[plan/<slug>#<active step>]]

### Recent activity
- commit abc123 (3m ago) references s2
- session [[2026-04-28-foo]] (1h ago) links s3
```

If the result includes an `error` field (e.g. `plan_not_found`),
surface the error message verbatim. Don't retry.

## Important rules

- **One MCP call.** No Glob/Read/Grep, no per-file walks. If the call
  fails, surface the error.
- **Read-only.** This skill never mutates `step_status` — that's
  `/lore:plan-advance` (or `lore plan step` directly).
- **Recognize `s<N>` everywhere.** Step refs use the canonical anchor
  form in prose AND in wikilinks. Never "Step N of M" or `#step-2`.

## Related

- `/lore:plan-advance <slug>` — write side: mark a step done.
- `/lore:resume` — broader context loader (recent sessions, open
  issues/PRs across all wikis).
