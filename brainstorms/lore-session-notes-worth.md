# Are session notes still worth it — divergent brief
state: exploring
updated: 2026-08-04 (Q1 evidence pass; user rulings: abstraction layer dead, functions = handover/recap + reporting)

Predecessor: `lore-session-note-shape.md` (2026-07-03, settled the *shape*; its
shape was since replaced by typed facts, PRD 0008 / ADR 0003). This brief asks
the prior question that was never asked ground-up: **what is the session note
for, now that lore-workflow exists — and is the answer "nothing"?**

## Framing

When Lore started, the session was the only place decisions and reasoning
lived; notes had to carry everything. Since then lore-workflow routes real work
into durable, authoritative artifacts: PRD (why-product), ADR (why-technical),
epic + issues (what/plan), PRs + git (what changed), docs (how to use). Each is
written at the moment of highest context, by the process that owns it.

The session note's claimed residual is "everything that didn't go through the
workflow". The evidence says that residual is real but small, and the current
note buries it: the 2026-08-03 epic-310 note is 1,636 lines / ~58k tokens, of
which ~30 Done lines restate what GitHub already records (each `✓` literally
*proves* the fact is recorded elsewhere), while the ~6–8 genuine gems
(environment traps, silent failures, "agreed but recorded nowhere") sit
undifferentiated in a 50-line Findings list, and the ledger then repeats every
fact twice more.

This brief holds the whole space open: kill notes, shrink them to the residual,
retarget them at a different reader, or split them by session population.
Nothing is settled.

## Open questions

- Q1 [reader] (settling — evidence in, 2026-08-04): **Who actually reads a
  session note, and when?** Measured over every Claude Code transcript on the
  machine (307 sessions, 2026-07-05 → 2026-08-04; older transcripts rotated
  out): **17 MCP retrieval calls total** — `lore_read` 11, `lore_search` 4,
  `lore_drill` 2, `lore_resume` **0**. Of the reads, 9 targeted session notes,
  2–3 concept/strategy notes. Direct filesystem `Read` of session notes: 10,
  *all* lore-quality meta-work (PRD 0002 pilot, this brainstorm), none
  organic. What the organic reads share: **all in ccat work, and all within a
  short horizon — ages 1 min, ~2.5 h ×2, 1 d, 3 d ×2, 7 d. No session note
  older than 7 days has ever been retrieved.** The "colleague months later"
  reader — PRD 0002's explicit design target — has never appeared. Session
  notes were retrieved *more* than Curator B's concept notes (~9:3), so the
  abstraction layer is read even less than the raw layer. Blind spots the log
  cannot see: (i) the SessionStart banner deterministically consumes note
  titles/summaries every session — by volume the *dominant* consumer; (ii)
  human reads in Obsidian leave no trace — only the user can answer that;
  (iii) curator feedstock reads happen outside these transcripts. **Settled
  2026-08-04 (user):** (ii) is near-zero *because notes aren't useful* — an
  aspirational reader, not an absent one; (iii) is void — abstraction layer
  dead (C8). The readers to design for: short-horizon continuity
  (agent/human), the reporting pipeline, and a human recap reader worth
  earning back.

