# Are session notes still worth it — divergent brief
state: exploring → converging on architecture
updated: 2026-08-04 (steer 3: three layers — team artifacts / personal
transcripts+ledger / flags as the crossing; medium principle)

Predecessor: `lore-session-note-shape.md` (2026-07-03, settled the *shape*;
superseded by typed facts, PRD 0008 / ADR 0003). This brief asks the prior
question never asked ground-up: **what is the session note for — and is the
answer "nothing"?**

## Framing

The vision (user, 2026-08-04): **an organic brain that keeps notes and grows a
context for the work.** The context already lives distributed — ADRs and PRDs
(why), epics/issues/PRs (what), docs (how), code (truth). Lore's distinctive
job is therefore not to *hold* everything but to be **the funnel: the layer
that knows how the context is distributed and knows how to pull it in.** The
vault holds supplementary material; domain-specific aggregation (pmo
timelines, CCAT reports) stays in project artifacts — lore stays general.
Governing value: **lightweight** — near-zero burden, or teams (and other
teams) won't use it.

Measured behavior agrees with the funnel identity: in one month of transcripts
the funnel tools (repo docs fetch/list, context pack, codemap, tier resolve)
were called ~76 times; note retrieval 17 times, all within a ≤7-day horizon.

Against that, the session-note machinery's price, quantified over the 8-day
spine window: 367 LLM calls (~46/day), 60 errors, 193 warnings, 91 reaper
force-flushes — plus four PRDs and three epics of engineering spent on note
quality in four months, plus the standing context-poisoning surface (C4).

The open field: what, if anything, do session notes contribute to the brain
that justifies machinery — or does a deliberate, occasional **flag to the
vault** (plus deterministic breadcrumbs) replace them at a fraction of the
price?

**Steer 3 (user, 2026-08-04) — the emerging architecture, three layers:**

1. **Team layer (shared brain):** the artifacts — ADR, PRD, issues/PRs,
   docs, code — plus only *human-worthy* wiki notes (flags, topic notes,
   orientation). "Don't we have all with ADR, PRD, github, docs plus the
   code?" — for the team: yes. Lore is the funnel over it.
2. **Personal layer (private, local *by design*):** each dev's own
   transcripts plus a **machine-format breadcrumb ledger** connecting
   transcripts ↔ artifacts. The individual drills their *own* decisions they
   own. The privacy boundary sits here: nobody traverses into a colleague's
   reasoning. (`transcript-ledger.json` already exists as infrastructure.)
3. **The crossing:** flags/promotion — the only channel by which
   reasoning-level value becomes team-visible. Deliberate, small, anchored.

