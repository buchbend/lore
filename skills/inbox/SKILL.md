---
name: lore:inbox
description: Process files from wiki inboxes and the root inbox.
  Calls MCP `lore_inbox_classify` for the deterministic walk; LLM
  reads each file and composes notes; `lore inbox archive` moves
  originals to `.processed/`. Run with "/lore:inbox".
user_invocable: true
---

# Knowledge Inbox Processor

Process files dropped into any inbox and create structured notes in
the appropriate wiki. The walk + classification is one MCP call; the
LLM reads files, composes notes, and archives originals.

## Workflow

1. **Classify** — call `mcp__lore__lore_inbox_classify` (no args).
   Returns per-file `path`, `type`, `target_wiki`, `needs_triage`. If
   `total == 0`, report and stop. Do not Glob substitute.

2. **Per file: route + analyze** — pre-routed files (`target_wiki`
   set) go to that wiki; root-inbox files (`needs_triage: true`) need
   content-driven routing (ask if ambiguous).

3. **Compose the note** — pick type
   (project / concept / decision / paper / reference), subfolder,
   and slug. Call `mcp__lore__lore_search` once with the topic to
   find related notes; `[[wikilink]]` the top hits.

4. **Contradiction check** — if the new content contradicts a top
   search hit's description, set `contradicts: [[note]]` in
   frontmatter and surface it in the final report. Don't auto-merge.

5. **Write + commit** — write the note via the Write tool following
   the wiki's CLAUDE.md conventions (`schema_version: 2`,
   `provenance: extracted` + `source:` for non-text). Commit with
   `lore session commit <path>`.

6. **Archive** — `lore inbox archive <path>` moves the source to
   `.processed/<YYYY-MM-DD>_<filename>`. Never delete.

7. **Report** — by type and inbox: filed paths, links created, items
   needing user review (ambiguous routing, large PDFs, contradictions).

## Hard rules

- One MCP call for the walk (Glob substitutes are a regression).
- Per-wiki inboxes are pre-routed — don't second-guess `target_wiki`.
- Always archive, never delete.
- Atomic notes — one topic per note; split multi-topic documents.
- Flag contradictions, never silently override.
