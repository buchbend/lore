---
name: lore:surface
description: Author a wiki's SURFACES.md via an LLM-guided conversation —
  add one surface or redesign the full set. Opens with one dispatch
  question; both flows commit via `lore surface commit <draft.json>`.
  Run with "/lore:surface <wiki>".
user_invocable: true
---

# Surface Authoring

Guide the user through editing `$LORE_ROOT/wiki/<wiki>/SURFACES.md`.
The skill always opens with one dispatch question — *add* one surface
or *redesign* the full set — regardless of whether SURFACES.md is
empty or populated. No auto-detect, no silent mode switch.

## Arguments

`/lore:surface <wiki>` — the positional is the wiki name (e.g.
`science`). If the wiki name is missing, ask the user once before
starting.

## Step 1 — Gather context (silent)

Call `lore_surface_context(wiki=<wiki>)`. You will get:

- `current_surfaces` — already-declared surfaces (empty if none)
- `surfaces_md_exists` — boolean
- `claude_md_attach` — the wiki's CLAUDE.md `## Lore` block
- `note_samples` — wikilinks to ~3 recent notes per existing type
- `shipped_templates` — `standard`, `science`, `design` template text

Read all of it. Do not show it to the user directly.

## Step 2 — Dispatch question

Ask **one** question:

> "Add a new surface, or redesign the full set?
> (`add` extends the existing vocabulary; `redesign` proposes a complete
> new SURFACES.md and replaces the current one with `--force` on commit.)"

If `surfaces_md_exists` is `false`, mention that — but still ask. The
user may want to design just one surface first and grow the set.

If the answer is `redesign` and `surfaces_md_exists` is `true`, confirm:
*"This will replace SURFACES.md at `<path>` (with `--force`). Continue?"*
Stop on `no`.

Branch on the answer.

---

## Add flow (one new surface)

### Step A1 — Open the conversation

Ask:

> "Describe the new surface in your own words — what does it capture,
> and when should Curator extract one?"

User-facing term is "Curator" — never "Curator B".

If the user asks for a semantic scan first, call `lore_search` with
their description as the query, present the top 5 hits, and ask if any
cluster looks like it would fit this surface.

### Step A2 — Synthesize a full draft

From the user's answer + the context pack, produce a complete surface
spec:

- `name` — lowercase ASCII, `^[a-z][a-z0-9_]*$`
- `description` — one sentence
- `required` — list; always starts with `type, created, description,
  tags` unless there's a reason to drop
- `optional` — list (`draft` is usually present)
- `extract_when` — short prose hint for Curator
- `plural` — only if `<name>s` would be wrong (`study` → `studies`)
- `slug_format` — only if the default `{date}-{slug}` wouldn't suit
- `extract_prompt` — only if needed beyond `description`

Run a semantic-overlap check against `current_surfaces`. If the new
surface sounds like an existing one, say so explicitly and propose
extending the existing surface instead. Let the user decide.

Build a draft-spec JSON with `"operation": "append"` and a single
`"surface": { ... }` block. Call `lore_surface_validate`. Revise
silently on issues (max 2 retries; if still broken, surface the codes
honestly).

### Step A3 — Present

Show the rendered `## <name>` section, a compact diff summary, and any
overlap notes. Ask:

> "Commit this, deepen a specific field, or save as draft?"

Branch as in **Commit / Deepen / Save** below.

---

## Redesign flow (full SURFACES.md set)

### Step R1 — Open the conversation

Ask:

> "What's this wiki for, and what kinds of things do you want to
> capture? A rough list or free-text description — either works."

### Step R2 — Synthesize the full set

Produce a complete SURFACES.md draft: 3–6 surfaces, internally
consistent:

- No semantic overlap between surfaces (`decision` and `choice` don't
  both exist).
- Consistent naming register (all imperative-nouns, or all agent-role
  nouns — pick a lane).
- `type, created, description, tags` in `required` for every surface
  unless there's a reason to drop.
- Always include a `session` surface (Curator writes session notes; the
  wiki needs a slot for them).
- Use `plural`, `slug_format`, `extract_prompt` only where they add
  real value.

Consult `shipped_templates` for inspiration — do **not** pick one
wholesale; build a set tailored to the user's description.

Build a draft-spec JSON with `"operation": "init"` and a `surfaces:
[...]` array. Call `lore_surface_validate`. Revise silently.

### Step R3 — Present

Show the rendered SURFACES.md and a one-line-per-surface summary
("`concept` — ideas that recur across sessions", etc.). Ask:

> "Commit this, refine one surface, or save as draft?"

Branch as in **Commit / Refine / Save** below.

---

## Commit / Deepen-or-Refine / Save (shared)

**Commit:**
1. Write draft to `$TMPDIR/lore-surface-<timestamp>.json`.
2. Run `lore surface commit <path>` (add `--force` for redesign if
   SURFACES.md already existed).
3. Report the receipt JSON path + the new surface's wikilink.

**Deepen** (add flow) — ask which field to tune; ask a focused
question; update the draft; re-validate; return to **Present**.

**Refine** (redesign flow) — ask which surface; run a mini add-flow
loop for that one surface only; preserve all other surfaces; return to
**Present**.

**Save as draft:**
1. Write to `$LORE_ROOT/drafts/surfaces/<wiki>-<name>.json` (add
   flow) or `<wiki>-init.json` (redesign flow).
2. Print: *"Saved. Commit later with `lore surface commit <path>`."*
3. Stop.

## Error handling, rules

- Never edit SURFACES.md directly — `lore surface commit` is the only
  write path.
- Never say "Curator B"; say "Curator".
- If MCP is unreachable, stop honestly; do not fake a context pack.
- If validation keeps failing after 2 retries, surface the issue codes
  verbatim and ask the user how to adjust.
- Commit exits non-zero → show stderr and stop; do not retry
  automatically.
- Do not invent surface fields the user didn't ask for.
- Do not offer to rename or remove existing surfaces — that's a
  separate (future) flow.