**Medium principle (settling):** machine data must not masquerade as human
notes. The breadcrumb ledger is routing data no human reads — as Obsidian
files it costs mental load (graph clutter, search hits, disclaimer headers
telling humans not to trust what's in front of them) while serving only
machine drill-down. Each layer in its native medium: machine index →
structured store (`.lore/`), human knowledge → wiki notes, raw record →
transcripts. The session note as a vault file was a medium mismatch.

## Open questions

- Q10 [identity] (settling — steer 3): **What does the funnel need to know
  about sessions?** Answer converging on (b), *in machine form*: a
  deterministic distribution map (repo, branch, PRs, issues, commits, files →
  transcript pointer), held as a structured store extending
  `transcript-ledger.json` — **not as vault notes** (medium principle).
  (c) LLM note bodies is out; "why did we abandon X?" is the flags' job
  (Q12) or stays in the owner's private transcript drill. Residual detail:
  exact store shape and how `lore_search`/drill expose it.

- Q11 [price] (open): **Is the Curator A machinery worth its cost?** Cost:
  quantified above; plus ops fragility (60 NoteClosedError in 8 days) and
  the adoption burden every new team inherits. Measured benefit: 17
  reads/month, ≤7-day horizon. Options: keep as-is; **trim to deterministic
  capture** — transcript archive + linkage frontmatter, zero LLM anywhere
  (keeps drill-down and the Q10b distribution map, kills noise, poison, and
  price together); keep LLM notes only for session classes that want them
  (old Q3 — e.g. science sessions with no artifact trail); kill capture
  entirely. Constraint check: the trim option destroys nothing irreversibly —
  transcripts remain, notes could be regenerated later from archives if a
  reliable extractor ever exists.

- Q12 [flag] (open): **The deliberate-capture alternative: an occasional flag
  when something is worth keeping.** Groundwork exists: journals
  (`journals/ai.md` + `human.md`, non-derived, no pipeline) +
  `lore_journal_write`; the flag variant would route into the wiki graph with
  scope and wikilinks instead of a flat file. Design axes held open: *who
  flags* — human command; agent-initiated (secretary pattern: the agent
  notices "worth keeping" mid-session); end-of-session single question; *what
  a flag captures* — the fact, why it's worth keeping, an anchor into the
  archived transcript; *where it lands* — wiki note vs journal vs project
  folder. What counts as flag-worthy inherits the old gem definition:
  environment traps, dead ends with reasons, unwritten reasoning, gap-facts.
  **The central tension (A5):** auto-capture was ratified precisely because
  humans forget to write notes; a flag-only model reintroduces
  amnesia-by-default. Middle grounds: Q10b breadcrumbs auto-captured +
  flags for gems; agent-initiated flags make remembering the agent's job,
  not the human's.

- Q1b [horizon] (settling — accepted as observation): every measured organic
  read is short-horizon continuity (ages 1 min – 7 days; archival reader
  never observed; user confirms Obsidian reads ≈ 0 *because notes aren't
  useful*). Design implication contingent on Q11/Q12: whatever is kept must
  serve a reader days away with partial context; long-tail value travels
  only via flags/promotion, never via note bodies nobody re-finds.

- Q8 [bridge] (open, demoted to "could"): lore **could** be the
  cross-harness bridge (cursor → claude etc.) — same-harness continuity is
  already solved by each harness. Minimal question: what would the bridge
  actually need — normalized transcript archive + linkage, or prose notes?
  If the former, the bridge survives the Q11 trim untouched.

- Q13 [durability] (reframed by steer 3): **locality is the design, not the
  hole.** Transcripts hold reasoning; reasoning is private; therefore the
  archive being machine-local is *correct* — the privacy boundary made
  structural. What remains open shrinks to: (i) personal durability — backup
  of one's own archive (a dev's own concern, maybe a doctor-check nudge, not
  a lore feature per C10); (ii) same-person multi-machine fragmentation
  (laptop + desktop = two half-brains); (iii) **anchor asymmetry**: a flag
  shared in the wiki may carry an `@N` anchor into a transcript only its
  author holds — drillable for the owner, dead weight for colleagues. Flags
  must therefore stand alone; the anchor is a bonus for the author, never
  load-bearing for the team.

## Pressure-test: hybrid vs organic brain (2026-08-04)

Hybrid under test: auto-captured deterministic breadcrumbs (linkage
frontmatter → transcript pointer) + transcript archive + **agent-initiated,
topic-routed flags**. Tested against what "an organic brain that grows a
context for the work" functionally requires.

**Where it holds:**

- *Topology (the strongest resonance).* A brain files by topic and
  strengthens with use, not by date. Flags appending to living topic/project
  notes grow the vault the way the vision describes; date-sharded session
  sediment stops pretending to be memory. The date layer that remains
  (breadcrumbs) is logistics, and honest about it.
- *Lab-notebook culture (domain resonance).* Real labbooks are deliberate:
  instruments auto-log raw data, the scientist chooses what to write down.
  Transcripts = instrument logs; flags = notebook entries; breadcrumbs = the
  run index. The auto-note was trying to make the instrument write the
  notebook. The already-ratified deliberate `/lore:handover` spec shows the
  ecosystem was drifting this way on its own.
- *Lightweight (C9).* Capture needs no LLM backend at all — a team can adopt
  breadcrumbs + archive with no API key, no curator config, none of the
  pipeline's failure classes (60 NoteClosedErrors/8 days all die). The
  machinery deletion is large and reversible (code in git, transcripts
  regenerable-from … see G1/Q13).
- *Forgetting + trust surface.* Fewer, better lines; positive-evidence
  staleness applies to a reviewable volume; C4 surface shrinks drastically.

