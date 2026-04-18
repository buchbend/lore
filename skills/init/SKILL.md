---
name: init
description: Initialize a new Lore vault at $LORE_ROOT — creates the
  canonical shape (wiki/, sessions/, inbox/, drafts/, templates/) and
  writes a starter root CLAUDE.md. Run with "/lore:init".
user_invocable: true
---

# Vault Init

One-command vault setup. Creates the canonical shape at `$LORE_ROOT`
(default `~/lore`) and writes a starter root `CLAUDE.md`.

## Workflow

1. Check `$LORE_ROOT`. If it exists and is non-empty, confirm with the
   user before writing.
2. Create the canonical shape:
   ```
   $LORE_ROOT/
   ├── sessions/       # empty
   ├── inbox/          # empty
   ├── drafts/         # empty
   ├── templates/      # populated from plugin's templates/
   ├── wiki/           # empty — user mounts wikis here
   └── CLAUDE.md       # from templates/root-CLAUDE.md
   ```
3. Offer to mount a first wiki:
   - By URL → `git clone <url> $LORE_ROOT/wiki/<name>`
   - By existing path → `ln -s <path> $LORE_ROOT/wiki/<name>`
   - Skip → leave `wiki/` empty (user can `/lore:new-wiki` later)
4. Run `/lore:lint` once to regenerate catalogs.

## Related

- `/lore:new-wiki <name>` — create a brand-new wiki skeleton
- `/lore:import <name>` — convert an existing markdown vault

## Important rules

- **Never overwrite existing content without confirmation** — if
  `$LORE_ROOT/CLAUDE.md` already exists, show a diff and ask.
- **Templates live in the plugin** — `templates/` is copied from the
  plugin's `templates/` folder.
- **Don't auto-populate wiki conventions** — each wiki defines its own
  `CLAUDE.md`; `/lore:new-wiki` handles that step.
