# Session-note retirement + flag architecture — design export

Exported 2026-08-04 from `lore-session-notes-worth.md` (divergent brief). All
items under "Settled" were confirmed explicitly by the user, including the
four named losses. This file is the input to grilling and PRD drafting. It is
a design description, not a build plan. The brief stays open for the parked
questions; reopen there if grilling breaks something.

## The architecture at a glance

```
TEAM LAYER (shared brain)
  ADRs · PRDs · issues/PRs · docs · code          ← authoritative artifacts
  wiki: topic/project notes + flags               ← human-worthy only (C11)
  lore = the funnel: knows where context lives, pulls it in

        ▲  flags — the ONLY crossing (deliberate, one fact, stamped)
        │
PERSONAL LAYER (private by design — the privacy boundary)
  own transcripts (wiki/*/.transcripts/, gitignored — locality is correct)
  breadcrumb ledger (machine format, extends transcript-ledger.json):
    session → repo, branch, PRs, issues, commits, files → transcript id
  owner drills their own reasoning; nobody traverses a colleague's
```

## Settled decisions

1. **Session notes as vault files are retired.** No more LLM-composed session
   notes, in-note fact ledgers, or note renders. Accepted losses (user,
   2026-08-04): the wedge re-phrases privacy-first (why is recorded raw +
   crosses only via artifacts/flags); passive teammate browsing removed by
   design; briefings need a new source or parking; onboarding rides on flag
   quality.
2. **Medium principle / vault is a human surface.** Machine data never
   masquerades as wiki notes. Machine state lives in `.lore/` stores; wiki
   holds only what a human might want to read; transcripts hold the raw
   record. (The disclaimer-headed machine note was the symptom of the
   mismatch.)
3. **Breadcrumb ledger, machine format.** The deterministic per-session
   linkage (already captured zero-LLM: repo, branch, PRs, issues, commits,
   files, transcript id) becomes a structured store extending
   `transcript-ledger.json` — never rendered as notes. It is derived and
   rebuildable (from transcripts + git + GitHub); knowledge remains
   markdown+git; transcripts remain private raw.
4. **Privacy boundary = locality.** Transcripts stay machine-local and
   gitignored *by design*. Personal backup is the dev's own concern (not a
   lore feature). A colleague's "why" is reachable by asking the owner, who
   drills their own archive.
5. **SessionStart recap is deterministic.** The banner's continuity function
   ("yesterday: repo X, PR #n, files …") renders from the ledger, zero LLM.
   Rich mid-thread handoffs use the deliberate `/lore:handover` (existing
   spec). Harness-native continuity covers the common same-session case.
6. **The flag** — the deliberate crossing:
   - *Initiation:* agent-primary (secretary pattern, files in the moment a
     gem appears: trap, dead end + reason, unwritten reasoning, gap-fact) +
     always-available human command (CLI + slash) + one agent self-check at
     session end ("anything flag-worthy unflagged?"). No human interruption
     anywhere. Team-relevant gems go to the flag, not private agent memory.
   - *Content:* one fact per flag; lead sentence + short body (claim + why
     worth keeping); deterministic origin block (author, date, transcript id,
     `@N` anchor, repo/PR/issue/commit refs). Refs code-verified at write:
     checkable → plain with ✓; uncheckable → stamped session-talk phrasing.
     Hard gate: no origin, no flag. Flags stand alone for the team — the
     anchor is an owner-only bonus (anchor asymmetry). No mandatory kind
     taxonomy; optional tags.
   - *Landing:* route-before-write — funnel proposes the target; append to
     the owning topic/project note as an attributed, append-only block; new
     topic note only when no home exists. Wiki/scope routing rides `.lore.yml`
     unchanged. Edit policy extension: agents append, never edit; humans own
     bodies and refactor freely.
   - *Machinery:* MCP tool + CLI verb on the journal pattern — deterministic
     write, no pipeline. One spine event per flag (write-side telemetry from
     day one). Capture needs no lore-owned LLM; lore's backend becomes
     optional (briefings only).
7. **Measurement is mandatory before trust (S2).** Instrument flag rate from
   day one; run a known-gem baseline (replay sessions containing known gems,
   check they get flagged); flip-probe the directive. Under-flagging is the
   main failure mode and is invisible without measurement.
8. **Rollout is additive-first.** The flag primitive ships beside the
   existing pipeline; retirement/teardown of note composition is the *last*
   step, taken with S2 evidence in hand.

## What is retired vs what stays

Retired: LLM note composition (Curator A's compose path), in-note typed-fact
ledger + rendered bodies, note files as the capture output, `sessions/*.md`
growth. Already dead, confirmed no-regression: Curator B/C, the abstraction
layer (concepts/decisions/threads extraction), surfaces.

Stays: transcript capture + archive, deterministic linkage capture, scope
routing (`.lore.yml`), the funnel tools (repo docs, context pack, codemap,
search/drill over the human wiki), journals (orthogonal genre: freeform vs
fact), sensitivity gate (now only over flags — job shrinks), 61 legacy
concept notes (read-only stock).

## Binding constraints (carried from the brief)

- C2 banner budget tiny; local-backend capacity ceiling.
- C3/ADR 0004: nothing in the vault asserts world-state on its own
  authority — flags keep code-stamped phrasing.
- C4: every LLM-written vault line is a poisoning surface — flags are few,
  gated, and land where humans see them (the self-correcting loop).
- C8: no LLM-verifies-LLM, no LLM-distills-LLM — no rescue layers.
- C9 (governing): lightweight or unused; capture must work with no API key.
- C10: lore stays general — no domain aggregation features.
- C11: vault is a human surface.
- July brief (still valid): deterministic = trusted; no insider taxonomies
  in user-facing forms.

## Open items grilling must resolve

1. Flag block template (exact markdown shape of the attributed block).
2. User-facing naming (`lore flag` / `/lore:keep` / other) and directive
   wording + flagging threshold (tune via S2 experiment).
3. Banner recap depth and cross-day window (yesterday only? open threads?).
4. Ledger store shape (extend transcript-ledger.json vs sibling store) and
   how `lore_search`/`lore_drill` expose ledger routing; read-side
   telemetry event.
5. Briefings (L3): new source (flag/topic-note digest) or park.
6. Migration of the ~556-note stock (archive wholesale / freeze in place;
   C8 forbids a reliable LLM gem-harvest).
7. Fate of `lore_resume` (never called) and the retired machinery's config
   surface (curator backend becomes optional).
8. Hygiene: rewrite the vault's lore orientation note and [[use-cases]]
   (#1 wedge phrasing, #26 "lore is the WHY" → "knows where the why
   lives"; strike triad/surfaces remnants); update agent memories.
9. Same-person multi-machine story for the personal layer (two half-brains)
   — may be "document it, don't solve it".

## Evidence (why this design, one line each)

- Reads: 17 note retrievals/month across 307 sessions, all ≤7 days old;
  archival reader never observed; lore_resume never called.
- Cost: ~46 LLM calls/day, 60 errors + 91 force-flushes per 8 days for the
  note pipeline; funnel tools out-used note retrieval ~4.5:1.
- Specimen: 1,636-line note for one session; Done ≈ GitHub restatement;
  6–8 genuine gems buried; every fact tripled.
- Archive audit: 556 transcripts / 216 MB, gitignored, one machine — made
  the privacy boundary structural instead of accidental.
- Use-case cross-check: no veto; #16 already states the funnel identity in
  ratified language; losses L1–L4 named and user-confirmed.
- History: two PRDs + one prior brainstorm iterated the note *mechanism*;
  the noise complaint persisted — the mission, not the mechanism, was the
  problem.