**Stress points (named, not resolved):**

- S1 *continuity gap.* The hybrid kills the one artifact with measured
  organic use: the ≤7-day "what did the last session on this thread do" read
  (~17/month). Raw-transcript drill is too expensive to replace it (C7).
  Mitigations on the table: the deliberate `/lore:handover` (already
  spec'd — but deliberate, so same A5 amnesia risk); a deterministic
  breadcrumb recap in the banner ("yesterday: repo X, PR #n, files …");
  accept harness-native continuity for the common case.
- S2 *under-flagging amnesia — now with a team cost.* "Did we already try
  X?" — negative-result recall is the classic labbook value, and exactly what
  agents will under-flag (bias toward traps and successes over abandoned
  paths). Under steer 3 the stakes rise: flags are the *only* channel across
  the privacy boundary, so an unflagged gem is not just personally
  forgotten — it is structurally invisible to the team forever (the owner's
  transcript being private). Measurable (flag rate against a known-gem
  baseline); must be measured, never assumed.
- S3 *topology without a gardener.* C8 forbids a defrag/merge rescue layer,
  so flag routing must be right at write time: route-before-write (search
  first, append to the existing topic note). Otherwise the sibling-note
  problem returns at topic level. Candidate assist: the funnel itself
  proposes the target note — the funnel serving capture, not just retrieval.
- S4 *flags are still LLM writes.* C4 shrinks but does not vanish — and a
  flag carries an implicit "worth keeping" endorsement, so per-line trust
  demand *rises*. The typed-fact gates survive in miniature: no anchor, no
  flag; stamped phrasing for anything unverifiable.
- G1 *the safety-net hole* → promoted to Q13 (transcript durability).

**Verdict of the test:** the hybrid survives the vision's core demands
(topic-shaped growth, lightweight, deliberate culture) better than the status
quo, with three real stress points (S1–S3) that each have candidate
mitigations, one structural caveat (S4), and one unresolved hole (Q13).
Not adopted — held open.

## Use-case cross-check (2026-08-04, against [[use-cases]] — 27 items, 2026-05-07)

**Verdict: no veto.** Nothing in the ratified list hard-requires session notes
as vault files — but the check surfaces four honest losses needing explicit
user confirmation, one identity rewrite, and several sharpenings.

**The list already contains the steer-3 identity.** Use case #16
(implementation-arc narrative): "Artifacts stay in their canonical homes …;
Lore is the threading layer that makes the arc walkable across time, harness,
and teammate." That *is* the funnel + ledger, in ratified language. #26's
"Lore is the WHY" overstates under steer 3 and wants rewriting to "Lore knows
where the why lives and threads it".

**Losses to confirm (the price of retirement):**

- L1 — #1 (the wedge, "capture the why via auto-extracted session notes"):
  the promise weakens from "the why is captured automatically" to "the why is
  recorded raw (private transcript) and crosses to the team only via
  artifacts and deliberate flags". This is A5 at the product-promise level.
- L2 — #14 (teammate handoff): passive "browse what a colleague's sessions
  did last week" is *removed by design* (privacy boundary). Handoff becomes
  deliberate (`/lore:handover`) + artifacts + flags. No evidence it was ever
  used passively (all measured reads were same-user).
- L3 — #6 (briefings/active distribution): loses its source (session notes)
  on top of its dead engine (Curator B). Needs a new source (flag/topic-note
  digest?) or parking.
- L4 — #15 (onboarding): was "falls out of #3 cross-handle synthesis" —
  #3 is dead (C8). Onboarding now rides on project orientation + topic-note
  quality, i.e. on flags working (S2).

**Sharpened by the architecture:** #7 sensitivity gate (only small deliberate
flags ever cross — the gate's job shrinks structurally); #16, #17 (banner
recap gets cheaper and deterministic), #19/#20 (capture needs *no lore-owned
LLM at all* — flags are written by the session's own agent; lore's backend
becomes optional, briefings-only), #22 (knowledge = markdown+git stays;
ledger = derived, rebuildable index; transcripts = private raw), #27
(journals — "potential to grow into a pillar" reads prescient).

