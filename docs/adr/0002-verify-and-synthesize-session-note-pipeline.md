# Session notes are produced by a three-stage verify-and-synthesize pipeline

**Status:** accepted (2026-05-20)

A v1.0 session note is title + grounded lede + grounded body in a
single retrieval-safe region. The body is a structured `claims[]`
array; each claim carries `{text, kind, evidence_turns, quote}` where
`quote` is a verbatim substring of a cited turn. Notes are produced by
three stages, run *deferred* after the session boundary (a fast
crash-safe stub is written at SessionEnd; the pipeline ripens it into
a verified note in the background):

1. **Generate** — one LLM call emits the P1-extended schema (title +
   `lede_claims[]` + `claims[]`, each with a `quote`).
2. **Verify** — per-claim, not batch. *Pass 1* triages all claims in
   parallel (mechanical `quote in turn.content` + a narrow kind-aware
   semantic judge). *Pass 2* repairs failures sequentially against a
   growing verified core, chronologically by first evidence turn.
   Bounded retry with explicit failure-feedback (the turn text is
   shown, turning "guess a quote" into "copy a span"); allowed
   outcomes are re-cite / drop / certainty-lowering kind-demotion
   (e.g. `decision`→`leaning`) — never claim-text weakening. Outcome
   is a three-tier trust flag (`trusted | degraded | failed`).
3. **Resolve** — one LLM call over the verified claim set (no
   transcript re-read): assign supersession (`active | superseded |
   tentative`) and write the resolved lede.

Cost is ≈ 7–17 model calls per session. This is deliberate: trust is
bought with calls, and the calls are deferred where latency is
invisible.

## Considered options (the trust bar)

- **Existence-only** (turn indices resolve) — rejected as the *sole*
  bar; it cannot catch a real-but-wrong citation.
- **Pure verbatim-quote** (substring only) — kept as the cheap
  mechanical gate, but insufficient alone: selective quoting passes it
  ("considered Sonnet" cited to support "decided Sonnet").
- **Batch supports-by-judge** (atomize + judge a whole narrative in
  one call) — rejected; experiment 006a measured local models at 0%
  contradicted-recall on this task even with reasoning=high.
- **Narrow per-claim judge** (this decision) — the batch failure does
  not imply locals can't judge; a single claim-vs-turn yes/no is a far
  smaller task. Whether it calibrates is the keystone experiment (008).

## Considered options (note shape & retraction)

- **End-state-only claims** — rejected; requires retraction detection
  at generation time, which experiment 007 showed is unsolved for
  local narrators.
- **Time-anchored claims + Stage-3 resolution** (this decision) —
  each claim is true at its cited moment (cheap, individually
  verifiable); end-state coherence is a separate post-hoc pass over
  *already-grounded* claims. 007 never tested this architecture (its
  probes were all generation-time), so we are in untested, not
  disproven, territory.
- **Two-region notes** (PRD #92, reload-safe + human-only) — rejected;
  the human-only region existed to quarantine ungrounded model prose,
  and this pipeline produces none. Collapsed to a single grounded
  region.

## Consequences

- **Local-first is preserved but unproven.** The whole architecture
  assumes the narrow judge (Stage 2) and cross-claim resolver (Stage
  3) calibrate on local models. This is gated by pre-committed
  experiments before the trim commits:
  - **008** narrow-judge contradicted-recall vs Opus **≥70%** — HARD.
  - **009** supersession-recall on a retraction-dense input **≥60%** —
    DEGRADE (a miss ships time-anchored claims and defers Stage-3
    resolution to 1.x, rather than blocking release).
  - **Regression**: end-to-end published-claim factuality **≥0.85
    mean** (the `quote` requirement must not tank the 007 baseline of
    0.89) — HARD.
  - **Survival floor ≥40%** + a liveness check (verification must
    demonstrably reject at least some claims) — HARD.
  - **Latency <10 min/session p90** — HARD.
  - Pilot on **both** `private` (discussion) and `ccat` (work-grind);
    input class reverses factuality/selection effects (003/004).
- **`decision` is kept despite being the schema's footgun** because it
  is the highest-value signal; it is hardened by a stricter kind-aware
  judge test and the demotion repair path.
