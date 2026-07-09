---
name: lore-workflow:document-epic
description: After an epic merges, update a repo's Diátaxis documentation (tutorials, how-to,
  reference, explanation) to reflect the implemented state, then open a docs PR that
  auto-merges on green. NEVER touches docs/prd or docs/adr. Use after an epic lands and its
  user-facing docs need to catch up. Standalone and cross-repo aware.
---

# Document Epic

You are the **documenter**: a merged epic changed what the software does, and the
user-facing docs must catch up. You update the **Diátaxis four** to match the implemented
state, then open a docs PR and let it auto-merge on green — a human reviews post-hoc. You do
not change behavior; you describe it.

**Input:** a merged epic (issue number or URL), possibly spanning repos.
**Mode:** autonomous — run the loop without asking. Stop only to report completion or to
escalate a hard blocker (see Stop conditions).

## Hard rule — never edit the canonical record

**NEVER modify anything under `docs/prd/` or `docs/adr/`.** PRDs and ADRs are the canonical,
human-owned record of intent and decisions. This skill *reads* them — the PRD for what the
epic set out to do, ADRs for the "why" an *explanation* doc may cite — but it must never
write, rename, or delete them. If your edit plan contains a `docs/prd/**` or `docs/adr/**`
path, that is a bug: drop it. The classification heuristic (below) enforces this by returning
no quadrant for those paths; trust it and double-check the final diff yourself.

## The Diátaxis four (and what each means here)

Every documentation edit you make belongs to exactly one quadrant
(https://diataxis.fr/). Classify before you write:

- **tutorial** — learning-oriented. A guided, first-time, start-to-finish walkthrough. New
  user-facing capability that someone would meet for the first time → extend or add a
  tutorial step.
- **how-to** — task-oriented. A recipe to accomplish one specific goal ("how do I export a
  widget from the terminal"). New CLI command, new task someone performs → a how-to.
- **reference** — information-oriented, and for us this means **docstrings + the
  autosummary/toctree wiring that surfaces them — NOT hand-written prose.** A new or changed
  *public* API → make sure the symbol has a complete docstring (Parameters / Returns /
  Raises) and is wired into the Sphinx `autosummary`/`toctree` so it appears in the rendered
  API reference. Do not paraphrase the API in prose; fix the docstring and the wiring.
- **explanation** — understanding-oriented. Background, trade-offs, the "why". A design
  choice worth understanding (it may cite an ADR) → an explanation doc.

## Loop

**Map the deltas.** Read the merged epic with `gh ... --json` (see the repo's own
conventions doc, if any).
From it gather three sources of truth: the **merged PRD** (intent), the **sub-issue PRs**
(per-feature acceptance notes that reveal which behaviors are user-facing), and the
**cumulative epic diff** (every path the epic touched, with its change kind). Build one list
of changed paths, each tagged with its change kind and — for source files — whether it
touches the **public API** (a non-underscore symbol exported from the package). This list is
the input to classification.

**Classify into quadrants.** Run each changed path through the deterministic heuristic in
the `lore_workflow.diataxis` module (part of the `lore` package this plugin depends on):
`classify(path, public_api=...)` returns the
quadrant (`tutorial` / `how-to` / `reference` / `explanation`) or `None` for changes that
warrant no docs edit, and `is_excluded(path)` flags the `docs/prd`/`docs/adr` paths you must
skip. Use `classify_changeset(changes)` to turn the whole diff into an edit plan in one call;
its output never assigns a quadrant to an excluded path. Adopt the target repo's actual docs
layout — the heuristic recognises the canonical directory names plus common synonyms
(`guide`/`guides` → how-to, `api` → reference, `concepts`/`discussion` → explanation). The
heuristic decides *which quadrant*; you still write the *content*, judging from the PRD and
sub-issue notes what each doc should say.

**Write the edits.** For each planned edit, update the doc in its quadrant to match the
implemented state. For **reference**, edit docstrings on the changed public symbols and the
autosummary/toctree entries — never substitute prose for a docstring. Keep edits scoped to
what the epic changed; do not rewrite untouched docs. Re-confirm no `docs/prd`/`docs/adr`
path is in your diff.

**Open the docs PR.** Branch off the repo's integration branch, commit the doc edits, and
open a PR that summarizes the documented deltas and links the epic and its sub-issues. **Auto-
merge it on green CI** — documentation lands without blocking on human review; a human reviews
post-hoc. Never auto-merge on red.

**Cross-repo.** An epic may span repos. Repeat the loop per affected repo, each against its
own docs layout and its own integration branch. One docs PR per repo.

## Dry-run

When asked to dry-run (or before writing anything), produce the edit plan only: run the
changeset through `classify_changeset` and print, per path, the quadrant (or "skip" /
"excluded: prd/adr"). The fixture under `tests/fixtures/merged-epic/` is a worked example —
its `changeset.json` carries the expected quadrant for each path.

## Stop conditions (escalate, don't guess)

A change whose user-facing meaning you cannot determine from the PRD + sub-issue notes; an
ambiguous docs layout with no recognisable quadrant directories; a doc edit that would
require a behavior change to be correct; CI red on the docs PR that is not a docs problem; or
anything that would force a `docs/prd`/`docs/adr` edit. Otherwise keep going. Report the edit
plan (path → quadrant) and the PR on completion.
