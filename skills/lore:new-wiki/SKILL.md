---
name: lore:new-wiki
description: Scaffold a new wiki skeleton inside $LORE_ROOT/wiki/ —
  directory structure, CLAUDE.md from template, initial _index.md,
  optional git init + GitHub remote. Run with "/lore:new-wiki <name>".
user_invocable: true
---

# New Wiki Scaffolder

Creates a fresh wiki under `$LORE_ROOT/wiki/<name>/` with the expected
layout and a `CLAUDE.md` template. Supports team or personal modes.

## Workflow

1. Ask the user:
   - Name (kebab-case)
   - Mode: `team` (public/shared, will push to a remote) or `personal`
     (private, stays local)
   - Git remote URL (if team)
2. Create the directory:
   ```
   $LORE_ROOT/wiki/<name>/
   ├── projects/
   ├── concepts/
   ├── decisions/
   ├── sessions/
   ├── inbox/
   ├── templates/      # symlink to ../../templates/ or copy
   ├── CLAUDE.md       # from plugin template
   └── _index.md       # stub — filled by the linter
   ```
3. Write `CLAUDE.md` from `templates/wiki-CLAUDE.md`, substituting the
   wiki name and a starter tag taxonomy.
4. If team mode:
   - `git init` inside the wiki
   - Add the remote
   - Initial commit
5. Run `/lore:lint --wiki <name>` to seed `_index.md` / `_catalog.json`.

## Important rules

- **Don't overwrite an existing wiki** — refuse if
  `$LORE_ROOT/wiki/<name>` already exists
- **Template first, edit later** — wiki conventions live in the wiki's
  own `CLAUDE.md`; the user customizes after scaffolding
- **Personal mode skips git** — avoids accidentally publishing private
  notes to a remote
