---
name: lore:resume
description: Load working context from the vault on demand. Single MCP
  call dispatches across no-arg (recent across all wikis), wiki-scoped,
  keyword search, or scope-prefix aggregation. Run with "/lore:resume"
  optionally followed by keywords, a wiki name, or a scope prefix.
user_invocable: true
---

# Resume — load context via the `lore_resume` MCP tool

Reconstructs working context by calling the `lore_resume` MCP tool
exactly once. The tool covers four modes; the skill picks the right
mode from the user's input and renders the result.

**Do not** Glob, Read, or Grep the vault from this skill — the gather
work is the CLI's job (and via MCP, free of permission prompts and
iterative tool churn).

## Modes (the MCP tool dispatches in this priority order)

1. **scope** — argument contains a `:` (e.g. `ccat:data-center`).
   Aggregates `gh issue list` + `gh pr list` per repo in the subtree
   plus matching session notes.
2. **keyword** — single word or phrase that does not look like a wiki
   name or a scope. Ranked FTS5 search across the vault.
3. **wiki** — argument matches a wiki directory name (`ccat`,
   `private`, `science`, etc.). Recent sessions in that wiki.
4. **recent (default)** — no arguments. Recent sessions across all
   wikis (last 3 days by default).

## Workflow

### 1. Parse the argument

The user may type any of:

```
/lore:resume                         → mode=recent
/lore:resume ccat                    → mode=wiki, wiki="ccat"
/lore:resume ccat:data-center        → mode=scope, scope="ccat:data-center"
/lore:resume "ffts numa debugging"   → mode=keyword, keyword=...
/lore:resume ccat numa               → mode=keyword, keyword="numa", wiki="ccat"
/lore:resume last week               → mode=recent, days=7
```

Heuristics:
- Argument contains `:` → `scope`
- Argument is a known wiki name (single token, no space) → `wiki`
- Argument is "last N days" or "last week"/"last month" → `recent`
  with days set
- Otherwise → `keyword`
- If two tokens and first is a wiki name → `keyword` scoped to that
  wiki

### 2. Call the MCP tool — exactly one call

Use the `mcp__lore__lore_resume` tool with the parsed arguments. Only
pass non-default values. Examples:

| User input | MCP arguments |
|---|---|
| `/lore:resume` | `{}` |
| `/lore:resume ccat` | `{"wiki": "ccat"}` |
| `/lore:resume ccat:data-center` | `{"scope": "ccat:data-center"}` |
| `/lore:resume numa` | `{"keyword": "numa"}` |
| `/lore:resume ccat numa` | `{"keyword": "numa", "wiki": "ccat"}` |
| `/lore:resume last week` | `{"days": 7}` |

### 3. Render the result

The MCP tool returns a structured dict with a `mode` discriminator and
mode-specific fields. Render it as markdown — the shape is already
clean. For consistency with the CLI's `format_markdown`, use these
section headers:

- `mode == "recent"` → `## Resume: <wiki|all wikis> (last Nd)` then
  `### Recent sessions` and `### Open items`.
- `mode == "keyword"` → `## Resume: <keyword>` then `### Top matches`.
- `mode == "scope"` → `## /lore:resume <scope>` then `### Open issues`,
  `### Open PRs`, `### Recent session notes`.

If the result has an `error` field, surface it verbatim.

## Why this is short

In the CLI-first design, every gather operation lives in
`lore_core/resume.py` and is exposed through both the CLI
(`lore resume`) and the MCP tool (`lore_resume`). The skill is just
the keyboard shortcut and the renderer. Token-economy by construction.

## Important rules

- **One MCP call.** No Glob, no Read, no Grep, no fallback to
  iterative file walks. If the MCP call fails, surface the error —
  do not retry by hand.
- **Read-only.** This skill never writes files.
- **No re-format if the user asked for JSON.** If the user explicitly
  wants raw JSON, suggest `lore resume --json` from a shell instead.

## Related

- `/lore:loaded` — show what SessionStart already cached (no fresh
  gather)
- `/lore:search` — direct FTS query without the open-items / recency
  framing
