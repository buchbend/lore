---
name: lore:detach
description: Remove the current directory's Lore attachment (and any
  legacy `## Lore` section left in CLAUDE.md). Reversible via
  `/lore:attach`. Run with "/lore:detach".
user_invocable: true
---

# Detach-from-wiki

Undoes what `/lore:attach` did. Two things may need removing:

1. The **attachment row** in `$LORE_ROOT/.lore/attachments.json` (the
   modern location, post-0.3.0).
2. The legacy **`## Lore` section** in `CLAUDE.md` (only present on
   repos attached before 0.3.0 that haven't been migrated).

## Workflow

### 1. Inspect

```
lore attachments show . --json 2>/dev/null
```

Show the user the covering attachment (path, wiki, scope) so they can
confirm what is about to go. Silent edits to `CLAUDE.md` or to shared
state are not welcome — one explicit yes.

### 2. Remove the attachment row

If `lore attachments show` returned an attachment, remove it by its
exact path:

```
lore attachments rm <path-from-show>
```

This is idempotent. Exit 1 means nothing was registered — fine to
continue.

### 3. Strip any legacy `## Lore` section

```
lore detach
```

No-op if `CLAUDE.md` is missing or has no section. The CLI only touches
the managed heading — content outside it is preserved verbatim, and a
single blank line preceding the section is collapsed.

### 4. Report

Print the path(s) that changed. If nothing needed removing, say so
plainly — that's a common, healthy outcome.

## Important rules

- **Attachment row first, CLAUDE.md second.** Reverse order leaves a
  stale `attachments.json` entry if the section strip errors.
- **Only the `## Lore` section.** Content outside the heading is
  preserved. The CLI enforces this; never edit `CLAUDE.md` directly.
- **Longest-prefix match.** `lore attachments show .` surfaces a
  parent attachment too. If the covering attachment is above cwd, ask
  the user whether they mean to detach the parent or just cwd — the
  former affects everything below.
- **Missing file or missing section is a no-op.** Never create
  `CLAUDE.md` just to detach.
- **No decline side-effect.** Detaching does not decline the offer —
  re-running `/lore:attach` would re-prompt. Use `lore attach decline`
  if the intent is "stop offering this forever."

## Related

- `/lore:attach` — companion that creates the attachment
- `lore attachments ls` — list every attachment on this host
- `lore attach decline` — refuse an offer permanently (fingerprint-keyed)