- Q1b [reframe] (open, spawned by Q1 evidence): **Is the session note a
  handover artifact with a ~one-week half-life, not an archive?** Every
  observed organic read is thread-continuity: "what did the last session on
  this do" (docs strategy, queue discovery, report flow, deploy-role fix,
  schema cleanup). That is the resume/handover job, not the labbook-archive
  job. If accepted, it reframes hard: depth and ledger should optimize for a
  reader *days* away with partial context, not months away with none;
  long-tail value would then live in *promotion* (Q6) — with abstraction dead
  (see settled), promotion is the *only* long-tail path; retention of full
  bodies past the horizon becomes questionable (C4 poisoning surface with no
  measured reader). User input 2026-08-04 strengthens this from both sides:
  the two wanted functions — handover/recap (cross-harness, cross-human) and
  **reporting what has been worked on** — are both short-horizon by nature
  (reporting cadence is daily/weekly). The user also confirmed they rarely
  open old notes in Obsidian, *because they are not useful* — a symptom to
  fix, not a reader to design away. Tension remains: a 30-day observation
  window cannot rule out rare high-value archaeology ("what broke in
  March?") — absence of evidence over one month is weak evidence of absence
  over a year.

- Q2 [residual] (open): **Should the note keep only what is recorded nowhere
  else?** The verification machinery already computes this per line: `✓` means
  "an authoritative store has this", the hedge stamps ("agreed in discussion,
  recorded nowhere", "reported done, recorded nowhere") mean "this exists only
  here". Inversion on the table: `✓` lines collapse to a one-line breadcrumb
  ("epic #310 landed — 8 PRs, see GitHub"), and the note body *is* the
  recorded-nowhere residual. Tension: the Done list has skim value as a
  timeline even when redundant; and a breadcrumb-only note depends on GitHub
  permanence and access (C5).

- Q3 [population] (open): **Are there two session populations needing two note
  depths?** Workflow-run sessions leave a dense artifact trail — residual is
  gems + traps only. Ad-hoc sessions (debugging, science analysis, brainstorms,
  ops) leave *no* trail — the note is the only record. Options: adaptive depth
  (workflow sessions → stub + links; ad-hoc → full labbook); uniform format
  with the workflow case naturally shrinking via Q2's inversion; explicit
  session-type stamp from lore-workflow skills. Tension: detection is fuzzy;
  most sessions are mixed.

- Q4 [signal] (open): **What is labbook signal, defined positively?** "What's
  left over" is a negative definition. Candidates from the specimen note:
  environment traps (`gh pr edit` fails silently → use REST;
  `LORE_SUPPRESS_CAPTURE=1` leaks into pytest; Vale's three silent-clean
  traps), dead ends with the reason they were abandoned, reasoning that didn't
  make the ADR cut, effort calibration (what was tried, how many rounds),
  gap-facts. Tension: several of these want to *stop being* session-note
  content — a trap worth remembering wants to be an issue, a doc line, or a
  memory, not a line in a dated note nobody re-finds (see Q6).

- Q5 [ledger] (open): **Who is the ledger for, and where should it live?** It
  triples file size (JSON comment + bold restatement + quote per fact) to
  provide grounding and drill-down. Options: sidecar file next to the archived
  transcript; keep inline; drop quotes and keep JSON only; ledger *is* the
  note (machine format) and any human reading is rendered on demand. Cuts
  across Q1: if the primary reader is a machine, the rendered body is the
  optional layer, not the ledger.

- Q6 [promotion] (open): **Should recorded-nowhere facts be pushed toward a
  real home instead of warehoused?** A session that surfaces "the epic
  workflow never bumps versions" could end by *filing* that (issue via the
  file-issue skill, memory entry, doc patch) rather than leaving it as note
  residue. Then the note's job shrinks to: index of the session + pointer to
  where each fact went. Tension with the ratified Capture-Retrieve-Never-Ask
  stance — no capture-time nudges to the *human*; but an *agent*
  end-of-session filing pass may not violate that. Contested — needs the
  user's read.

- Q7 [existence] (narrowed 2026-08-04): **The honest kill option — now only
  for the archive function.** Full kill is off the table: the user names two
  wanted functions (handover/recap, reporting). What remains open is killing
  the *archival* role: does anything past the ~week horizon deserve to exist
  as a note body, or only as promoted artifacts (Q6) + transcript archive?
  Known dependents rechecked: SessionStart banner, `lore_resume` (never
  called — dead weight or undiscovered?), the vault graph. Curator B
  feedstock struck as a dependent — B is dead.

- Q8 [cross-cutting] (open): **Is the cross-harness / cross-team / human-AI
  promise still load-bearing?** The user's own hunch: mostly solved by
  workflows. But workflows assume GitHub-repo work. Science sessions (data
  reduction, calibration runs) have no repo artifact trail and a real labbook
  tradition — possibly the strongest surviving case, and the population Q3
  points at. Untested: does the ccat/science wiki actually accumulate useful
  session notes today?

## Assumptions

- A1 (broken → reframed Q1b): we assumed *someone* reads session notes after
  the day they were written, on an archival horizon. Measured: reads exist but
  are rare (~17 in a month, ≲5% of sessions pull anything) and confined to a
  ≤7-day continuity window; the archival reader never appeared in the
  observable record. Unmeasured remainder: Obsidian (human) reads, banner
  consumption, curator feedstock.
- A2 (live): the archived transcript persists and is drillable, so a note
  never needs to restate — only to index and point.
- A3 (live): workflow artifacts adequately capture the why at ratification
  time; mid-implementation reasoning that never reached PR/ADR text is
  genuinely at risk of loss (supports a nonzero residual).
- A4 (live, inherited from predecessor C0): deterministic = trusted;
  LLM-written = suspect. Frontmatter session facts are already zero-LLM.

## Domain constraints

- C1: capture is lights-out (SessionEnd/reaper); any design must work with
  zero human effort at write time. Ratified product stance: Capture, Retrieve,
  Never Ask.
- C2: SessionStart context budget is tiny and local backends have a hard
  capacity ceiling (~120B model silently returns empty on oversized prompts) —
  whatever the note becomes must compress to a banner line.
- C3: notes are constitutionally non-authoritative (ADR 0004 code-stamped
  authority phrasing; disclaimer header). Any redesign keeping notes must keep
  this; any redesign promoting content must route through real artifacts.
- C4: every LLM-authored line in the vault is a context-poisoning surface —
  future sessions re-ingest it, curators abstract it, claims acquire sources
  (Stille-Post). Noise is not neutral bulk; it is active risk.
- C5: verification-against-GitHub only exists for repo-linked sessions;
  science/ad-hoc sessions have no authority store to verify against — any
  Q2-style inversion needs a story for them.
- C6: wikis are portable; whatever replaces or shrinks notes must keep links
  resolving per-wiki.
- C7 (inherited): raw transcript is the dominant processing cost; each turn
  seen once, cheapest tier that works.
- C8 (ratified 2026-08-04): **no LLM abstraction pass is available as a rescue
  move.** Curator B/C-style distillation over LLM-written notes proved
  unreliable — wrong claims entered context (poison), too many
  inconsistencies. Whatever the note itself doesn't carry well, no downstream
  layer fixes. Extends predecessor C1 (no LLM-verifies-LLM): also no
  LLM-distills-LLM.

## Glossary

- residual := facts established in a session that no authoritative artifact
  (git, GitHub, ADR/PRD, docs, memory) records. [candidate]
- gem := a residual fact with plausible future value (trap, dead end, unwritten
  reasoning). [candidate]
- labbook signal := contested — currently defined only negatively (Q4). [contested]
- handover/recap := carrying a working thread across a session boundary —
  next session, other harness, or other human. [candidate]
- reporting := periodic human-facing account of what has been worked on,
  sourced from notes (daily/weekly cadence). [candidate]

## Sources

- [PRD 0008 / epic #282]: end-mode typed facts, Done/Findings/Open render,
  epistemic stamping — shipped; produced the current note form → bears on Q2, Q5.
- [ADR 0003]: body is a deterministic render of the append-only ledger → Q5.
- [ADR 0004]: authority phrasing is code-stamped → C3, Q2.
- [Specimen: wiki/private/sessions/2026/08/03-1759 (epic-310 landing)]: 1,636
  lines; Done ≈ GitHub restatement; 6–8 gems in Findings; ledger 3× duplication
  → Q2, Q4, Q5.
- [PRD 0002 / epic #151]: one-note-per-session, exemplar-bleed fixes → history
  of "make notes useful" attempts; two PRDs in, the noise complaint persists —
  evidence the problem may be the mission, not the mechanism → Q7.
- [Predecessor brief lore-session-note-shape.md]: settled chapter shape
  2026-07-03; superseded within a month — same evidence → Q7.
- [[use-cases]] (vault): ratified list of problems Lore solves — any kill or
  shrink must be checked against it. Not yet re-read for this brief: TODO.
- [Transcript sweep 2026-08-04]: all `~/.claude/projects` transcripts (307
  sessions, Jul 5–Aug 4) grepped for lore MCP calls and direct vault reads;
  method: tool_use extraction, both plugin prefixes → settles most of Q1,
  breaks A1, spawns Q1b. Caveats: 30-day retention window; banner, Obsidian,
  and curator reads invisible. Lore's own read path emits no telemetry
  (spine.jsonl is write-side only) — measuring this permanently would need a
  read-side spine event.

## Parked / settled

- (settled, ratified elsewhere) Notes are never authoritative — not reopened
  here.
- (settled 2026-08-04, user) **The abstraction layer is dead.** Curator B/C
  and the concepts/decisions/threads extraction are retired — could not be
  made reliable; wrong claims poisoned context. Verified in the running
  system: B last spawned 2026-06-15, zero B/C runs in the spine window, zero
  abstraction writes since, no B/C code paths in hooks/spawn — no regression.
  61 legacy concept notes remain as read-only stock (2 were organically read;
  keep, don't grow).
- (settled 2026-08-04, user) Wanted note functions: handover/recap
  (cross-harness, cross-human) and reporting. Replaces the open-ended
  reader list of Q1.
- (hygiene, needs filing) Stale doctrine: the vault's lore orientation note
  (SessionStart-injected) still describes the Curator A/B/C triad and daily
  B abstraction; agent memory likewise. Should be corrected to match the
  ratified state — outside this brief's questions.
- (parked) Fate of `lore_resume` (never called) and of the 61 legacy concept
  notes — revisit after the note redesign settles.
