# NEXT SESSION — Lore trim-1.0 (citation-anchored notes)

**Updated:** 2026-05-20. Single entry point for picking up the trim.
Read this first, then the docs in the order below.

## Status

- **Design: RESOLVED and committed.** A full grill-with-docs session
  walked the decision tree end-to-end. The authoritative design lives
  in `CONTEXT.md` + the two ADRs (below).
- **Build: NOTHING started.** No pipeline code, no removals.
- **Gate: not yet run.** The trim does not proceed until experiment
  008 passes its pre-committed gate.

## Read order

1. `CONTEXT.md` — the glossary + full resolved design tree (what a v1.0
   note is, the 3-stage verify-and-synthesize pipeline, kept/removed
   scope, removal entanglements, the earn-gate).
2. `docs/adr/0001-trim-to-citation-anchored-notes.md` — *why* we delete
   Curator B/C/surfaces/briefings; what's kept (substrate + journals);
   revival gates; decouple-early/delete-late.
3. `docs/adr/0002-verify-and-synthesize-session-note-pipeline.md` — the
   3-stage pipeline + every rejected alternative + the pre-committed
   earn-gate thresholds.
4. `~/git/lore-experiments/experiments/008-narrow-claim-judge/HANDOVER.md`
   — the keystone experiment briefing (method, data sources, gate).

> **Do NOT act on** `~/.claude/plans/lore-trim-1-0-citation-foundation.md`
> as written — it is **superseded** (it describes a single
> verbatim-anchor contract; the design evolved into the 3-stage
> verify pipeline). Its header points here. Keep it only as history.

## The one number that gates everything

**008 narrow-judge contradicted-recall ≥ 70% vs Opus.** This is the
HARD gate. If a local model can do the narrow "does this one claim's
cited turn support it?" task, the whole Stage-2 verification (and thus
the trim) is viable local-first. If it can't, Stage 2 is hollow and
the trust contract needs rework before anything is deleted.

(006a already showed the *batch* judge fails at 0% recall. 008 tests
the *narrow* per-claim version — a genuinely smaller task.)

## Next action

**Build + run 008.** Adapt `lore-experiments/.../006-self-judge-correct/
run_006a.py`: same client factory / kiconnect config, swap in
`prompts/narrow_judge_v1.json`, loop per-claim (not per-narrative),
resolve `evidence_turns` → turn text from 005's input snapshot.
Pre-commit the ≥70% gate before looking at output. Then 009
(cross-claim resolution) and the regression check.

## Safe-to-start-now work (no gate needed)

Per ADR-0001's *decouple early, delete late*: the decoupling is
reversible and can begin before the gate:
- Salvage `find_orphan_links` out of `lore_curator/c_orphan_links.py`
  into lint/freshness (load-bearing for PRD #65 — must NOT die with C).
- Salvage `rewrite_scopes_in_frontmatter` out of `lore_core/surfaces.py`
  into a scope module.
- Disable B/C/surfaces/briefing entry points (no runs, no MCP exposure,
  no skill entries). Leave the module *files* on disk until the gate
  passes, then delete them + their tests in one sweep.

## Where the work is

- Design docs: merged to `main` (this branch:
  `worktree-grill-trim-1-0`).
- 008 scaffold: `~/git/lore-experiments` branch `master`, committed.
- AI journal entry on this session: `~/git/vault/journals/ai.md`
  (2026-05-20) — two corrections worth remembering.
