---
name: lore:plan-advance
description: Mark a plan step as done. Defaults to the current
  in-progress step; otherwise the next pending step. Wraps `lore plan
  advance`. Run with "/lore:plan-advance <slug>".
argument-hint: <slug>
user_invocable: true
---

# Plan advance — mutate `step_status` via the CLI

Marks a plan step as done by shelling out to `lore plan advance`. The
plan-as-authority contract means the mutation goes through the CLI,
which holds a per-slug flock under the hood — never edit the plan
note's frontmatter directly.

## Workflow

### 1. Parse the argument

```
/lore:plan-advance refactor-auth
```

If the slug is missing, ask for it.

### 2. Shell out — one Bash call

```
lore plan advance <slug>
```

The bare form picks the right step automatically: any `in_progress`
step wins (earliest by document order); otherwise the first pending
step. For an explicit transition (mark a non-default step done, or
flip to `--in-progress` / `--blocked` / `--pending`), use:

```
lore plan step <slug> <step_id> --done
```

### 3. Surface the result

`lore plan advance` prints `<slug> · <step_id>: <prev> → done`. Echo
that line back. If the CLI exits non-zero, surface stderr verbatim.
If the CLI says `nothing to advance — all steps are done`, surface
that — flipping `status: active` → `status: done` is a separate
manual edit (the skill stops there).

## Why this is short

In the CLI-first design the mutation primitive lives in
`lore_core/plans/step_status.py` and is exposed through the CLI
(`lore plan advance` / `lore plan step`). This skill is just the
keyboard shortcut and the renderer; the lock semantics, validation,
and timestamp bumping happen behind the CLI boundary.

## Important rules

- **One CLI call per invocation.** Don't chain `lore plan step`
  multiple times — invoke this skill once per step.
- **Never edit the plan note's frontmatter directly.** The
  flock-protected mutator is the only safe writer.
- **Use `s<N>` everywhere.** Step refs use the canonical anchor form
  in prose AND in wikilinks. Never "Step N of M" or `#step-2`.

## Related

- `/lore:plan-resume <slug>` — read side: show current state.
- `lore plan step <slug> <step_id> --in-progress|--blocked|--pending`
  — fine-grained transitions beyond the "advance to done" shortcut.