**Needs a rule:** #23 edit discipline — flags append to topic-note bodies,
which the vault edit policy reserved for humans. Proposed shape: flags are
append-only blocks, attributed, never editing existing content. Open.

**Already stale independent of this brainstorm:** #3, #10, #11 (curator
triad, surfaces — superseded by earlier epics); the use-cases note itself
needs a revision pass once this brief settles (hygiene).

## Absorbed questions (from earlier passes)

- Q2 (residual inversion) and Q6 (promotion) → folded into Q12: the
  recorded-nowhere residual is the flag-worthiness criterion; promotion *is*
  the flag, agent-initiated.
- Q4 (positive signal definition) → folded into Q12 (flag-worthiness).
- Q5 (ledger) and Q3 (per-class depth) → contingent on Q11's outcome.
- Q7 (kill) → absorbed: full kill superseded by Q11's option set.
- Q9 (handover vs reporting renders) → parked; built on the reporting
  function, which was struck (see settled).

## Assumptions

- A1 (broken → reframed Q1b): archival readers exist. Measured: reads are
  rare (~17/month, ≲5% of sessions) and ≤7 days old; archival reader never
  appeared. Obsidian reads ≈ 0, confirmed by user — because notes aren't
  useful.
- A2 (live): the archived transcript persists and is drillable — capture of
  *transcripts* is not in question, only what is derived from them.
- A3 (affirmed by user): ADRs/PRDs/docs already grow the work's context "to
  some degree" — the artifact layer is the primary store; sessions are
  secondary.
- A4 (live): deterministic = trusted; LLM-written = suspect. Linkage
  frontmatter is already zero-LLM.
- A5 (contested — founding doctrine reopened 2026-08-04): "capture must be
  automatic because humans forget to write notes" (the Capture, Retrieve,
  Never Ask model). The flag model revises this; it must answer the amnesia
  risk it was built against. Not yet resolved either way.

## Domain constraints

- C2: SessionStart context budget is tiny; local backends have a hard
  capacity ceiling — whatever survives must compress to a banner line.
- C3: notes are constitutionally non-authoritative (ADR 0004); anything
  promoted to authority must route through real artifacts.
- C4: every LLM-authored line in the vault is a context-poisoning surface
  (Stille-Post). Noise is active risk, not neutral bulk.
- C5: verification-against-GitHub exists only for repo-linked sessions;
  science/ad-hoc sessions have no authority store.
- C6: wikis are portable; links resolve per-wiki.
- C7: raw transcript is the dominant processing cost; each turn seen once.
- C8 (ratified 2026-08-04): **no LLM abstraction pass exists as a rescue
  move** — Curator B/C distillation proved unreliable (wrong claims,
  inconsistencies). No LLM-verifies-LLM, no LLM-distills-LLM.
- C9 (ratified 2026-08-04, governing): **lightweight or unused.** Lore must
  impose near-zero burden — setup, runtime, cognitive — or teams and other
  teams won't adopt. Note the asymmetry: the funnel is read-only and needs no
  per-user buy-in; capture machinery must be installed and trusted by
  everyone whose sessions it eats.
- C10 (ratified 2026-08-04): lore stays **general**. Domain aggregations
  (pmo timelines, CCAT reporting) live in project artifacts, never as lore
  features.
- C11 (settling, steer 3): **the vault is a human surface.** Only content a
  human might want to read lives as wiki notes; machine state lives in
  machine stores under `.lore/`. Machine-written files in the Obsidian graph
  cost mental load and are a medium mismatch.

## Glossary

- funnel := the layer that knows how context is distributed across artifacts
  (ADRs, PRDs, docs, issues, code, vault) and pulls it in on demand. [agreed]
- hybrid := auto deterministic breadcrumbs + transcript archive +
  agent-initiated topic-routed flags; the capture model under test. [candidate]
- flag := a deliberate, occasional capture of one keep-worthy item to the
  vault, with an anchor to its origin. [candidate]
- distribution map := zero-LLM per-session linkage (repo, branch, PRs,
  issues, files → transcript) that tells the funnel where context landed.
  [candidate]
- gem / flag-worthy := residual fact with future value: trap, dead end with
  reason, unwritten reasoning, gap-fact. [candidate]
