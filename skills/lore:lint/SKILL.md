---
name: lore:lint
description: Health-check the knowledge vault and regenerate
  _catalog.json / _index.md / llms.txt. Runs the lore_core linter,
  interprets results, offers safe auto-fixes. Run with "/lore:lint".
user_invocable: true
---

# Lore Linter

## What this skill does

1. Runs `lore_core.lint` to scan all wikis, check health, regenerate
   catalogs + indexes (including `llms.txt` for forward compatibility
   with the emerging convention).
2. Interprets the report and offers auto-fixes for safe issues.

## Step 1 — Run the linter

```bash
python -m lore_core.lint --json
```

Outputs a JSON report with all issues. Also writes per-wiki (atomic):
- `_catalog.json` — machine-readable metadata, links, hierarchy
- `_index.md` — LLM-scannable knowledge index
- `llms.txt` — alias of `_index.md` (llms.txt convention)

### CLI options

```bash
python -m lore_core.lint                  # full lint + regenerate outputs
python -m lore_core.lint --check-only     # lint only, no writes
python -m lore_core.lint --wiki <name>    # scope to one wiki
python -m lore_core.lint --json           # output report as JSON
```

## Step 2 — Interpret and fix

Read the JSON output. Summarize findings by wiki and severity.

### Auto-fixable (offer to the user)

- **Missing `schema_version`**: run
  `python -m lore_core.migrate --add-schema-version --apply` to backfill
  all notes at once
- **Missing `last_reviewed`** / **`created`**: infer from
  `git log --follow`
- **Missing `status`**: default to `active` on concept/project/decision
  notes; `stable` on session notes
- **Missing index notes**: create stub index with links to existing
  sub-notes
- **Unlinked sub-notes**: add `[[parent-index]]` link
- **Oversized flat notes**: offer to split into subfolder with index +
  sub-notes

### Not auto-fixable (report only)

- **Broken wikilinks**: need human judgment (create stub? fix typo?
  remove?)
- **Staleness**: needs human review of content accuracy
- **Empty descriptions**: needs actual content, not a placeholder
- **Cross-pollination**: needs manual move to correct wiki
- **Orphan notes**: needs context to determine correct links

## Migrations

When the plugin introduces a new schema field (like `schema_version` in
v1 or `repos:` later), a migration script in `lore_core.migrate`
backfills existing notes. All migrations are idempotent and dry-run by
default; require `--apply` to write.

```bash
python -m lore_core.migrate --add-schema-version            # dry-run
python -m lore_core.migrate --add-schema-version --apply    # write
```

## What the catalogs enable

`_catalog.json` and `_index.md` per wiki make the vault work as a
RAG-style knowledge brain:

- **LLM navigation**: read `_index.md` to find relevant notes by
  description and tags, then load only what's needed via `[[wikilinks]]`
- **Team use**: browse the index in Obsidian or any markdown viewer
- **Programmatic access**: `_catalog.json` has metadata, link graph,
  hierarchy for scripts and tools
- **Search index**: `lore_search` consumes these outputs to build its
  FTS5 + Model2Vec index

## Important rules

- **Always run the script first** — don't manually scan files
- **Read-only by default** — only fix with user approval
- **Catalogs are auto-generated** — never edit `_index.md`,
  `_catalog.json`, or `llms.txt` by hand
- **Atomic writes** — catalogs are written via `.tmp + rename`, safe for
  concurrent hook reads
