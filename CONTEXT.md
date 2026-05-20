# Lore — domain context

Glossary of terms with tight, shared meanings. Add a term here the
first time it gets sharpened in a grill or design discussion. If a
term is being used loosely, sharpen it before adding.

## Curators

- **Curator A** — the *session-note composer*. Runs per session (buffer
  + flush). Reads transcript turns; writes session notes under
  `sessions/<handle>/YYYY/MM/`. Per-session granularity.
- **Curator B** — the *daily abstractor*. Lifted concepts / decisions /
  papers from session notes into the wiki proper. Per-day granularity.
  **Dormant as of v1.0.** Removed from the codebase entirely (not just
  gated): module + tests deleted.
- **Curator C** — the *defrag / converge pass*. Adjacent-merge,
  auto-supersede, cross-scope hoist, orphan-link repair. Weekly
  granularity. **Dormant as of v1.0.** Removed from the codebase
  entirely.

## Trust scopes — what stays vs. what goes in v1.0

- **Substrate (kept):** adapters (turn extraction), `transcript_sync`
  (gitignored per-wiki mirror), `identity` + team-mode, `.lore.yml` +
  scope, SessionStart + turn hooks, Curator A (buffer/flush +
  Phase-2 narration), `lore_mcp` server, `lore_search` (FTS),
  `freshness` + verdicts, `drain` + run_log, `git_sync`, inbox.
- **Journals (kept):** `lore_core/journal.py` + `lore_journal_*`
  MCP handlers. Reason: pure user/AI scratch — no LLM abstraction,
  no propagation, no trust claim. Survives the trim risk-free.
- **Removed:** Curator B, Curator C, surfaces, briefings. Code + tests
  deleted; user-facing skill entries removed.

### Removal is not a clean delete — entanglements (mapped 2026-05-20)

Six consumer sites in KEEP code reference REMOVE code; each is
rewired or its dependency salvaged:
1. `lore_curator/__init__.py:27` exports `run_curator_c` → drop export.
2. `lore_cli/curator_cmd.py` imports B + C runners → drop those subcommands.
3. `lore_cli/briefing_cmd.py` → delete the command.
4. `lore_mcp/server.py` dispatch → drop `handle_briefing_gather`,
   `handle_surface_context`, `handle_surface_validate`. **Keep**
   `handle_journal_write/read` (journals stay).
5. `lore_core/lint.py:967` imports `find_orphan_links` from
   `c_orphan_links` — **load-bearing for PRD #65 freshness** (orphan
   set feeds the read-time staleness check). **Salvage:** relocate
   `find_orphan_links` into lint/freshness; do NOT delete with C.
6. `lore_core/schema.py`, `git_sync.py`, `scopes_cmd.py` import from
   `lore_core/surfaces.py`. Decompose surfaces.py:
   - **Salvage** `rewrite_scopes_in_frontmatter` (scope rewriting is a
     KEEP primitive) → relocate to a scope module.
   - **Remove** surface-aware schema validation (`required_fields_for`
     via SURFACES.md) — moot once v3 notes have fixed schema fields
     and surface-typed notes are gone.

The deletion set is ~80% clean; the salvage list is small and
determined (orphan-link detection + scope rewriting).

### Implementation ordering — decouple early, delete late (decided 2026-05-20)

Interleaved (c):
- **Now:** disable B/C/surfaces/briefing entry points (no runs, no MCP
  exposure, no skill entries) AND do the salvage relocations
  (orphan-links → freshness, scope-rewrite → scope module) so KEEP
  code no longer imports REMOVE code. Leave the dormant module *files*
  on disk.
- **After pilot green:** delete the dormant files + tests in one sweep.

Principle: decouple early (safe, reversible — moving functions, not
deleting capability); delete late (irreversible — gate behind proof).
Pure remove-first is rejected: it deletes the only working note-
abstraction machinery before the v3 replacement is proven.

