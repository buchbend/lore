---
name: lore:surface
description: Author a wiki's SURFACES.md via an LLM-guided conversation —
  add one surface or redesign the full set. Opens with one dispatch
  question; both flows commit via `lore surface commit <draft.json>`.
  Run with "/lore:surface <wiki>".
user_invocable: true
---

# Surface Authoring

Edit `$LORE_ROOT/wiki/<wiki>/SURFACES.md` via an interview. Always
opens with one dispatch question — *add* one surface or *redesign* the
full set — regardless of whether SURFACES.md is empty or populated.

If the wiki name is missing, ask once before starting.

## Step 1 — Gather context (silent)

Call `lore_surface_context(wiki=<wiki>)`. Read `current_surfaces`,
`surfaces_md_exists`, `claude_md_attach`, `note_samples`,
`shipped_templates`. Don't show the pack to the user.

## Step 2 — Dispatch question

> "Add a new surface, or redesign the full set?"

If `redesign` and `surfaces_md_exists`, confirm: replacing SURFACES.md
needs `--force`. Stop on `no`.

## Add flow

Ask: *"Describe the new surface in your own words — what does it
capture, and when should Curator extract one?"* (User-facing term is
"Curator", never "Curator B".) Synthesize a complete surface spec:

- `name` (lowercase ASCII, `^[a-z][a-z0-9_]*$`), one-sentence
  `description`, `required` (always starts with `type, created,
  description, tags` unless reasoned otherwise), `optional` (usually
  `draft`), `extract_when` hint.
- `plural` / `slug_format` / `extract_prompt` only when needed.

Run a semantic-overlap check vs `current_surfaces`; if the new surface
sounds like an existing one, propose extending instead.

Build a draft with `"operation": "append"` and a single `surface`
block. Validate via `lore_surface_validate`; revise silently (max 2
retries; report codes if still failing).

## Redesign flow

Ask: *"What's this wiki for, and what kinds of things do you want to
capture?"* Synthesize 3–6 surfaces, internally consistent: no semantic
overlap; one naming register; `type, created, description, tags` in
`required` for every surface; always include `session`. Use
`shipped_templates` for inspiration but don't pick wholesale.

Build a draft with `"operation": "init"` and a `surfaces: [...]`
array. Validate as above.

## Step 3 — Present + branch

Show the rendered markdown + a one-line-per-surface summary + any
overlap notes. Ask: *"Commit, deepen/refine, or save as draft?"*

- **Commit** — write draft to `$TMPDIR/lore-surface-<ts>.json`, run
  `lore surface commit <path>` (`--force` for redesign-replacing-
  existing). Report receipt + new wikilinks.
- **Deepen** (add) — pick a field, ask focused question, update,
  re-validate, return to Present.
- **Refine** (redesign) — pick a surface, run a mini add-flow on it,
  preserve the rest, return to Present.
- **Save** — write to `$LORE_ROOT/drafts/surfaces/<wiki>-<name>.json`
  (or `<wiki>-init.json`); print the commit command; stop.

## Hard rules

- Never edit SURFACES.md directly — `lore surface commit` is the only
  write path.
- MCP unreachable → stop honestly. Validation failing twice → surface
  the codes verbatim. Commit non-zero → show stderr and stop.
- Don't invent fields the user didn't ask for; don't rename or remove
  existing surfaces (separate future flow).
