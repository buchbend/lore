---
title: Workflow lightening & deepening on the lore substrate
status: draft
epic: https://github.com/buchbend/lore/issues/229
repos:
  - buchbend/lore
---

# PRD 0006: Workflow lightening & deepening on the lore substrate

> Source of truth for this epic. Tracker: [epic issue](https://github.com/buchbend/lore/issues/229).
> The epic links here; this file is not embedded in the issue body.

## Problem

The workflow skills (`orient`, `orchestrate-epic`, `seed-epic`, `to-epic`) were
ported and built by the two preceding epics, which shipped a deterministic
substrate — `lore_context_pack`, `lore codemap`, `lore tier resolve`, linkage
frontmatter, and the `lore workflow` command group — but deliberately deferred
rewiring the skills to consume it. Both PRD 0003 and PRD 0004 name that rewiring
as a follow-up epic. The plumbing exists; nothing calls it.

The cost of that gap shows up in four places:

- **Orient burns explorer tokens for context the substrate already serves.** Its
  fan-out spawns mid-tier explorer subagents across four facets unconditionally;
  the Docs-&-decisions and Prior-art facets are exactly what `lore_context_pack`
  now returns deterministically, but orient never calls it.
- **Orchestrate-epic re-derives deterministic mechanics every run.** At ~293
  lines it spells out target-branch and deploy-gate detection, effort-band
  classification, and — the largest block — hand-maintains a GitHub issue comment
  as a state database that it re-scrapes with the model on every resume.
- **Handover-on-notes does not pay off because the notes are noisy.** Every
  dispatched teammate session writes its own standalone note, so an epic scatters
  low-signal fragments across the vault instead of leaving one high-signal record.
- **Notes are hard to skim.** Titles are cryptic date-stamped slugs; bodies open
  with a standalone bold summary separated from a verbose body, so the signal is
  buried and the reader cannot bail early.

## Solution

Rewire the workflow skills to call the substrate, and sharpen the note format so
handover-on-notes is worth doing.

- **Orient gates its fan-out.** It pulls `lore_context_pack` (plus repo-docs
  listing) once, up front. The Docs-&-decisions and Prior-art facets are served
  from the pack; an explorer subagent is spawned only for a facet the pack leaves
  thin. Cross-repo stays an unconditional explorer (genuine cross-boundary
  judgement); Code-map stays on `lore codemap`. `to-epic`'s repo-exploration step
  adopts the same intake.
- **Orchestrate-epic goes on a prose diet.** Deterministic mechanics move behind
  `lore workflow` subcommands. The supervision board stays on the GitHub issue —
  where humans and teammates watch it — but becomes machine-readable and is parsed
  deterministically instead of model-scraped. The orchestrator's own working
  context rides on a single composed epic note.
- **Teammate sub-sessions stop self-capturing.** The orchestrator passes a
  capture-suppress signal when it dispatches a teammate; the teammate leaves no
  standalone vault note, and the orchestrator composes one high-signal epic note
  from the PRs and teammate results.
- **Note format v2.** Titles gain a scope prefix and a composed human name; bodies
  open with a bold lead sentence inline in a single block the reader can skim and
  bail from, followed by tighter single-weight prose.

A manual tier-choice review pass tightens delegation guidance. Spawn-gate
enforcement and any workflow/tier telemetry are explicitly out of scope.

## Implementation decisions

**Orient fan-out gate.** `orient` calls `lore_context_pack` (and
`lore_repo_docs_list`/`lore_repo_docs_fetch`) before any fan-out. A facet is
served directly from the pack when the pack returns content for it; the explorer
subagent for that facet is spawned only when the pack is thin or empty. Code-map
remains on `lore codemap`; Cross-repo remains an unconditional explorer. `to-epic`
step 2 mirrors this intake.

**`lore workflow` growth, bounded by a threshold.** The command group grows by
exactly what is non-trivial *and* reused or test-worthy; one-liners stay inline in
the skills. Concretely:

- `lore workflow epic-policy` — resolves, per involved repo, `{target_branch,
  deploy_gate}` from the repo's `AGENTS.md` markers plus git branch state. Pure,
  deterministic, exit-coded.
- `lore workflow validate-roadmap --json` — extends the existing validator to emit
  `{rows, repos, edges}` counts so effort-band selection is a pure function of the
  validator's output rather than a re-parse.
- A supervision-board parser — turns the epic's machine-readable board comment into
  structured state (feature × repo × status), so resume reads it deterministically.

These are CLI-only; no MCP twin is added until a call site needs one.

**Supervision-state split (recorded as an ADR).** Board state (feature × repo ×
status) stays on the GitHub epic issue comment, made machine-readable and parsed by
the board parser. The orchestrator's working context (tiers chosen, teammates
dispatched, crosscheck verdicts, in-flight marker) rides on the orchestrator's own
session note, keyed by linkage `epics: [<n>]`, read back on resume via
`lore_context_pack`. Only same-session writes occur — the
orchestrator never writes another session's note — which keeps the mechanism
compatible with the session-note reopen ADR. Teammate notes are read-only inputs.

