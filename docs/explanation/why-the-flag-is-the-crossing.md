# Why one flag, and not a session note

**Audience:** anyone who sees an unreviewed block appear in a topic note they
own, or who wonders why Lore asks an agent to file one fact at a time
instead of writing up the session.

---

## The measurement that ended session notes

Lore composed a note for every working session. The 2026-08 telemetry sweep
counted what those notes were worth:

- Readers pulled a session note 17 times per month across 307 sessions.
- Every pulled note was at most 7 days old. The archival reader never
  appeared.
- The pipeline cost about 46 LLM calls per day, and eight days of runs
  produced 60 errors and 91 force-flushes.
- The retrieval tools that find context in the repo out-used note retrieval
  about 4.5 to one.
- One session produced a 1,636-line note holding six to eight facts worth
  keeping.

Two earlier reworks (PRD 0002, PRD 0008) each changed *how* the note was
composed. The noise complaint outlived both. The third reading is that the
mission was wrong, not the mechanism: a per-session write-up is the wrong
unit. It bundles six good facts with a thousand lines of process narration,
and a reader has to find the six.

[ADR 0007](../adr/0007-session-notes-retired-flags-are-the-crossing.md)
records the decision that follows.

## Three layers, one crossing

| Layer | Holds | Who reads it |
|---|---|---|
| Team | Repo artifacts (ADRs, PRDs, issues, PRs, docs, code) and wiki topic notes | Everyone |
| Personal | Raw transcripts and the transcript ledger, machine-local | The owner |
| Crossing | The flag | Everyone, once it is filed |

Most of what a session produces already belongs on the team layer, and the
workflow chain already puts it there — a decision becomes an ADR, a plan
becomes a PRD, work becomes a PR. Nothing needs a second, prose copy of that
in a note.

What is left over is the interesting part: a trap avoided, a dead end and
the reason it was dead, reasoning nobody wrote down, a gap between the docs
and the code. Those facts have no artifact. They are what the flag carries.

The personal layer holds the raw material. Transcripts and the ledger stay on
one machine and are gitignored, and Lore ships no sync for them
([ADR 0009](../adr/0009-privacy-boundary-is-locality.md)). That boundary is
locality, not access control — there is no server to configure and no key to
manage, and the raw record structurally cannot reach the team surface. The
cost is stated rather than hidden: a lost machine loses that machine's raw
record, and same-person multi-machine use is a documented limitation.

## Why the flag is small and stamped

A flag carries one fact: a lead sentence, a short body, and an origin line
that code writes in full. There is no kind taxonomy and no composition step.

**One fact, because a fact is the retrievable unit.** A note bundles facts
that have nothing to do with each other except a shared afternoon, so it
lands in one place while its contents belong in five. A flag routes to the
topic note the fact is actually about, which is where a reader searching
those words will look.

**Filed at the moment, because worth is knowable then.** A session note had
to be composed backward from the whole transcript, and a model had to guess
which lines a reader would want. The agent that just hit the trap already
knows it hit one.

**Stamped, because the model does not get to author authority.** Refs are
verified against the repo at write time, and the phrasing follows from what
the check returned
([ADR 0004](../adr/0004-authority-phrasing-is-code-stamped.md)). A hallucinated
PR number costs authority instead of buying it. A write carrying no
transcript pointer and no ref is refused outright: a fact with nothing behind
it has no business on a shared surface.

**A human's flag is not stamped.** The phrasing rules exist to constrain what
a model may claim, not what a person writes, so a flag you file from your own
shell keeps your words and lands already reviewed.

## Why it lands immediately, marked unreviewed

The obvious design is a queue: hold the agent's flag under `.lore/`, and
write it into the wiki when a human approves it. Lore rejected that
([ADR 0008](../adr/0008-flag-lands-marked-unreviewed.md)).

A queue makes the gem invisible for exactly as long as the owner is busy,
which is exactly when it matters most. Queue rot is silent — an unwalked
queue looks identical to an empty one, and the facts inside it never reach
the teammate who needed them yesterday. Landing the flag immediately means a
teammate sees it fresh, labelled as unreviewed, and can judge it themselves.

What the marker buys instead is honesty about provenance. LLM-authored text
on a human surface is a poisoning surface: a machine-written line that a
later session reads as settled, restates, and thereby makes look
better-attested than it is. The `unreviewed` token at the end of the origin
line says, in the reader's own line of sight, that no human has confirmed
this. Accept is the only verdict that removes it.

The trade is stated in the ADR: unreviewed text is visible until someone
reviews it, and review latency replaces under-flagging as the failure that
hides. Both are measured — see
[measure flag quality](../how-to/measure-flag-quality.md).

## Why review is a pull, not a prompt

Nothing about a flag interrupts a session. The agent files and moves on; the
SessionStart banner shows a count and never the content; the review runs when
you type `lore flag review`.

This is the same rule the banner has always followed — deep context is a
pull, never a push. Pushing a review prompt into a session would put a
teammate's unreviewed text into your context window at a moment you did not
choose, which is precisely the poisoning path the marker exists to close. A
count is safe to push because a count says nothing.

The walk itself is snapshotted before it starts, so a retarget that moves a
flag into a note you have already passed cannot show it to you twice. Skip is
the default verdict, so pressing Enter through the walk is a no-op rather
than a bulk accept.

## Why pending state is derived, never stored

The marker in the note *is* the pending state. `lore flag list` finds pending
flags by scanning notes for the token; no queue file records them.

A queue store would be a second copy of a fact the wiki already holds, and
two copies drift. Someone edits a note by hand, someone reverts a commit,
someone resolves a merge conflict — and the store now disagrees with the
wiki about what is pending, with no way to tell which is right. Scanning is
slower and cannot drift. `lore status` is honest about the seam this leaves:
the pending count comes from the scan while the accept and decline counts
replay spine events, so the two are not expected to sum.

## What has not happened yet

The compose pipeline still runs. Capture still writes a session note at every
session boundary, and the sensitivity gate still covers note text as well as
flag text. The flag shipped *beside* the old pipeline, not in place of it.

That is deliberate. The rollout is additive-first, and the teardown is gated
on evidence: a flag rate measured against known gems, a review latency, an
accept rate. Under-flagging is the failure mode that leaves no trace — a fact
the agent should have flagged and didn't produces no error, no alert, and no
gap anyone would notice. Retiring the pipeline before that is measured would
trade a surface nobody reads for one nobody can check.

So the honest statement of today's system is: the flag is the deliberate
crossing, and the note pipeline is the one still running underneath it. The
flag becomes the *only* crossing when the teardown lands.

## See also

- [ADR 0007](../adr/0007-session-notes-retired-flags-are-the-crossing.md) —
  the three layers, and what the teardown retires.
- [ADR 0008](../adr/0008-flag-lands-marked-unreviewed.md) — the landing
  decision, the rejected queue, and the marker.
- [ADR 0009](../adr/0009-privacy-boundary-is-locality.md) — why transcripts
  never leave the machine.
- [PRD 0011](../prd/0011-session-note-retirement-flag-architecture.md) — the
  full problem statement and the per-decision rationale.
- [File a flag, and review the flags agents filed](../how-to/file-and-review-flags.md)
  — the commands.
- [Why a session note is written only at the end](why-notes-are-written-at-session-end.md)
  — the pipeline that is still running, and the reasoning it was built on.
