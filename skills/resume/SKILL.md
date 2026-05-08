---
name: lore:resume
description: Load working context from the vault on demand. Single MCP
  call dispatches across no-arg (recent across all wikis), wiki-scoped,
  keyword search, or scope-prefix aggregation. Run with "/lore:resume"
  optionally followed by keywords, a wiki name, or a scope prefix.
user_invocable: true
---

# Resume — load context via the `lore_resume` MCP tool

One MCP call, then render. The tool dispatches four modes; the skill
parses the user's input and renders the result. No Glob, no Read, no
Grep — the gather work lives in the CLI / MCP, free of permission
churn.

## Parse + dispatch

| User input | MCP arguments | Mode |
|---|---|---|
| `/lore:resume` | `{}` | recent (3d) |
| `/lore:resume ccat` | `{"wiki": "ccat"}` | wiki |
| `/lore:resume ccat:data-center` | `{"scope": "ccat:data-center"}` | scope |
| `/lore:resume numa` | `{"keyword": "numa"}` | keyword |
| `/lore:resume ccat numa` | `{"keyword": "numa", "wiki": "ccat"}` | keyword |
| `/lore:resume last week` | `{"days": 7}` | recent |

Heuristics: `:` → scope; known wiki name → wiki; "last N days/week/
month" → recent with days; otherwise keyword. Two tokens with first a
wiki name → keyword scoped to that wiki.

## Render

The tool returns a structured dict with a `mode` discriminator. Use
these section headers (matching the CLI's `format_markdown`):

- `recent` → `## Resume: <wiki|all wikis> (last Nd)` then `### Recent
  sessions` and `### Open items`.
- `keyword` → `## Resume: <keyword>` then `### Top matches`.
- `scope` → `## /lore:resume <scope>` then `### Open issues`,
  `### Open PRs`, `### Recent session notes`.

If the result has an `error` field, surface it verbatim.

## Rules

- One MCP call. No retry-by-hand fallbacks.
- Read-only.
- For raw JSON, suggest `lore resume --json` from a shell.

## Related

- `/lore:context` — show what SessionStart already cached.
- For raw ranked paths without resume framing, use `lore search
  <query>` (shell) or `lore_search` MCP.
