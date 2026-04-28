---
name: lore:journal
description: Read or append entries to the AI/human freeform journal. Two side-chains at $LORE_ROOT/journals/{ai,human}.md — non-derived, low-bar notes (jokes, criticism, ideas, weather). Run with "/lore:journal" to read, or "/lore:journal <text>" to append a human entry.
user_invocable: true
---

# Journal — AI + human freeform side-chains

Two newest-first markdown logs at the top of the vault:

- `$LORE_ROOT/journals/ai.md` — written by the model (volitional, via
  `lore_journal_write`)
- `$LORE_ROOT/journals/human.md` — written by you (default for this
  skill)

Distinct from the per-day `journal` *surface* (auto-extracted into a
wiki by Curator B). The journals here are deliberately **non-derived**
— jokes, criticism, half-formed ideas, weather, anything that
wouldn't survive auto-extraction. The bar to write must be near-zero.

## Workflow

### 1. Parse the argument

```
/lore:journal                       → read recent human entries
/lore:journal ai                    → read recent AI entries
/lore:journal --ai                  → same
/lore:journal "remember to ..."     → append a human entry with that text
/lore:journal ai "weather is good"  → append an AI entry (rare — AI usually writes via MCP)
```

Heuristics:

- No args → read human, default 10 entries
- One token `ai` or `human` (with optional leading `--`) → read that journal
- Anything else (or quoted text) → append as human entry

### 2. Read

Shell out: `lore journal read [--ai|--human] -n 10` and render the
output verbatim.

If empty, the CLI prints `(<kind> journal is empty — ...)` — surface
that line as-is.

### 3. Write

Shell out: `lore journal write [--ai] "<text>"` and surface the
one-line confirmation. The author tag is auto-resolved (handle from
git config / `LORE_USER_HANDLE`).

### 4. Status / toggle

If the user types `/lore:journal status`, `/lore:journal enable`, or
`/lore:journal disable`, forward to `lore journal <verb>` and render
the result.

## Important rules

- **Read-only by default.** No-arg invocation reads, never writes.
- **No vault search, no Glob, no Read.** This is a thin wrapper over
  the `lore journal` CLI. The journal is intentionally outside the
  curator/surface graph; do not try to enrich it.
- **No frontmatter, no schema.** Each entry is a `## <ts> — <author>`
  head followed by free prose. Don't add tags, summaries, or
  wikilinks unless the user wrote them.
- **Tone.** The AI journal is for the model; the human journal is for
  the user. Don't editorialize when reading; render verbatim.

## Why this skill is short

The CLI does the work. The skill is a tiny dispatcher: parse, shell
out, render. The journal is intentionally low-ceremony.

## Related

- `lore_journal_write` (MCP) — what the model uses inline during a
  session, gated on `journal.enabled` via the SessionStart prompt
  fragment.
- `lore_journal_read` (MCP) — for the model to retrieve recent
  entries when explicitly asked.
- The `journal` surface in `lore-data/<wiki>/SURFACES.md` — auto-
  extracted per-day notes; *not* the same thing.