**Sub-session consolidation.** The orchestrator passes a capture-suppress signal
(an environment flag on dispatch) to each teammate session; a suppressed session
writes no standalone vault note. The orchestrator composes a single epic note from
the merged PRs and teammate results. Default (unsuppressed) capture is unchanged.

**seed-epic lift.** `seed-epic` populates the seed issue's Origin and Findings from
the current session's note when one is present (Origin from linkage
`{repo, epics, prs}`, Findings from the note body), falling back to freehand when
the note is thin. The seed issue references its source note so a cold `/orient` can
pull it.

**Note format v2.** Curator-A compose produces a title of the form
`scope: composed-name` (scope first, then a compiled human name), and a body whose
first block opens with a bold lead *sentence* inline — not a standalone bold line —
followed by tighter single-weight prose in the same block. The format is specified
in `CONTEXT-FORMAT.md` and refines the ratified note essence voice.

**Naming.** Orchestrate-epic's homegrown codemap-symbol excerpt is renamed
"codemap excerpt"; "context pack" is reserved for `lore_context_pack`. `CONTEXT.md`
gains glossary entries for `lore_context_pack`, codemap excerpt, the two handover
senses, the epic note, and `workflow`/`skill`.

## Testing decisions

Tests exercise external behavior of the deterministic pieces the skills call; the
skill prose itself is not unit-tested, but every mechanic it delegates to is.

- `lore workflow epic-policy` — table tests over fixture repo roots: `AGENTS.md`
  present/absent, `develop` vs `main` present, asserting the returned
  `{target_branch, deploy_gate}`.
- `validate-roadmap --json` — asserts emitted `{rows, repos, edges}` counts against
  a fixture roadmap, alongside the existing validity checks.
- Board parser — parses a fixture board comment to structured state; a malformed
  comment yields a clear error rather than a silent misread.
- Capture-suppress — a session launched with the suppress flag writes no note; the
  default path still writes one (behavior, not internals).
- Note format v2 — Curator-A compose output over a fixture: title carries the
  `scope:` prefix; the body's first block leads with an inline bold sentence and
  no standalone bold line.

Prior art: the existing roadmap-validator tests and the curator compose tests are
the models to extend.

## Out of scope

- **All spawn-gate work** — Cursor enforcement verification, provisional
  tier-column promotion, and ports to further hosts — deferred to a separate epic.
- **Workflow/tier usage telemetry / instrumentation** — the observability epic
  (PRD 0005) owns the event spine; this epic adds no second event format.
- **New host adapters** (codex / opencode / copilot-cli / gemini-cli).
- **Any re-introduction of auto-promoted context** — the deleted curators stay
  deleted; all deepening here is pull-only and pointer-based.
