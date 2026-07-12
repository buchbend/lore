---
title: "Substrate trim: cut dead surface, consolidate CLI, split hooks"
status: draft
epic: https://github.com/buchbend/lore/issues/257
repos:
  - buchbend/lore
---

# PRD 0007: Substrate trim — cut dead surface, consolidate CLI, split hooks

> Source of truth for this epic. Tracker: [epic issue](https://github.com/buchbend/lore/issues/257).
> The epic links here; this file is not embedded in the issue body.

## Problem

The substrate has accumulated far more surface than the product uses. A
full-repo audit (traced through plugin hook wiring, spawn roles, skill
markdown, and the MCP server) found:

- Of 34 top-level CLI command groups, only five are machine-called
  (`hook`, `curator`, `transcripts sync`, `mcp`, and codemap via direct
  import). Several of the rest are pure duplicates of one another
  (`detach` is `attach`'s inverse, `registry` re-covers `scopes`,
  `on`/`off` are one toggle in two groups) or have zero callers of any
  kind (`completions`).
- Of 18 MCP tools, five have no caller anywhere — no skill, template,
  integration rule, or hook ever routes a model to them. Ambient
  guidance routes models to `lore_search`/`lore_drill` only.
- Roughly 1,700 lines across eight modules are retired-feature corpses
  (the per-slice noteworthy classifier, the threads surface, the
  narrative-kind selector) that are imported only by their own tests but
  read as live pipeline, making the capture flow hard to trace.
- The hook entrypoint is ~2,600 lines, of which only ~30% is genuine
  hook plumbing; SessionStart context assembly, capture-side flush
  routing, heartbeat scheduling, and drain rendering are stranded in the
  CLI layer.

This carries real cost: confusing surface (even for the maintainer),
maintenance drag on every change, and instability for downstream
consumers — the PMO office explicitly defers wiring against Lore until
its surfaces stop moving.

## Solution

One trim epic that deletes what nothing calls, folds what duplicates,
and decomposes what has grown monolithic — with zero user-facing
behavior change beyond removing the observability aliases that epic
#183 already deprecated with an explicit removal notice.

After the trim: ~15 visible CLI groups (from 34), 13 MCP tools (from
18), no retired-feature code masquerading as live pipeline, and a hook
entrypoint that is thin dispatch over properly-layered core/curator
modules. All current user-facing scope is kept.

## Implementation decisions

- **Deletion set is evidence-based and closed.** Only modules verified
  to have no production importer are deleted: the noteworthy cluster
  (`noteworthy` + `noteworthy_features`), the narrative cluster
  (`narrative_kind` + `decision_signals`), the threads cluster
  (`threads` + `topic_files`), `summary_block`, `projects/router`, the
  empty `lore_runtime` and `surface_templates` packages, the workflow
  `diataxis` module, the unreferenced transcript-purge script, and the
  no-op staleness hygiene pass. Their dedicated tests go with them.
- **Adapter seam survives the stub trim.** The `cursor_agent` and
  `vscode_copilot` adapter stubs are unreachable (no integration string
  ever selects them) and are deleted, but `protocol` and `registry`
  remain as the extension seam: cursor/copilot transcript capture stays
  on the roadmap and future adapters register at the same point. The
  Cursor install target is unrelated and untouched.
- **MCP cuts are exposure-only.** `lore_index`, `lore_catalog`,
  `lore_wikilinks`, `lore_journal_read`, `lore_briefing_gather` come off
  the server; shared core modules behind them stay wherever another
  caller exists (wikilink expansion inside `lore_drill`, the briefing
  CLI, gated journal writes). The server's exposed-tools header is
  rewritten to match the real list.
- **CLI folds preserve verbs users type.** `lore on`/`lore off` keep
  their spelling but collapse to one implementation; `detach` and
  `attachments` become `attach` subcommands; `registry` verbs move under
  `scopes`; `journal` is hidden (parked feature, code stays); one-shot
  upgrade paths (frontmatter migrations, slug backfill, open-items
  rewrite) unify under `lore migrate`. The deprecated
  `log`/`runs`/`proc`/`news` aliases are removed outright — `status` and
  `trace` are the surviving observability surfaces.
- **hooks decomposition follows the layering fence.** Capture routing
  and heartbeat move to `lore_curator`, SessionStart assembly and drain
  rendering to `lore_core`; `lore_cli` imports downward only. Handlers
  stay as thin shells so plugin hook wiring is untouched.
- **Hotspot splits are mechanical.** `run_lint` splits per check,
  `_apply_outcome` per outcome, the install `_helpers` grab-bag
  relocates to an honestly-named core module (install-specific helpers
  stay put), and the redundant hygiene `_run_*` wrappers collapse into
  the pass registry.
- **Skill roster shrinks by two.** The three grill skills merge into one
  parameterized skill that keeps all existing trigger phrases.

## Testing decisions

- Existing suites are the behavior contract. Refactor slices (hooks
  decomposition, hotspot splits) must pass existing tests with at most
  import-path edits — semantic test changes indicate a behavior change
  and fail the slice.
- Tests dedicated to deleted features are deleted with them, not ported.
  Folded CLI groups get their tests rewritten against the merged verb;
  alias-level tests are dropped rather than translated.
- Deletion slices verify absence: grep-clean of removed names across
  `lib/`, plus clean package imports.
- End-to-end smoke: a SessionStart hook run renders an identical banner
  before/after the hooks decomposition; `lore lint` and a curator flush
  produce identical output on a fixture vault before/after the hotspot
  splits.

## Out of scope

- Implementing cursor/copilot transcript capture (roadmap; only the dead
  stubs are removed here).
- Any change to the surviving observability surfaces (`status`, `trace`,
  `doctor`) or other PRD 0005 deliverables.
- Shipping or extending the parked journal feature (it is only hidden
  from help).
- The ruff CI gate and violation burn-down (buchbend/lore#196).
- New features of any kind — this epic only removes and reorganizes.
