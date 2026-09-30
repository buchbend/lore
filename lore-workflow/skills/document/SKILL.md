---
name: lore-workflow:document
description: Bring a repo's Diátaxis docs (tutorial, how-to, reference, explanation) in line
  with the code. Pre-merge — commit docs onto the branch that is about to merge and, at an epic
  tail, mark the PRD shipped. Catch-up — a docs PR for work that already merged. Consolidate —
  a plan-approved sweep of a docs tree that grew wild. Never edits docs/prd or docs/adr content.
  Use after build, or on "document this", "docs catch-up", "consolidate docs", "docs cleanup".
---

# Document

You describe behaviour; you do not change it. Every doc edit belongs to one Diátaxis quadrant.

| Mode | Input | Delivers |
|---|---|---|
| **Pre-merge** | A branch about to merge: `epic/<n>`, a `build` issue branch, `loop/<slug>`. | A commit on that branch. No separate PR. |
| **Catch-up** | A merged epic, PR or commit range. | A docs PR that merges itself on green checks. |
| **Consolidate** | A docs root that grew wild. | One docs PR, after the user approves the plan. The user merges. |

Run `lore style show writing-rules` before you write. The docs follow the writing rules.

## Hard rule: the record stays as written

Do not edit anything under `docs/prd/` or `docs/adr/`. PRDs and ADRs are the human-owned record
of intent and decisions. Read them for context. Do not write, move, rename or delete them.

One sanctioned exception: at an epic tail, `lore workflow prd-ship` flips the PRD's `status` to
`shipped` and adds the line that names the current ADRs. Make no other change there. Before you
commit, check the diff: any other `docs/prd/**` or `docs/adr/**` path is a bug; drop it.

## The four quadrants

- **tutorial** — a guided first walkthrough. A capability someone meets for the first time.
- **how-to** — a recipe for one task. A new command, a new task a user performs.
- **reference** — docstrings plus the autosummary/toctree wiring that surfaces them. A new or
  changed public symbol gets a complete docstring (Parameters, Returns, Raises) and a place in
  the toctree. Do not paraphrase the API in prose.
- **explanation** — background and trade-offs. It may cite an ADR.

`lore_workflow.diataxis` decides the quadrant. `classify(path, public_api=...)` returns the
quadrant or `None`. `is_excluded(path)` flags `docs/prd` and `docs/adr`.
`classify_changeset(changes)` turns a whole diff into an edit plan and never assigns a quadrant
to an excluded path. The heuristic knows the canonical folder names and common synonyms
(`guides` → how-to, `api` → reference, `concepts` → explanation). You write the content.

## Pre-merge and catch-up

1. **Map the deltas.** Read the PRD for intent and the PR bodies for user-facing behaviour.
   Take the diff: `<target>...<branch>` before the merge, the merged range after it. Tag each
   path with its change kind and, for source files, whether it touches the public API.
2. **Classify** the list with `classify_changeset`.
3. **Write** each planned edit to match the code. Keep edits to what the change touched. A
   removed behaviour removes its sentence.
4. **Epic tail only:** run `lore workflow prd-ship docs/prd/NNNN-<slug>.md --adr NNNN` with one
   `--adr` per ADR the epic made or keeps current.
5. **Deliver.**
   - *Pre-merge:* commit onto the branch. Report the commit SHA and the edit plan.
   - *Catch-up:* branch off the integration branch, commit, and open a PR that links the epic.
     Write the body through [`file-issue`](../file-issue/SKILL.md) in PR-body mode. Merge it on
     green checks; a human reviews afterwards. Never merge on red.

An epic that spans repos gets one delivery per repo, each against its own docs layout.

## Consolidate

1. **Inventory.** One table: each doc's path, title, headings and inbound links (toctrees,
   relative links). Mark orphans (no index reaches them) and dangles (links to missing files).
2. **Classify** each doc with `classify`. Where the folder gives no signal, judge from the
   content. A page that mixes quadrants is a finding.
3. **Find the wild growth:** duplicates, contradictions (check the code before picking a side),
   prose that restates the API, mixed-quadrant pages, orphans and dangles. Flag stale content
   only on named evidence: a contradiction with the code, a removed feature, a broken link. Age
   alone flags nothing.
4. **Propose one plan:** per action (`merge A into B`, `move`, `split`, `delete`, `rewire link`,
   `convert to docstring reference`) one line of reason. Group by quadrant, deletions last. Wait
   for the user's approval; they may strike actions. Do not proceed on silence.
5. **Apply** on a branch off the integration branch. A merged page absorbs the unique content of
   the page it replaces before that page goes. Fix every link and toctree a move breaks. Open one
   PR through `file-issue`. The user merges.

## Dry run

On request, or before any write: print the edit plan only, per path the quadrant, "skip" or
"excluded: prd/adr". `tests/fixtures/merged-epic/changeset.json` in the lore repo is a worked
example.

## Stop conditions

A change whose user-facing meaning the PRD and PR bodies do not give; a docs layout with no
recognisable quadrant folders; a doc edit that needs a behaviour change; red checks on the docs
PR that are no docs problem; any edit to `docs/prd` or `docs/adr` beyond `prd-ship`. Report the
edit plan and the PR on completion.