## The trustworthy-note substrate (from `lore-experiments`)

The session-note model that the experiments converged on (and the
v1.0 candidate substrate) is **P1 — structured claims with
`evidence_turns`**, deterministically rendered to markdown. The LLM
never writes markdown; it emits structured data and a pure renderer
produces the body.

- **Composer schema** (`lore-experiments/005/schemas/claims_v1.json`):
  - `title` — 6–10 words, content-named
  - `summary_lede` — one sentence (≤220 chars), names what the
    session worked on and what changed/landed/was decided
  - `claims[]` — atomic propositions; required fields:
    - `text` — one-sentence claim (10–30 words), self-explaining
    - `kind` — one of: `decision | tried | leaning | open | done | context`
    - `evidence_turns: int[]` — non-empty list of turn indices that
      support the claim (`minItems: 1`, `maxItems: 4`)
- **Deterministic renderer** (`render_claims.py`): groups by `kind`
  in a fixed section order (Decisions / Tried / Leaning / Done /
  Open / Context), one bullet per claim with `[@<turn>, @<turn>]`
  appended verbatim. No inline weaving, no LLM-authored markdown.

### Two axes: `kind` vs. `status` (decided 2026-05-20)

A claim carries two orthogonal labels:
- **`kind`** — what kind of statement this is *at its cited moment*:
  `decision | tried | leaning | open | done | context`. Set at Stage 1.
- **`status`** — whether it survived to session end:
  `active | superseded | tentative`. Set at Stage 3.

`{kind: decision, status: superseded}` is coherent: "decided Haiku at
@47, later superseded by the Sonnet decision at @153." The two never
collapse into each other.

### The `decision` kind — kind-aware verification (decided 2026-05-20)

`decision` is the schema's footgun (005: Mistral over-tags proposals
as decisions; this is a *kind* error that passes ordinary Stage 2
because the quote still supports the text). We keep `decision`
(it is the highest-value signal for a knowledge graph) but harden it:
- Stage 2's semantic judge is **kind-aware**. For `kind=decision` the
  test is stricter: "Does the quote show a *ratified choice* (X
  conclusively picked over Y), not a proposal or a leaning?"
- A failed `decision` may be **demoted** `decision`→`leaning` as a
  repair outcome — a controlled exception to the no-rewrite rule.
  Rationale: demotion lowers a claim's *certainty* to match the
  evidence (honest), unlike text-weakening which hides *content*.
  The allowed repair ladder is certainty-lowering only.

### What the experiments establish about this substrate

- **Structural grounding works.** 005-P1 achieved 0/88 `from_example`
  and 2/88 (2.3%) `unsupported` claims across both local models —
  on the harder discussion-shape input.
- **Local-judge is dead.** 006a closed the door at 0%
  `contradicted`-recall, even with `reasoning_effort=high`. Locals
  cannot critique their own narration.
- **Ship lever for local models: `reasoning_effort=high` at narrate
  time.** 007 baseline at GPT-OSS-120B reasoning=high hit 0.89 mean,
  2 contradicted, 0 unsupported. Every probe regressed against it.
- **The `decision` kind is the schema's weak spot.** Mistral over-
  applies it to proposed-but-not-ratified items. Mitigation pending
  in v1.0 (drop / tighten / move to a separate field).

### What "trustworthy" means here

A claim is **structurally grounded** when its `evidence_turns` resolve
to existing turns in a stored transcript. The current bar is
**existence-only** — the validator can prove "these turn indices
exist," not "these turns *support* the claim text."

**Four candidate bars** (in order of strength + cost):
- **(a) Existence-only** — turn indices resolve.
- **(b) Verbatim-quote** — schema adds `quote`; substring check.
- **(c) Fuzzy-quote** — quote near-match (n-gram / Levenshtein).
- **(d) Supports-by-judge** — second LLM call decides semantic support.