- ledger := **redefined by steer 3**: the machine-format store connecting
  transcripts ↔ artifacts (extends `transcript-ledger.json`). The old
  meaning — the in-note typed-fact ledger of PRD 0008 — is historical.
  [candidate — rename pending, "session ledger" vs "distribution map"]
- privacy boundary := reasoning (transcripts) is private to its owner; only
  artifacts and flags cross to the team. A colleague's "why" is reachable
  only by asking them — the owner drills their own archive and answers.
  [candidate]
- handover/recap := carrying a working thread across a session boundary —
  next session, other harness, other human. [candidate]
- reporting := struck 2026-08-04 — CCAT-style reports are a different
  animal, not a lore note function. [struck]

## Sources

- [Transcript sweep 2026-08-04]: 307 sessions Jul 5–Aug 4; 17 note
  retrievals (read 11 / search 4 / drill 2, resume 0), all ≤7 days old;
  funnel tools ~76 calls; direct session-note Reads all meta-work. Caveats:
  30-day retention; Obsidian and banner consumption invisible. Lore has no
  read-side telemetry (spine is write-only) — permanent measurement needs a
  read-side spine event.
- [Spine cost window 2026-07-27→08-04]: 367 LLM calls, 60 errors (NoteClosed),
  193 warnings, 91 reaper force-flushes, 152 session-starts in 8 days.
- [Journal groundwork]: `lib/lore_core/journal.py` — non-derived channels,
  feature-flagged, MCP + CLI; `journals/ai.md` + `human.md` exist in vault →
  Q12 starting point.
- [Specimen: wiki/private/sessions/2026/08/03-1759]: 1,636 lines / ~58k
  tokens; Done ≈ GitHub restatement; 6–8 gems; ledger 3× duplication.
- [PRD 0002, PRD 0008, ADR 0003, ADR 0004]: the two prior "make notes useful"
  attempts and their ratified mechanics; the recurrence of the complaint
  across both is itself evidence (the problem may be the mission, not the
  mechanism).
- [[use-cases]] (vault, 2026-05-07): ratified problem list — cross-checked
  2026-08-04, see the cross-check section: no veto, four losses (L1–L4) to
  confirm.
- [Transcript archive audit 2026-08-04]: `wiki/*/.transcripts/` gitignored;
  556 files / 216 MB on one machine; retention janitor covers only ops logs
  (spine/flushes/runs), transcripts unbounded and unmanaged → Q13.
- [Handover spec (proposed 2026-05-11)]: `/lore:handover` deliberate +
  explicit, user-invoked — prior art for deliberate capture inside lore's own
  design history → S1, Q12.

## Parked / settled

- (settled, ratified elsewhere) Notes are never authoritative.
- (settled 2026-08-04, user) **Abstraction layer dead.** Curator B/C retired;
  verified no regression (B last spawned 2026-06-15, zero B/C runs, zero
  abstraction writes since, no code paths). 61 legacy concept notes remain
  read-only stock.
- (settled 2026-08-04, user) Reporting struck as a note function; CCAT daily
  reports are out of scope for lore.
- (settled 2026-08-04, user) Same-harness continuity is the harness's job;
  cross-harness bridge demoted to "could" (Q8).
- (settled via Q1 evidence + user) Readers: short-horizon continuity, plus a
  human recap reader *worth earning back* — not an archival audience.
- (settling 2026-08-04, steer 3) **Session notes as vault files are retired
  as a form.** The breadcrumb job moves to a machine-format ledger; the
  human-worthy residue moves to flags/topic notes; the recap function is
  served by a deterministic banner recap from the ledger, not by note files.
  The "reader worth earning back" is earned by *removing* machine notes from
  the human surface, not by writing better ones. Pending explicit user
  confirmation before state flips to settled.
- (hygiene, needs filing) Stale doctrine: the vault's lore orientation note
  (SessionStart-injected) and agent memory still describe the Curator A/B/C
  triad and daily B abstraction — misinformation fed to every session.
- (parked) Fate of `lore_resume` (never called), the 61 legacy concept
  notes, and Q9's render mechanics — revisit after Q10–Q12 settle.
