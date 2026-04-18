---
name: detach
description: Remove the managed `## Lore` section from CLAUDE.md in the
  current directory. Content outside the section is never touched.
  Reversible via `/lore:attach`. Run with "/lore:detach".
user_invocable: true
---

# Detach-from-wiki

Removes the `## Lore` section that `/lore:attach` wrote. Leaves the rest
of `CLAUDE.md` exactly as the user had it. The directory is simply no
longer a scope anchor.

## Workflow

1. **Read the current block.** Run `lore attach read`. If the result is
   `{}`, print "No ## Lore section found — nothing to detach." and stop.
2. **Confirm.** Show the block that will be removed and ask the user to
   confirm. (Reversible by re-running `/lore:attach`, but worth an
   explicit yes — silent edits to CLAUDE.md are not welcome.)
3. **Remove.** Run `lore detach`. The CLI removes only the `## Lore`
   section and trims the blank line immediately before it if present.
4. **Report.** Print the path that was edited.

## Important rules

- **Only the `## Lore` section is removed.** Content outside the
  heading — even content immediately after it — is preserved verbatim.
- **Missing file or missing section** is a no-op. Never create a file
  just to detach.
- **No cleanup of empty CLAUDE.md.** If removal leaves the file empty,
  leave it empty. The user may want it as a placeholder.

## Related

- `/lore:attach` — the companion that creates the section
- Tracked in [buchbend/lore#1](https://github.com/buchbend/lore/issues/1)
