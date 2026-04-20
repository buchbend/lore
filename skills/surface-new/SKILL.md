---
name: lore:surface-new
description: Add a new surface to a wiki's SURFACES.md via an LLM-guided
  conversation. Proposes a full draft from one open question, allows
  per-field deepening, commits via `lore surface commit <draft.json>`.
  Run with "/lore:surface-new <wiki>".
user_invocable: true
---

# Surface Authoring — add one surface

Guide the user through adding a new surface to `$LORE_ROOT/wiki/<wiki>/SURFACES.md`. One open question, synthesis-first, optional per-field deepening, hybrid commit.

## Arguments

`/lore:surface-new <wiki>` — the positional is the wiki name (e.g. `science`).

If the wiki name is missing, ask the user once before starting.

## Step 1 — Gather context (silent)

Call the MCP tool `lore_surface_context(wiki=<wiki>)`. You will get:

- `current_surfaces` — already-declared surfaces
- `claude_md_attach` — the wiki's CLAUDE.md `## Lore` block (what the wiki is for)
- `note_samples` — wikilinks to ~3 recent notes per existing type
- `shipped_templates` — `standard`, `science`, `design` template text for inspiration

Read all of it. Do not show it to the user directly.

## Step 2 — Open the conversation

Ask **one** question:

> "Describe the new surface in your own words — what does it capture, and when should Curator extract one?"

(User-facing term is "Curator" — do not say "Curator B".)

If the user asks you to run a semantic scan of the wiki first, call `lore_search` with their description as the query, present the top 5 hits as a compact list, and ask if any cluster looks like it would fit this surface before continuing.

## Step 3 — Synthesize a full draft

From the user's answer + the context pack, produce a **complete** surface spec:

- `name` — lowercase ASCII identifier, `^[a-z][a-z0-9_]*$`
- `description` — one-sentence prose
- `required` — list; always starts with `type, created, description, tags` unless there's a reason to drop one
- `optional` — list (`draft` is usually present)
- `extract_when` — short prose hint for Curator
- `plural` — only if `<name>s` would be wrong (e.g. `study` → `studies`)
- `slug_format` — only if the default `{date}-{slug}` wouldn't suit (e.g. `{citekey}` for papers)
- `extract_prompt` — only if you need to tell Curator something the description doesn't

Before presenting: run a **semantic-overlap check** against `current_surfaces`. If the new surface sounds like an existing one, say so explicitly and propose extending the existing surface instead. Let the user decide.

Build a draft-spec JSON:

```json
{
  "schema": "lore.surface.draft/1",
  "wiki": "<wiki>",
  "operation": "append",
  "surface": { ... }
}
```

Call `lore_surface_validate(wiki=<wiki>, draft=<draft>)`. If it returns issues, revise the draft until clean — do **not** surface validation noise to the user; fix it silently and try again (max 2 retries; if still broken, report the issue honestly).

## Step 4 — Present

Show the user:

- The rendered `## <name>` section (from `rendered_markdown`)
- A compact summary of the diff (how SURFACES.md will change)
- Any overlap notes from Step 3

Ask:

> "Commit this, deepen a specific field, or save as draft?"

## Step 5 — Branch

**Commit:**
1. Write the draft to a temp file: `$TMPDIR/lore-surface-<timestamp>.json`.
2. Run `lore surface commit <path>` via the Bash tool.
3. Report the receipt JSON path + the new surface's wikilink.

**Deepen:**
1. Ask the user which field to tune. Accept free text.
2. For that field, ask a focused question (e.g. for `required`: "Any required fields beyond type/created/description/tags?").
3. Update the draft, re-validate, return to Step 4.

**Save as draft:**
1. Write to `$LORE_ROOT/drafts/surfaces/<wiki>-<name>.json`.
2. Print: *"Saved. Commit later with `lore surface commit <path>`."*
3. Stop.

## Error handling

- MCP server not reachable → say so honestly, stop. Do not fake a context pack.
- Validation keeps failing → surface the issue codes verbatim + ask the user how they want to adjust.
- Commit exits non-zero → show the receipt stderr and stop; do not retry automatically.

## What you do NOT do

- Do not edit SURFACES.md directly. The commit CLI is the only write path.
- Do not invent surface fields the user didn't ask for (e.g. don't add `citekey` to `required` unless the user described a paper-like thing).
- Do not mention "Curator A/B/C" — always just "Curator".
- Do not offer to rename or remove existing surfaces — that's a separate (future) flow.
