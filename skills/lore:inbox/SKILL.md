---
name: lore:inbox
description: Process files from wiki inboxes and the root inbox. Routes
  content to the correct wiki's knowledge graph. Supports markdown, PDFs,
  images, notebooks, code. Run with "/lore:inbox".
user_invocable: true
---

# Knowledge Inbox Processor

Processes files dropped into any inbox and creates structured notes in
the appropriate wiki's knowledge graph.

## Inbox locations

| Inbox | Purpose | Auto-routes to |
|-------|---------|----------------|
| `$LORE_ROOT/inbox/` | Root triage inbox — unsorted drops | Determine by content, ask if unsure |
| `$LORE_ROOT/wiki/<name>/inbox/` | Per-wiki drops | `wiki/<name>/` |

Per-wiki inboxes skip the routing step. The root inbox requires triage:
analyze each file and route to the best wiki.

## Supported file types

| Type | Extensions | How it's read |
|------|-----------|---------------|
| Markdown/text | `.md`, `.txt` | Read directly |
| PDF | `.pdf` | Read tool (max 20 pages/request, chunk large docs) |
| Images | `.png`, `.jpg`, `.jpeg`, `.gif` | Read tool (multimodal) |
| Jupyter notebooks | `.ipynb` | Read tool (renders cells) |
| Code files | `.py`, `.rs`, `.js`, `.toml`, `.yml`, etc. | Read directly |
| RST docs | `.rst` | Read directly |

## Workflow

### 0. Git pull each affected wiki

```bash
git -C $LORE_ROOT/wiki/<target> pull --ff-only
```

### 1. Scan all inboxes

```
Glob: $LORE_ROOT/inbox/*           (root — needs triage)
Glob: $LORE_ROOT/wiki/*/inbox/*    (per-wiki — pre-routed)
```

Skip `.processed/` and hidden files.

### 2. For each file, read and analyze

Determine:
- **What is it?** (paper, architecture doc, config, screenshot, code pattern)
- **What wiki does it belong to?** (pre-answered for per-wiki inboxes)
- **What note type?** project, concept, decision, paper, etc.
- **What subfolder?** Use hierarchical paths where appropriate
- **Related notes?** Scan the target wiki for `[[wikilink]]` candidates

For root inbox items where the target wiki is ambiguous, ask the user.

### 3. Check for contradictions

Before writing, run `lore search` on the new note's topic. Read the
top-3 neighbours. If the new note contradicts an existing one, don't
auto-merge — flag in frontmatter:

```yaml
contradicts: [[existing-note-name]]
```

Surface the conflict so the user can decide whether to supersede or
reconcile.

### 4. Create vault notes

- Follow the target wiki's CLAUDE.md conventions (frontmatter, tags,
  structure)
- Include `schema_version: 1`
- Use kebab-case filenames
- Add `[[wikilinks]]` to related existing notes
- For non-text sources, add a `## Source` section noting the original
  file
- Set `provenance: extracted` and `source: "original-filename"`
- Keep notes atomic — one topic per note; split multi-topic documents

### 5. Update related notes

Add backlinks to existing notes where clearly relevant.

### 6. Archive originals

Move processed files to `.processed/` in the same inbox with a date
prefix: `YYYY-MM-DD_original-filename`. Never delete inbox files —
always archive.

### 7. Git commit

Commit in the correct repo:
- `wiki/<name>/` → that wiki's git repo
- Root inbox archives → the vault root repo (if it's a git repo)

### 8. Report

Summarize: files processed (by type and inbox), where each was filed,
links created, files needing user review (ambiguous, large PDFs,
contradictions).

## Important rules

- **Per-wiki inboxes are pre-routed** — don't second-guess the wiki
- **Root inbox needs triage** — analyze content, ask when unsure
- **Respect each wiki's conventions** — each wiki's CLAUDE.md defines
  tag taxonomy and frontmatter rules
- **Summarize, don't reproduce** — create concise vault notes;
  originals stay in `.processed/`
- **Keep notes atomic** — one topic per note
- **Link generously** — connections are the value of the knowledge
  graph
- **Flag contradictions** — don't silently override existing knowledge
- **Large PDFs** — chunk with the `pages` parameter
