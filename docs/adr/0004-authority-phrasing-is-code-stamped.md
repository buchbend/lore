# ADR 0004: Authority phrasing is code-stamped from verifiable refs

- Status: Accepted
- Date: 2026-07-12
- Context: epic [#282](https://github.com/buchbend/lore/issues/282),
  PRD [0008](../prd/0008-typed-fact-session-notes.md), sub-issue
  [#286](https://github.com/buchbend/lore/issues/286)

## Context

Session notes are read by later sessions. A machine-extracted line like
"Decision: the ledger is the source of truth" is pulled into a future context
window and read as settled — even when the extraction over-read a musing. The
future session restates it, the curator abstracts it into a decision note, and a
claim that was never made acquires sources. Circular context poisoning: the
system's own output becomes its evidence.

The genre disclaimer at the top of a note is the only defense today, and under
context pressure a wrapper loses to line-level phrasing. What a reader — human
or model — ingests is the sentence, not the header above it.

ADR 0003 made the note body a deterministic render of the ledger, which fixes
*layout, ordering and suppression* but not this: a model-authored sentence
rendered deterministically is still a model-authored sentence. The remaining
question is who writes the words that carry epistemic weight.

Typed-fact extraction gives each fact structured `refs` (`pr` / `commit` /
`file` / `tag` / `issue`) — pointers to things that exist outside the note and
can be checked without asking a model.

## Decision

**Code, never the model, authors the phrasing that carries authority.** The
extraction model contributes a fact's `text` and its `why`. Every word around
them — what the line claims about the world, and how strongly — is chosen by a
template the renderer owns, keyed on `(kind, verification)`.

`verification` is computed, not asserted (`lore_core.ref_verify`):

- **Commits, tags and files are verified exactly** — against the session's own
  deterministic frontmatter facts (what capture recorded) and then against local
  git and the filesystem.
- **PRs and issues are verified best-effort** through `gh`.
- A fact's verdict is the weakest of its refs; a fact with no refs is its own
  fourth column.

Three verdicts, and the asymmetry between them is the decision:

- `verified` — a check ran and succeeded. The line states itself plainly and
  shows its pointer with a check mark.
- `missing` — a check ran and came back empty. The line is **demoted**:
  "Claimed in session, ref not found: …". A hallucinated ref costs authority
  rather than buying it.
- `unchecked` — nothing could be checked (offline, no `gh`, no repo, a value
  that is not a sha or a number). The pointer is stamped `(unchecked)`.

**Positive evidence only.** Verification promotes on success and on nothing
else. A `gh` call that fails means GitHub was unreachable, not that the PR is
fake, so it yields `unchecked` — never `verified`, never `missing`. A ref the
verifier said nothing about at all renders `(unchecked)`. Offline operation
therefore degrades a note's phrasing and never fails its render.

**A `decision` with no refs is not a decision.** It renders under **Open** as
"Agreed in discussion, recorded nowhere: `<text>` — `<why>`". The most
poison-prone claim in the system becomes, by construction, a line that
advertises its own weakness and invites checking.

No LLM runs in the verification or the stamping path.

## Consequences / Trade-offs

- **Positive — over-claiming is capped by code, not by prompt obedience.** A
  weak local model that labels a musing a `decision` produces a hedged line, not
  an authoritative one. The failure mode of the cheapest tier is bounded.
- **Positive — trust is a checkable property.** Every authoritative line carries
  the pointer that earned it, so a reader can verify it in one command instead
  of trusting the note's voice.
- **Positive — the incentive is aligned.** The only way for a fact to read
  confidently is to point at something real, which is also the only way it is
  useful a month later.
- **Negative — notes read more hedged than before**, including for facts that
  are true but unrecorded. This is the intended trade: a true claim with no
  artifact behind it is exactly the claim a later session should not ingest as
  settled.
- **Negative — a note's phrasing is not stable over time.** A ref that verified
  at close may not verify on a re-render (a branch deleted, a repo moved). The
  ledger is unchanged by this; only the reading moves, which ADR 0003 already
  permits.
- **Negative — verification costs subprocesses at close.** Bounded by deduping
  refs and by the fact that it runs once per session, at the ending.
- **Constraint on every future note feature.** Anything that adds a rendered
  line must state where its authority comes from. A feature that lets a model
  phrase its own certainty reopens the hole this ADR closes.

## Alternatives considered

- **Prompt the model to hedge appropriately.** Rejected: it is the model's own
  judgement of its own certainty, which is exactly what fails. It also cannot be
  tested — there is no assertion to write against "sounds suitably tentative".
- **Verify with an LLM (check the fact against its quote).** Rejected here: it
  puts a model back in the authority path, and no LLM re-reads LLM prose as
  source material in this pipeline (the Stille-Post rule). The ledger keeps the
  quote beside the claim, so a fidelity checker remains possible later as a
  separate, clearly-labelled tier.
- **Drop unverifiable facts from the render.** Rejected: silence is worse than a
  hedge. An unbacked observation is often the most valuable thing in a session,
  and the note's job is to say what it is, not to hide it.
- **Treat a failed `gh` lookup as evidence of absence.** Rejected: it makes an
  offline laptop rewrite history, demoting every real PR in the note.
