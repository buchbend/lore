---
name: import
description: Bring an existing markdown vault into Lore's shape. Mount-as-is
  by default; optional LLM enrichment backfills frontmatter (type,
  description, tags) and git-log dates. Run with "/lore:import <path>
  [--enrich]".
user_invocable: true
---

# Vault Import

Two modes in v1:

### Mount as-is (default)

Symlink an existing Obsidian vault or markdown repo under
`$LORE_ROOT/wiki/<name>/`. Skills tolerate missing frontmatter; linter
warns but doesn't error. Wikilinks, tags, hierarchy all work out of the
box.

```bash
ln -s /path/to/existing-vault $LORE_ROOT/wiki/<name>
/lore:lint --wiki <name>       # regenerate catalogs
```

### Enrich (`--enrich`)

Per-note LLM pass that fills missing frontmatter:

- `type` — inferred from content + folder (project / concept / decision
  / paper / session)
- `description` — generated from the first paragraph or body summary
- `created` / `last_reviewed` — backfilled from `git log --follow`
- `tags` — suggested from the wiki's taxonomy (declared in its
  `CLAUDE.md`)
- `schema_version: 1` — added to every note

Dry-run preview with a diff; user approves per batch before writing.
Bounded cost: one small prompt per note, one-time on import.

## Workflow

1. **Dry-run**
   ```
   /lore:import <wiki> --enrich
   ```
   Shows proposed frontmatter patches note-by-note.

2. **Approve in batches** (all / group / note-by-note).

3. **Apply** writes frontmatter via atomic `.tmp + rename`. Bodies
   untouched.

4. **Commit** in the wiki's own git repo.

## Non-markdown sources

Notion, Confluence, Logseq: export to markdown first using existing
tools (`notion-markdown-exporter`, etc.), then import. Lore doesn't
handle proprietary formats.

## What's deferred

- **Restructure mode** (re-cluster flat vaults into hierarchical
  folders) — needs human review of every move; risky. Defer to v2.

## Important rules

- **Never edit bodies automatically** — only frontmatter
- **Dry-run is the default** — `--apply` to write
- **Respect existing frontmatter** — only fill *missing* fields; don't
  overwrite user-set values
- **Generate `schema_version: 1`** on every touched note