**(d) splits into two sub-bars:**
- **(d-batch)** — atomize-and-judge whole narrative at once. What
  006a tested. Locals: 0% contradicted-recall. Dead.
- **(d-narrow)** — per-claim binary: "Does turn N support claim X
  given quote Q?" *Not yet tested.* Hypothesis: locals can do narrow
  yes/no even when they fail batch atomization. Would unlock (d) for
  local-first deployment.

### The retraction-over-circles problem (separate from the trust bar)

In a long session, the user and assistant circle back, revise, retract.
A claim like "we picked Haiku" may have a quotable span at turn @47
("leaning Haiku") that *correctly* validates under bars (a), (b), (c),
and even (d-narrow when the judge sees only turn @47) — but at turn
@153 the position was reversed ("scratch that, Sonnet").

007 tested four interventions on this (retraction_field, backwards,
two_pass, sliding) and none beat the `reasoning_effort=high` baseline
— **but that verdict is narrower than it sounds** (reviewed
2026-05-20):
- All four probes addressed retraction at **generation time**. None
  tested a **post-hoc resolution pass over already-grounded claims**
  (= our Stage 3, option B). Our architecture is *untested*, not
  *disproven*.
- The input was retraction-**sparse** (~1–2 genuine reversals in 180
  turns). A detector has almost nothing to catch; null result is
  uninformative.
- The regression was Mistral-driven (no reasoning toggle) at n=1 per
  probe with >40-pt iteration variance. GPT-OSS-120B reasoning=high
  stayed at 0–2 contradicted across *every* probe.
- The judge (`judge_v1`) had **no explicit retraction rubric**; it
  scored support/contradiction generically.

Design implication: a session note's *meaning* changes depending on
whether claims represent moments-in-time (cheap; retraction-tolerant)
or end-state positions (requires retraction handling). Our pipeline
takes the moment-in-time stance for claims (each independently
grounded) and pushes end-state resolution into Stage 3.

**Experiment 009 must fix 007's confounds:** retraction-dense input,
GPT-OSS reasoning=high, per-model reporting, n≥3, explicit retraction
rubric in the judge.

## Note structure — single grounded region (decided 2026-05-20)

