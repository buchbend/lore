---
title: "Typed-fact session notes: end-mode extraction, deterministic rendering, epistemic stamping"
status: draft
epic: https://github.com/buchbend/lore/issues/282
repos:
  - buchbend/lore
---

# PRD 0008: Typed-fact session notes — end-mode extraction, deterministic rendering, epistemic stamping

> Source of truth for this epic. Tracker: [epic issue](https://github.com/buchbend/lore/issues/282).
> The epic links here; this file is not embedded in the issue body.

## Problem

Session notes record the *working* instead of the *work*. Three failure classes,
observed on real notes:

**Process narration drowns substance.** A long orchestration session yields ~27
prose blocks ("status comment posted", "worktree prepared and teammate
dispatched", "PR opened") of which perhaps five would matter to a reader a month
later. The cause is architectural, not prompt quality: chapters are composed
*forward* at flush time and are immutable, but which facts matter is only
knowable *backward*, at session end. At flush time "PR #524 opened" genuinely is
the substance of its slice; only the ending reveals that twenty such blocks
collapse into "all five features merged". No prompt given to the per-flush model
can fix this — it cannot know the future.

**Interim states persist as stale facts.** "Features #514–#516 remain in
progress" is false by the end of the note that contains it. Append-only-until-
close guarantees this class of noise for any long session.

**LLM-authored phrasing acquires false authority.** Machine-extracted "decision"
lines get pulled into future sessions' context and read as settled, even when
the extraction over-claimed a musing. Future sessions restate them, the daily
curator abstracts them into decision notes, and a fabricated or imprecise claim
gains sources — circular context poisoning. The genre disclaimer at the top of
the file is the only defense, and under context pressure wrappers lose to
line-level phrasing.

A fourth, structural issue: flush boundaries (buffer cap-trip, pre-compact,
session end) are lifecycle accidents that beat against the work's own rhythm, so
extraction windows routinely cut topics in half.

## Solution

From the user's perspective, after this epic:

- **Nothing is written while the session runs.** At session end (SessionEnd
  hook, reaper, or startup sweep — the existing triggers), the whole session is
  processed in one pipeline and the note appears once, complete.
- **The note is short, scannable, and final-state.** A headline, then sections
  **Done / Decisions recorded / Findings / Open** — one line per fact, anchors
  at line end. Interim progress that was superseded by an ending is absent from
  the note (it remains in the ledger below). "What exactly was done" is the Done
  list, and each line is a breadcrumb to the PR/commit that authoritatively
  records it.
- **Trust is a checkable property, not a voice convention.** Every line either
  carries a pointer that code verified against git/GitHub (rendered plainly,
  with its ref), or is explicitly stamped as session-level talk ("Agreed in
  discussion, recorded nowhere: … — why"). A hallucinated ref cannot acquire
  authoritative phrasing: verification fails and the line demotes to the hedged
  template. Nothing in a note asserts world-state on its own authority.
- **The grounding tier stays.** Below the rendered note, the append-only ledger
  keeps every extracted fact with its verbatim, code-attached quote; `@N`
  anchors point into the archived transcript. Drill-down chain: note → ledger →
  transcript.

## Implementation decisions

**Pipeline (all LLM work at session end).** The buffer/slice machinery, session-
end triggers, publish gate, and archive flow are unchanged. What changes is what
a "flush" does: mid-session events only bookkeep; the close path runs
segment → extract×N → render.

**Segmentation (LLM A — indices only).** A cheap model reads a *collapsed* view
of the replayed session (thinking dropped, tool results folded to line counts;
the noteworthy filter's view shape) and returns proposed chunk boundaries as
turn indices. Its entire output surface is a list of integers — it makes no
claims, so its errors degrade to suboptimal windows, never false facts.
Deterministic lints: monotone, in-range, full coverage, chunk sizes inside a
configured band (merge tiny, split huge at fallback points). Oversized sessions
are segmented in windows with deterministic stitching. On failure: fixed-size
windows. This aligns extraction windows with the work's own beats instead of
buffer-cap accidents.

**Typed-fact extraction (LLM B — the only generative step).** One first-
generation call per logical chunk, reading raw transcript turns. Each fact:
`kind` (`progress` | `done` | `decision` | `finding` | `open`), optional
`thread` key, structured `refs` (`pr` / `commit` / `file` / `tag` / `issue`),
short `text`, `why` (mandatory for decisions), one `@turn` anchor. Bounded
corrective retries mirror the existing compose contract: anchor-in-chunk lint,
kind-enum lint, decision-without-why lint. Verbatim quotes are code-attached
from the anchor turn — the model never writes them. Prompt rules that this PRD
fixes: the **terminal-state rule** (commits, PRs, verified-green states are
`done`; edits en route are `progress`), the **month test** (a fact must change
what a colleague does or believes a month later), the **supervision clause**
(when the session supervises other agents, the subject is the deliverable, not
the choreography), and the exemplar/quoted-material rules carried over from
PRD 0002. Extraction calls run sequentially; call *n* sees the compact fact
table from calls 1..n-1 for thread-key continuity and dedup only — facts must
come from the transcript chunk, never from the table. No LLM ever re-reads LLM
prose as source material (Stille-Post rule).

**Headline (LLM B′ — the one bounded exception).** After the final chunk, one
call writes a single headline sentence from the fact table. It is the only
cross-chunk synthesis; a deterministic lint rejects any headline naming a
thread or ref absent from the table, and the publish gate scans it like any
text.

**Deterministic renderer (no LLM).** Pure function from the typed ledger to the
note body, written once at close: disclaimer, headline, sections in fixed order,
items anchor-sorted, empty sections dropped. One suppression rule: a `progress`
fact is omitted when its thread has a later terminal fact; suppressed facts
remain in the ledger. Failed-chapter markers render as one-line coverage gaps.
Same ledger in, byte-identical note out.
**ADR-worthy:** the note body becomes a *derived render* of the append-only
ledger (extends ADR 0001's immutability carve-out; recorded as an ADR in the
renderer slice).

**Ref verification + epistemic stamping (no LLM).** At render time, refs are
verified: commits/tags/files exactly, against the session's deterministic
frontmatter facts and local git; PRs/issues best-effort via `gh`, stamped
"unchecked" when unreachable — never silently promoted (positive-evidence-only).
Phrasing templates are owned by code, keyed on (kind, verification):
artifact-backed → plain statement + pointer; unverifiable ref → explicit
"unchecked"; no refs → session-attributed stative phrasing; a ref-less
`decision` routes into **Open** as "Agreed in discussion, recorded nowhere:
<text> — <why>" — the most poison-prone claim in the system becomes, by
construction, a line advertising its own weakness. The extraction model never
authors the words that carry epistemic weight; over-claiming by a weak model is
capped by code, not by prompt obedience.
**ADR-worthy:** authority phrasing is code-stamped from verifiable refs, never
model-authored (recorded as an ADR in the stamping slice).

**Trust boundary, stated honestly.** Determinism does not make content true —
it makes false authority unreachable and every claim cheap to check. Residual
LLM trust surface: fidelity of a fact's text to its anchored turn (mitigated by
the verbatim quote beside it in the ledger and by refs bounding `done`/
`decision` damage), kind misclassification (worst case: a hedged
"recorded nowhere" line that invites checking), omission (irreducible; the
ledger and archived transcript remain the recovery path), and wrong thread keys
(bounded to rendering noise; the ledger keeps everything).

**Model tiers.** Segmentation and extraction stay on the cheap/local tier —
"classify a fact and write one short sentence" and "emit indices" are squarely
inside a small model's competence; layout, ordering, suppression, and phrasing
move into code where they are tuned with tests instead of prompt archaeology.
All prompts stay small enough for the local-backend capacity ceiling.

## Testing decisions

External behavior over internals, on the established stub-LLM replay harness
(`test_chapter_compose.py`, `test_chapter_flush.py` — every model call faked,
no LLM-as-judge):

- **Segmentation:** boundary lints (non-monotone / out-of-range → one corrective
  retry → fixed-window fallback); size-band merge/split; windowed stitching;
  model failure never blocks the pipeline.
- **Extraction:** replay fixtures produce typed facts; each lint earns exactly
  one corrective retry; typed metadata round-trips through ledger markers;
  pre-existing untyped notes still parse; an orchestration-style fixture yields
  `progress` en route and `done` only at terminal states; headline lint rejects
  out-of-table refs.
- **Renderer:** golden-file renders (section order, anchor sort, suppression,
  coverage gaps); byte-determinism; re-render never mutates the ledger; close /
  reopen semantics preserved.
- **Verification/stamping:** template-matrix fixtures (verified / unchecked /
  no-ref × kinds) golden-tested; nonexistent refs demote; offline never fails a
  render and never awards a check mark; no network in unit tests.

## Out of scope

- **Re-rendering existing notes** (fix-forward, as PRD 0002 did).
- **The noteworthy gate** — unchanged, still in front of everything.
- **Curator B / briefing / search changes** — they consume note bodies as text
  and benefit passively; no structural coupling is added.
- **Mid-session draft notes** — end-mode deliberately shows nothing until
  close; if a live-preview need emerges it is a separate feature.
- **A general claim-fidelity verifier** (LLM checking each fact against its
  quote) — the quote-beside-claim ledger layout keeps this possible later.
