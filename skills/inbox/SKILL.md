---
name: inbox
description: Process files from wiki inboxes and the root inbox.
  Calls MCP `lore_inbox_classify` for the deterministic walk; LLM
  reads each file and composes notes; `lore inbox archive` moves
  originals to `.processed/`. Run with "/lore:inbox".
user_invocable: true
---

# Knowledge Inbox Processor

Processes files dropped into any inbox and creates structured notes
in the appropriate wiki. The walk + classification is one MCP call;
the LLM only reads the files (judgment is required for note
composition) and composes the resulting notes.

## Inbox locations

| Inbox | Purpose | Auto-routes to |
|-------|---------|----------------|
| `$LORE_ROOT/inbox/` | Root triage — unsorted drops | Determine by content, ask if unsure |
| `$LORE_ROOT/wiki/<name>/inbox/` | Per-wiki drops | `wiki/<name>/` |

Per-wiki inboxes are pre-routed; root inbox needs triage.

## Workflow

### 1. MCP classify (silent, fast)

Call `mcp__lore__lore_inbox_classify` with no args. Returns:

```
{
  "files": [
    {
      "path": "<absolute>",
      "filename": "<basename>",
      "extension": ".md",
      "type": "markdown" | "pdf" | "image" | "notebook" | "code" | "config" | "rst" | "text" | "unknown",
      "size_bytes": 1234,
      "target_wiki": "ccat" | null,
      "needs_triage": false | true
    },
    ...
  ],
  "by_inbox": {"(root)": [...], "ccat": [...], ...},
  "by_type": {"markdown": 3, "pdf": 1, ...},
  "total": 4
}
```

If `total == 0`, report "No inbox files to process" and stop. **Do
not** Glob the inbox dirs yourself.

### 2. For each file, read + analyze (LLM judgment)

For pre-routed files (`target_wiki` set), the wiki is decided. For
root-inbox files (`needs_triage: true`), pick the best wiki by
content; ask the user when ambiguous.

Per file, decide:
- **Note type**: project / concept / decision / paper / reference
- **Subfolder** (if hierarchical wiki conventions warrant)
- **Slug** (kebab-case)
- **Wikilink candidates**: call `mcp__lore__lore_search` once with
  the file's topic to find existing related notes — `[[wikilink]]`
  the top hits

### 3. Contradiction check

Before writing, scan the top search hits' descriptions. If the new
content contradicts an existing note, set
`contradicts: [[existing-note-name]]` in frontmatter and surface
the conflict in your final report. Don't auto-merge.

### 4. Write the vault note

Write the new note directly with the Write tool, following the
target wiki's CLAUDE.md conventions:

- `schema_version: 2`
- `provenance: extracted` and `source: "<original-filename>"` for
  non-text sources (PDF, image, notebook)
- `[[wikilinks]]` to related existing notes
- Atomic per topic — split multi-topic documents

Then commit with `lore session commit <new-note-path>` (the commit
subcommand works for any path inside a wiki).

### 5. Archive the original via Bash

```bash
lore inbox archive <path-from-classify>
```

The CLI moves the source to `.processed/<YYYY-MM-DD>_<filename>` in
the same inbox. Never delete inbox files — always archive.

### 6. Report

Summarize: files processed (by type and inbox), where each was filed,
links created, files needing user review (ambiguous routing, large
PDFs that needed chunking, contradictions).

## Hard rules

- **One MCP call for the walk.** Glob/Read substitutes are a
  regression.
- **Per-wiki inboxes are pre-routed.** Don't second-guess
  `target_wiki`.
- **Root inbox needs triage.** Analyze content; ask when unsure.
- **Always archive, never delete.** `.processed/` is the audit trail.
- **Respect each wiki's CLAUDE.md.** Tag taxonomies and frontmatter
  conventions vary per wiki.
- **Atomic notes.** One topic per note; split multi-topic documents.
- **Flag contradictions, never silently override.**
