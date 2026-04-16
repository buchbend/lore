# Personal Knowledge Vault (Lore)

Root vault at `$LORE_ROOT`. Federates one or more knowledge domains
(wikis) into a single Obsidian graph while keeping each wiki as an
independent git repo.

## Structure

- `sessions/` — cross-cutting personal session logs (optional)
- `inbox/` — triage inbox — `/lore:inbox` routes items to the right wiki
- `drafts/` — WIP notes not ready for a wiki
- `templates/` — note templates
- `wiki/` — mounted wikis (symlinks or inline dirs)
- `CLAUDE.md` — this file

## Wiki mounts

Each wiki has its own `CLAUDE.md`, tag taxonomy, and git history.
Lore's `/lore:*` skills route work to the correct wiki based on
content; they never cross-pollinate.

Add a new wiki:

```bash
# Mount an existing team repo
ln -s /path/to/team-knowledge $LORE_ROOT/wiki/<name>

# Or scaffold a new one
/lore:new-wiki <name>
```

## Rules

- Session notes follow `templates/session.md`
- Personal notes can `[[wikilink]]` freely to any wiki note
- Wiki notes should not link to personal content
- Filenames are kebab-case, globally unique across all wikis
- Per-wiki conventions (frontmatter, tags, staleness) live in each
  wiki's own `CLAUDE.md`
