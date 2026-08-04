# Are session notes still worth it — divergent brief
state: exploring
updated: 2026-08-04 (steer 2: funnel identity, machinery price, flag model)

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
price? Nothing is settled.

## Open questions

- Q10 [identity] (open): **What does the funnel need to know about sessions,
  if anything?** The funnel routes "where does context about X live". Options:
  (a) nothing session-specific — artifacts + vault notes + codemap suffice;
  (b) a deterministic **distribution map**: per-session linkage frontmatter
  (repo, branch, PRs, issues, files touched → transcript pointer) already
  captured zero-LLM today — "X was worked in session S, its context landed in
  PR #n" as pure breadcrumbs; (c) LLM-written note bodies (status quo).
  Note: (b) is zero-LLM, zero-poison, and survives C8. Tension: can a
  breadcrumb with no prose ever answer "why did we abandon that approach?" —
  or is that exactly what flags (Q12) are for?

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

## Glossary

- funnel := the layer that knows how context is distributed across artifacts
  (ADRs, PRDs, docs, issues, code, vault) and pulls it in on demand. [agreed]
- flag := a deliberate, occasional capture of one keep-worthy item to the
  vault, with an anchor to its origin. [candidate]
- distribution map := zero-LLM per-session linkage (repo, branch, PRs,
  issues, files → transcript) that tells the funnel where context landed.
  [candidate]
- gem / flag-worthy := residual fact with future value: trap, dead end with
  reason, unwritten reasoning, gap-fact. [candidate]
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
- [[use-cases]] (vault): ratified problem list — any kill/trim must be
  checked against it. Still TODO for this brief.

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
- (hygiene, needs filing) Stale doctrine: the vault's lore orientation note
  (SessionStart-injected) and agent memory still describe the Curator A/B/C
  triad and daily B abstraction — misinformation fed to every session.
- (parked) Fate of `lore_resume` (never called), the 61 legacy concept
  notes, and Q9's render mechanics — revisit after Q10–Q12 settle.