v3 collapses PRD #92's two-region (reload-safe + human-only) split to
a **single grounded region**. The whole note is retrieval-safe by
construction:
- **Title** — model-authored navigational label (ungrounded by design;
  it's a label, not a claim).
- **Lede** — grounded (`lede_claims`, Stage-2 verified).
- **Body** — grounded (`claims`, Stage-2 verified).
- **Frontmatter** — mechanical/derived (files, projects, dates), not
  LLM claims.

Rationale: the human-only region existed to quarantine ungrounded
model prose. The new pipeline doesn't *produce* ungrounded prose, so
there is nothing to quarantine. A "give me flowing prose" instinct is
served by the grounded resolved lede, not by an ungrounded narrative
region (which would reintroduce the `18-1526` human-misleading
failure mode the trim exists to kill). User scratch narrative lives in
the kept **journal** side-chain, not in the session note.

## Citation unit

The unit that carries a citation is the **claim** (one entry in the
`claims[]` array). Not the bullet, not the sentence, not the section.
The rendered bullet inherits the claim's `evidence_turns`. The
deterministic renderer is the bridge between the structured form
(grounded) and the markdown form (read by humans / retrieved by
agents).

## v1.0 pipeline — three stages

**Stage 1 — Generation (1 LLM call).**
Curator emits P1-extended schema:
- `title` — 6–10 words, content-named (as P1).
- `lede_claims[]` — 1–3 entries, same shape as a body claim:
  `{text, kind, evidence_turns, quote}`. Renderer concatenates
  these into the note's top-of-page lede paragraph. They go
  through Stage 2 verification just like body claims.
- `claims[]` — body propositions. Each: `{text, kind,
  evidence_turns, quote}`. The `quote` field is a verbatim substring
  from at least one of the cited turns. Schema requires non-empty
  `quote` and non-empty `evidence_turns`.

The most-read part of the note (the lede) is subject to the same
per-claim verification as the body. No section is exempt.

**Stage 2 — Per-claim verification, two passes (decided 2026-05-20).**

Two checks define pass/fail for any claim:
- **Mechanical (cheap, runs first):** `claim.quote in turn.content`
  for at least one cited turn. No LLM call needed.
- **Semantic (LLM judge, narrow binary):** "Given quote Q from
  turn N and claim C, does Q support C?"

*Pass 1 — parallel triage.* Verify all candidate claims independently
and in parallel. Partition into `verified` and `failed`. The bulk
clears here.

*Pass 2 — sequential repair (accumulating context).* Process `failed`
claims one at a time, **chronologically by first evidence turn**, so
the verified set grows in the session's natural order. Each retry
call sees:
- the failed claim + the **exact** failure reason + the cited turn's
  actual text (turns "guess a quote" into "copy a span")
- the **current `verified` set** (grows as repairs succeed)
Allowed outcomes: **re-cite** (pick a supporting turn+quote) /
**drop-as-redundant** (already covered by a verified claim) /
**drop-as-contradictory** (conflicts with a verified claim) /
**drop-as-unsupported**. No claim-text weakening.
On success → claim joins `verified` (visible to the next repair).
On retry-cap exhaustion (default 2 per claim, tunable) → drop.

Effect: Pass 2 begins doing Stage 3's coherence work — contradictions
against the trusted core surface during repair, not only at synthesis.
Costs: Pass 2 is serial (slow if failures are heavy); per-call context
grows; outcome is order-dependent (mitigated by chronological order).

**Terminal trust outcome (a)+(e) — three tiers:**
- `trusted`   — all lede claims pass AND ≥80% body claims survive.
- `degraded`  — all lede claims pass AND ≥50% body claims survive.
- `failed`    — a lede claim was dropped, OR survival <50%. Note is
  written to disk for human review but **excluded from retrieval**.
Thresholds are placeholders; pilot data tunes them.

Open sub-decisions (defaults proposed):
- Retry granularity: per-claim calls (consistent with the narrow-call
  architecture) vs. one batched corrective call for all failures.
  *Default: per-claim.*
- What retry may change: citation-only (pick a better turn+quote) vs.
  also allow claim-text refinement. Allowing text-rewrite risks the
  model weakening claims to pass the check. *Default: citation-only;
  text-rewrite deferred.*
- Retry cap shape: per-claim cap vs. per-note global budget.
  *Default: per-claim cap = 2.*

Surviving claims become inputs to Stage 3.

**Stage 3 — Cross-claim resolution + resolved lede (1 LLM call).**
Input: the verified-claim set (no transcript re-read). Outputs:
- A **resolved lede** reflecting the verified end-state.
- A **claim status map**: per claim, `{status: active | superseded
  | tentative, superseded_by: <claim_id> | null}`.
Renderer uses these to hide / move / strike-through superseded claims
and to put the resolved lede at the top.

**Render — deterministic.** No LLM call. `render_claims.py`-shaped:
group active claims by `kind`, append `[@N]` citations verbatim,
render superseded claims to a "Reversed / superseded" section (or
omit, depending on note-shape design).

Total cost: 1 + N + 1 ≈ 7–17 calls per session for a typical 5–15
candidate-claim note. Local-friendly if narrow judges calibrate
(unmeasured — needs experiments 008 + 009).

## When the pipeline runs — two-phase, deferred (decided 2026-05-20)

- **At session boundary (SessionEnd / PreCompact):** write the
  existing live **stub** immediately (crash-safe, instant). Stub is
  filtered from retrieval.
- **Detached afterward:** the full 3-stage pipeline runs as a
  background job and flushes the stub → verified note when done. The
  user never waits; the note "ripens" over the following minutes and
  is ready by next SessionStart.
- **Self-healing:** on SessionStart, if a pending stub older than X
  has no verified note, resume its pipeline (rides the existing
  heartbeat/SessionStart machinery). Survives laptop sleep / closed
  sessions.

This preserves "capture is automatic, never ask" and keeps latency
invisible (nothing waits on the note in the session that produced it).

## Existing vault content — operational cleanup, not tooling (decided 2026-05-20)

v1.0 does **not** build a `trust: legacy` quarantine system (no
retrieval filter for legacy, no in-body banner, no flag migration).
The maintainer removes untrusted content at the source:
- **v2 session notes:** mostly already removed manually. Worth-keeping
  sessions get **re-derived** by re-running the v3 pipeline over the
  surviving transcript mirror — but **only once the pipeline is
  earned** (post-pilot), and only where the transcript still exists.
  This is the eventual answer to "what about old notes," gated on
  proof, not assumed.
- **Concepts (ccat wiki):** `git revert` the wiki repo's `concepts/`
  to the commit just before Curator B began writing into it. Accepts
  loss of any hand-written concepts since lore started (judged
  acceptable for ccat).
- **Concepts (private wiki):** cut back manually (surgical — private
  has hand-written concepts worth keeping).

Note: `trust: trusted | degraded | failed` (the three-tier outcome
for *newly produced* v3 notes) is unaffected — it is a different
mechanism from legacy quarantine, which is dropped.

## The earn-gate — pre-committed thresholds (decided 2026-05-20)

Both the dormant-file deletion (ordering step 2) and old-note
re-derivation gate on "pilot green." Green = all hard gates pass.
Pre-committing the numbers is what keeps the verdict honest (per
006a/007 methodology).

| Metric | Threshold | Gate type |
|---|---|---|
| **008** narrow-judge contradicted-recall vs Opus | **≥70%** | **HARD** — no judge = hollow Stage 2 |
| **009** supersession-recall, retraction-dense input | **≥60%** | **DEGRADE** — miss → ship time-anchored claims, defer Stage 3 cross-claim resolution to 1.x |
| **Regression** end-to-end published-claim factuality (Opus audit) | **≥0.85 mean** | **HARD** — quote requirement must not tank factuality |
| **Survival liveness** — ≥1 claim rejected per N pilot sessions | rejection observed | **HARD** — verification must demonstrably bite (replaces a survival-rate ceiling, which would wrongly penalize a genuinely good model) |
| **Survival floor** — candidate claims passing verification | **≥40%** | **HARD** — below 40% notes are too thin |
| **Pilot latency** — deferred pipeline completion | **<10 min/session p90** | **HARD** — notes must ripen before next session |

**Halt-vs-degrade:** 008 is load-bearing (the whole verify
architecture is hollow without a working narrow judge) → hard gate.
009 is a nice-to-have (time-anchored claims are honest without
supersession resolution) → a miss degrades scope rather than blocking
the release.

**Pilot scope:** both `private` (discussion-heavy) AND `ccat`
(work-grind). The 003/004 input-class finding warns that
single-class piloting misleads — factuality and selection effects
reverse across input class.

## Experiments still needed before locking v1.0

- **008 — narrow per-claim verifier calibration.** Does a local model
  doing the bar-(b)+bar-(d-narrow) per-claim check calibrate against
  Opus? 006a's failure was on the *batch* version; per-claim has not
  been measured. Reuse 005's claims + 002's judge as ground truth.
- **009 — cross-claim contradiction detection.** Does a local model
  given verified claim-text-only input correctly identify supersession
  ("leaning Haiku [@47]" superseded by "decided Sonnet [@153]")? Must
  fix 007's confounds: retraction-dense input, GPT-OSS reasoning=high,
  per-model reporting, n≥3, explicit retraction rubric in the judge.
- **regression test** — does forcing a `quote` field at Stage 1 hurt
  the 005-P1 / 007-baseline factuality numbers (0.89 mean)?
