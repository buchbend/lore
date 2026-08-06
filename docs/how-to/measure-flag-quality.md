# Measure flag quality: known-gem baseline and directive flip-probe

**Goal:** catch under-flagging before it erodes trust in the flag
architecture. The flag is the crossing from a private session to the
team wiki, and the only one — a fact an agent should have flagged and
didn't leaves no trace anywhere a human would look. Under-flagging is
measurable only by deliberately checking for it; it does not show up as
an error or any other alert `lore status` already earns.

Background: the flag shape and the "measurement before trust" rule are
recorded in [`brainstorms/lore-session-notes-worth.md`](../../brainstorms/lore-session-notes-worth.md)
("Flag shape" and "S2 under-flagging amnesia").

Both procedures below are **human-run**. Nothing here is a CLI command
or an automated check — you read transcripts, judge outcomes, and record
results yourself. What Lore gives you is the read side: the counters and
the raw event timeline to check your judgment against.

## Read side: what to check the results against

- **`lore status`** — the `flags` section shows, per wiki: flags
  written, withheld, pending, accepted, declined (and retargeted, when
  any exist). Compare this against how many gems you expected the
  session to surface.
- **`lore trace flag`** — every flag-write and flag-review spine event,
  chronologically, each carrying a timestamp and a `flag_id`. Use it to
  find a specific flag's write event and pair it with its verdict.
  `lore trace flag --json` gives the raw records for scripting a report;
  pipe that through `jq`/Python and filter by `wiki` or `ts` yourself —
  the selector itself takes no time-window or wiki filter.
- **Review latency** for one flag is the gap between its `flag-write`
  and `flag-review` timestamps in that output — both events carry the
  same `flag_id`, so pairing them is a lookup, not a derivation. A
  retarget verdict does not count as the review: the flag stays pending
  after a retarget, so the latency that matters is write-to-accept (or
  write-to-decline), skipping over any retargets in between.
- **`pending` and the write/verdict counters come from two different
  sources and are not expected to sum.** `pending` is a live scan of the
  wiki's notes for the unreviewed marker; the other counters replay
  spine events. Hand-accepting a flag by editing the note directly gives
  `written 2 · pending 1 · accepted 0` (the marker scan sees the edit,
  no `flag-review` event exists for it); deleting a flagged note outside
  Lore gives `written 2 · pending 0 · accepted 0 · declined 0` (both
  flags vanish from the scan with no event recording why). Neither
  output is wrong — they are honest about what each source can see.
- **The counters are a rotation-bounded window, not an unbounded total.**
  They read the live spine plus its most recent rotated sibling
  (`retention.cold_days`, default 30 days) — older events are gone once
  the janitor deletes that cold file. A baseline campaign running longer
  than that window will undercount its earlier sessions with no error.
  `accepted` (or `declined`) exceeding `written` is the signature that
  some of a flag's write events have rotated out from under its verdict
  — read it as "older than the window", not as a bug.

## Procedure 1: known-gem baseline replay

Checks whether the agent actually flags the facts a human would judge
worth keeping.

1. **Pick sessions with a known gem.** A gem is a trap avoided, a dead
   end with its reason, reasoning that was never written down elsewhere,
   or a gap-fact (something the team would otherwise re-discover the
   hard way). Use past sessions you remember clearly, or seed a fresh
   session with a scripted scenario that contains one deliberately.
2. **Run or replay the session** under the current flagging directive.
3. **Check whether the gem became a flag.** Read the session's
   transcript or its private notes, then check the wiki for a matching
   flag block, or run `lore trace flag --json` and filter the output
   yourself (by `wiki`, or by eyeballing timestamps near the session)
   for a write event whose lead matches the gem.
4. **Record a hit or a miss per gem**, not per session — one session can
   contain several gems, and a session that flags one gem and misses
   another is a partial result, not a pass.
5. **Repeat across enough sessions to trust the rate.** A single miss is
   a data point, not a verdict; the goal is a flag rate against a known
   baseline, tracked over time as the directive changes.

## Procedure 2: directive flip-probe

Checks whether a flagging decision reflects the fact's actual worth, or
just the way the directive happens to be worded.

1. **Take one scenario** — a session or a synthetic transcript
   containing a candidate fact whose flag-worthiness is genuinely
   debatable.
2. **Run it once under the directive as written.**
3. **Run it again under the directive reversed** — for example, ask the
   agent to argue for *not* flagging the fact, or invert the framing
   from "flag what's worth keeping" to "skip what's safe to lose".
4. **Trust only the signal that survives both framings.** If the
   fact gets flagged under the normal framing but dropped under the
   reversed one (or vice versa), that is directive bias, not a real
   judgment about the fact's worth — the directive wording needs work,
   not the fact.

## Done when

You have a flag rate against a known-gem baseline (procedure 1) and at
least one flip-probe result (procedure 2) for the current directive
wording. Neither procedure has a fixed passing threshold — the point is
a repeatable measurement to compare across directive changes, not a
one-time gate.

## See also

- [File a flag, and review the flags agents filed](file-and-review-flags.md)
  — the commands that produce the events measured here.
- [Why one flag, and not a session note](../explanation/why-the-flag-is-the-crossing.md)
  — why under-flagging and review latency are the two failures that hide.
