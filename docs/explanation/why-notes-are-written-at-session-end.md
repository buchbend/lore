# Why a session note is written only at the end

**Audience:** anyone who notices that nothing appears in the vault while a
session runs, or who wonders why a note's lines read so carefully — "Agreed
in discussion, recorded nowhere: …", "commit 3f9a2c1 ✓", "(unchecked)" —
instead of plainly stating what happened.

Both come from one decision: **the model never writes the words that carry
authority, and it writes nothing at all until the session is over.** This page
explains what that buys and what it costs.

---

## Composing forward recorded the working, not the work

Notes used to be written *while the session ran*. Each flush turned the newest
slice of transcript into a chapter of prose and appended it; the chapter was
immutable from then on. The note grew as the session grew.

That structure produced three defects no amount of prompt tuning could reach,
because they are properties of *when* the writing happened, not of how well the
model wrote.

**Which facts matter is only knowable backward.** At the moment a slice is
composed, "PR #524 opened" genuinely is the substance of that slice. Only the
ending reveals that twenty such blocks collapse into a single line: "all five
features merged". A model composing forward cannot know the future, so a long
orchestration session yielded dozens of blocks — status comment posted,
worktree prepared, teammate dispatched — of which perhaps five would matter to
a reader a month later. Process narration drowned the substance.

**Interim states froze into the record.** "Features #514–#516 remain in
progress" is false by the end of the note that contains it. Append-only-until-
close guaranteed that class of noise for any session long enough to change its
own mind.

**And the worst one: model prose became the next session's evidence.** A
machine-extracted line like "Decision: the ledger is the source of truth" gets
pulled into a later session's context window and read as settled — even when
the extraction over-read a musing into a decision. That session restates it. A
curator abstracts it into a decision note. A claim nobody ever made acquires
sources. This is circular context poisoning: the system's own output becomes
its own evidence, and each pass makes the claim look better attested than the
last.

The genre disclaimer at the top of a note was the only defense against that
last one, and under context pressure a wrapper loses to line-level phrasing.
What a reader ingests — human or model — is the sentence, not the header above
it.

---

## The fix: read the session backward, and let code do the claiming

Two changes, and they are inseparable.

### All LLM work happens once, at the ending

Nothing is written to the note while a session runs. Mid-session events — the
buffer tripping its cap, a pre-compact — only bookkeep: the buffer keeps
accumulating, no chapter is appended, no model is called. At session end the
whole session is read in one pipeline (segmented into logical chunks, each
chunk extracted into typed facts) and the note appears once, complete.

Because the render sees the whole session at once, the ending can retire its
own middle. A `progress` fact whose thread later reached a terminal state is
simply not in the reading. It is still in the ledger below, with its quote and
its anchor, so nothing is lost — but the note no longer lies about how it ends.

### Code, not the model, authors epistemic weight

The extraction model contributes two things: a fact's `text` and its `why`.
Every word around them — what the line claims about the world, and how strongly
— is chosen by a phrasing template the renderer owns, keyed on `(kind,
verification)`.

`verification` is *computed*, never asserted. Commits, tags and files are
checked against the session's own captured facts and then against local git and
the filesystem. Pull requests and issues are checked best-effort through `gh`.
The verdict is one of three, and the asymmetry between them is the whole point:

- **verified** — a check ran and succeeded. The line states itself plainly and
  shows its pointer.
- **missing** — a check ran and came back empty. The line is *demoted*:
  "Claimed in session, ref not found: …". A hallucinated ref costs authority
  rather than buying it.
- **unchecked** — nothing could be checked (offline, no `gh`, no repository).
  The pointer is stamped `(unchecked)`.

The direction of the incentive flips. Under forward composition, a model that
invented a plausible PR number got a confident-sounding line for free. Now the
only way for a fact to read confidently is to point at something that actually
exists — which is also the only way it is useful to anyone a month later.

**Positive evidence only.** Verification promotes on success and on nothing
else. A `gh` call that fails means GitHub was unreachable, not that the PR is
fake, so it yields *unchecked* — never *verified*, and never *missing*.
Otherwise an offline laptop would rewrite history, demoting every real ref in
the note. Absence of a failure is never evidence.

**A decision no artifact records is not a decision.** It renders under **Open**
as "Agreed in discussion, recorded nowhere: `<text>` — `<why>`". The most
poison-prone claim in the system becomes, by construction, a line that
advertises its own weakness and invites checking.

The quote beside each fact in the ledger is code-attached from the anchored
turn — the extraction tool schema has no quote field at all, so the model
cannot author the verbatim evidence for its own claim. And no model in this
pipeline reads model prose as source material: facts come from the transcript,
never from another model's summary of it.

---

## What this does not buy

Determinism does not make content true. It makes false authority *unreachable*
and every claim *cheap to check*. What remains on the model's side of the line:

- **Fidelity** — whether a fact's text is faithful to the turn it was anchored
  to. Mitigated by the verbatim quote sitting beside it in the ledger, and
  bounded by the refs that back a `done` or a `decision`.
- **Misclassification** — a musing labelled a `decision`. Worst case it lands
  under Open as a hedged "recorded nowhere" line that invites checking. That is
  the failure mode the cheapest model tier is *allowed* to have.
- **Omission** — irreducible. The ledger and the archived transcript remain the
  recovery path.

It also costs something real: **notes read more hedged than they used to**,
including for facts that are true but recorded nowhere. That is the intended
trade. A true claim with no artifact behind it is exactly the claim a later
session should not ingest as settled.

---

## See also

- [ADR 0003](../adr/0003-note-body-is-a-derived-render-of-the-ledger.md) — why
  the note body is derived state, thrown away and recomputed from the ledger.
- [ADR 0004](../adr/0004-authority-phrasing-is-code-stamped.md) — the phrasing
  templates, the three verdicts, and the alternatives that were rejected.
- [`session-note-lifecycle.md`](../architecture/session-note-lifecycle.md) —
  the mechanics: buffer, close, segment, extract, render, verify, publish gate.
- [PRD 0008](../prd/0008-typed-fact-session-notes.md) — the full problem
  statement and the per-decision rationale.
