# Trim Lore to citation-anchored session notes; remove Curator B/C, surfaces, briefings

**Status:** accepted (2026-05-20)

Lore expanded too early: abstractions (Curator B's `concepts/`, Curator
C's defrag/hoist passes, surfaces, briefings) were stacked on top of
session notes whose claims could not be verified. The result is a
**credibility cascade** — B abstracts unverified A output, C compounds
B, SessionStart injects the compounded noise into the next session, A
re-cites it. We are trimming Lore to one thing: trustworthy,
citation-anchored session notes producible on cheap local models. The
wedge is *audited memory* — notes whose every claim is mechanically
traceable to a stored transcript turn. Nothing else in the AI-tooling
space does this; everything else Lore aspires to is downstream of a
trustworthy session-note substrate that does not yet exist.

**Kept (substrate, orthogonal to the curator stack):** adapters,
`transcript_sync`, identity + team-mode, `.lore.yml` + scope,
SessionStart + turn hooks, Curator A (buffer/flush + synthesis),
`lore_mcp`, `lore_search`, freshness + verdicts, drain, `git_sync`,
inbox, **and the journals** (pure user/AI scratch — no LLM
abstraction, no propagation, no trust claim, so risk-free).

**Removed (code + tests):** Curator B (`daily_curator.py`), Curator C
(`defrag_curator.py` + `c_*.py`), surfaces (`surface_filer.py` +
surface-specific `surfaces.py` + `surface_cmd.py` + templates),
briefings (`lore_curator/briefing/` + `lore_core/briefing/` +
`briefing_cmd.py` + MCP `handle_briefing_gather`), and B/C-only
helpers (`cluster.py`, `abstract.py`, `c_passes.py`,
`curator_c_diff.py`).

## Considered options

- **Fork a clean v1 repo** — rejected; cut `v1.0.0` in-place instead.
- **Gate dormant, keep all code on disk** (the original plan) —
  rejected in favor of actual deletion, because dead code with live
  entry points rots and misleads. We *decouple early, delete late*
  (see Consequences), but we do delete.
- **Keep briefings** — rejected. Briefings push synthesised content to
  channels that cannot be retracted (Slack/Matrix/email); one-way
  external propagation of any synthesised claim is the exact failure
  mode the trim corrects.

This was stress-tested via flip-probe (two senior-architect subagents
with reversed framings). Both converged on: freeze the abstraction
stack, build the citation contract, treat A as the substrate.

## Consequences

- **Decouple early, delete late.** KEEP code is decoupled from REMOVE
  code now (salvage `find_orphan_links` → freshness, scope-rewrite →
  scope module); the dormant module files are deleted only after the
  earn-gate (below) passes. This keeps the only working note-
  abstraction machinery available as a fallback until the v3
  replacement is proven.
- **Revival gates** (so "dormant" does not decay into "deleted by
  rot"): Curator B unfreezes only when Curator A passes the citation
  validator at ≥95% across 100 consecutive sessions; surfaces gate
  behind B; briefings gate behind a named waiting team. Curator C is
  post-1.x.
- **Existing untrusted content** is cleaned operationally, not via a
  `trust: legacy` quarantine system: ccat `concepts/` git-reverted to
  pre-lore state, private cut back manually, old session notes
  re-derived through the v3 pipeline once it is earned.
- **The earn-gate** (pre-committed thresholds) decides whether the
  trim commits; see ADR-0002.
