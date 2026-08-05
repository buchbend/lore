# ADR 0002: Orchestrate-epic supervision-state split (board on the issue, working context on the epic note)

- Status: Accepted
- Date: 2026-07-10
- Context: epic [#229](https://github.com/buchbend/lore/issues/229),
  PRD [0006](../prd/0006-workflow-lightening-deepening.md), sub-issue
  [#228](https://github.com/buchbend/lore/issues/228)

## Context

`/orchestrate-epic` supervises a whole epic autonomously: it plans batches, dispatches
teammates, crosschecks every PR, and lands the epic. All of that produces *supervision
state* — which features are merged, which teammate got which model tier, why a feature
blocked, what a crosscheck verdict said — and that state has to survive a resume: an editor
restart, a `/compact`, or a fresh session picking the epic back up cold.

Historically the skill kept everything in one GitHub issue comment, written and re-read as
prose, and re-derived repo facts (target branch, deploy gate) and effort bands by eyeballing
the roadmap table. Resume re-scraped that comment with the model. Prose re-derivation is
exactly what PRD 0006 removes: this epic moved the deterministic mechanics into
`lore workflow` (`epic-policy`, `validate-roadmap --json`, `parse-board`), added note-format
v2, and added a capture-suppress flag (`LORE_SUPPRESS_CAPTURE=1`) for dispatched teammates.

That leaves one design question this ADR answers: **once the mechanics are deterministic,
where does each kind of supervision state live?** Two kinds pull in different directions. The
per-feature ledger (feature → tier/batch/state/PR) must be machine-readable, visible to
humans and teammates on GitHub, and single/authoritative across sessions. The working
narrative (tier rationale, dispatch and crosscheck reasoning, escalations, in-flight
decisions) is rich, orchestrator-private, and is exactly what the lore substrate already
captures into session notes. Forcing both into one store made the comment both unreadable and
unparseable, and made resume a model-scraping step.

## Decision

Split supervision state into two durable stores, each with a single writer path, and never
conflate them.

1. **The board — per-feature ledger, on the epic issue.** One comment on the GitHub epic
   issue, identified by the exact marker `<!-- lore-orchestrate-epic:status v1 -->` and a
   Markdown table whose header carries the columns `Feature | Issue | Tier | Batch | State |
   PR` (see `lore_workflow.board_parser`). The orchestrator *emits* that shape; on resume it
   reads it back structurally via `lore workflow parse-board` — never by re-reading the
   Markdown with the model. The board stays on the issue because humans and teammates watch it
   there, and it is authoritative for per-feature state. The orchestrator edits the one
   comment in place; a resumed run never opens a second.

2. **The epic note — working narrative, on the orchestrator's own session note.** The
   orchestrator works on the `epic/<issue>` branch, so deterministic linkage stamps its
   session note with `epics: [<issue>]`; that note *is* the epic note. The orchestrator does
   not write it through an API — it narrates its tier decisions, dispatch choices, crosscheck
   verdicts, and escalations in-session, and capture composes them into the note. On resume,
   `lore_context_pack` surfaces the epic-linked notes and carries that narrative
   into the new session.

3. **Same-session writes only.** The orchestrator writes only its *own* epic note. ADR 0001
   (session-note reopen) permits a session to append to its own note across a resume, but
   forbids any writer from appending to *another* session's note — so a fresh orchestrator
   session reads prior epic notes and starts its own; it never edits theirs.

4. **Teammates are capture-suppressed.** Each dispatched teammate runs with
   `LORE_SUPPRESS_CAPTURE=1`, so it emits no standalone session note. Its outcome (PR, verdict,
   tier deviation) is consolidated by the orchestrator into the single epic note. Without this,
   an N-feature epic would scatter N low-signal teammate notes and no single record of the
   supervision.

## Consequences / Trade-offs

- **Positive.** Per-feature state is deterministic and authoritative — a resumed run reconciles
  from `parse-board` rows, not a prose re-read, and cannot silently misread a half-merged
  board. The narrative is rich and epic-scoped without polluting the ledger. Capture-suppress
  keeps the vault from filling with one fragment per teammate; the epic note is the one
  consolidated supervision record. This matches the project's authoritative-state-over-derived
  stance: one writeable ledger, breadcrumbs (session notes, commits) elsewhere.
- **Negative — two stores can drift.** A board row can say `merged` while the epic note's
  narrative lags, or vice versa. The rule is: the board is authoritative for per-feature state;
  the epic note is advisory narrative. On any conflict, reconcile toward the board.
- **The orchestrator is *not* capture-suppressed.** The epic note exists only because capture
  runs for the orchestrator session. Suppressing the orchestrator too would erase the very
  record this ADR relies on — so suppression is teammates-only, by construction.
- **One epic note per orchestrator session, not per epic.** Across resumes the epic
  accumulates several epic-linked notes (one per orchestrator session), all surfaced together
  by `lore_context_pack` on the epic key. The board stays single. This is the direct
  consequence of the same-session-writes rule and is acceptable: the notes are a lab-notebook
  trail, not a single canonical document.

## Alternatives considered

- **One store — everything in the issue comment (the prior design).** Rejected: prose
  re-derivation and model-scraping on resume are exactly what PRD 0006 removes, and a single
  comment cannot hold a rich narrative without becoming both unreadable and unparseable.
- **Write each teammate's outcome into its own session note and aggregate on resume.**
  Rejected: N fragments per epic, a cross-session aggregation cost on every resume, and no
  single consolidated record. Capture-suppress plus orchestrator consolidation is simpler and
  yields one note.
- **A shared canonical epic note appended by every orchestrator session across resumes.**
  Rejected: it requires one session to write another session's note, which ADR 0001 forbids —
  and cross-session note-appending reopens the duplication hazard ADR 0001 closed.
