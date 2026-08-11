# Lore — domain context

An AI-navigation map of Lore's domain. Every term below has one tight
meaning, matched against shipped code. Cross-check a claim here against
the module it points at before you rely on it. A term or a behavior
that no real symbol grounds does not belong here.

## Vault, wiki, scope

- **Vault** — the directory `$LORE_ROOT` points at. Contains one
  `wiki/` subdirectory plus `.lore/` for derived, host-local state.
- **Wiki** — a mounted knowledge store at `<vault>/wiki/<name>/`. Each
  wiki is its own git repo; a vault can host several. `lore wiki new`
  scaffolds `projects/`, `concepts/`, `decisions/`, `sessions/`,
  `inbox/`. Lore writes nothing into a wiki automatically. A human, an
  agent's flag, or `/lore:inbox` writes it, when something is worth
  keeping.
- **Scope** — a colon-separated namespace inside a wiki
  (`ccat:data-center:data-transfer`), resolved from a working
  directory by longest-prefix match against `.lore/attachments.json`.

Full model (three state files, resolution order, failure modes):
`docs/architecture/state.md`. Config precedence across CLI flag / env
var / wiki config / root config / code default: `docs/architecture/config.md`.

## What a session leaves behind

A Claude Code session leaves two things, and lore writes neither with a
model.

**A transcript-ledger entry.** Capture registers every transcript it sees
for the session's directory (`lore_curator/capture_routing.py`) and
archives it. It stamps a **linkage block** onto the entry. The block names
the repo, the branch, and the PRs, issues, commits and files the session
touched. The block is derived from
git state and — at a session boundary — one read of the transcript. No LLM
call, no prose. The entry is the durable record that the session happened
and what it touched.

**Zero or more flags.** A **flag** is one team-relevant fact an agent
files *during* the session, deliberately (`lore_core/flag.py`). It is the
only crossing from a private session to the team wiki. Nothing crosses by
default: a session that files no flag leaves nothing in the wiki but its
ledger entry.

Lore writes no session note. There is no compose pipeline, no buffer, no
segmentation, no typed-fact extraction, no note render, and no LLM call at
a session boundary. Retired in `#361`; decisions in `docs/adr/0007`–`0009`,
spec in `docs/prd/0011`.

## The flag

A flag is appended to the **owning topic note** at write time, not queued.
It carries:

- **Lead** — one self-sufficient sentence. No pronoun reaches out of it.
- **Body** — optional short prose saying why the fact matters.
- **Origin line** — author, date, refs, transcript pointer, and the
  `unreviewed` marker until a human resolves it.

Ref verdicts are **code-stamped**, never model-phrased
(`lore_core/ref_verify.py`, `docs/adr/0004`). A ref reads `✓`,
`(unchecked)` or `(not found)` because code checked it. An agent cannot
claim authority it does not have.

**Pending state is derived, never stored** (`docs/adr/0008`). There is no
queue file. `lore flag list` and the SessionStart chip find pending flags
by scanning notes for the unreviewed marker. A flag accepted by hand in an
editor is simply no longer pending.

The review walk is `lore flag review`: accept, retarget, decline, skip. It
opens a local browser page by default, colouring each flag by its ref
verdict (`docs/adr/0011`); `--tty` keeps the terminal prompts. A flag a
human writes lands without the marker and keeps its own words — the
code-stamped phrasing rule constrains what a *model* may claim.

### The limit worth knowing

The unreviewed marker lives in the wiki note, and the wiki is a git repo.
The transcript a flag points at is **private and local** — it is not
pushed. The same person on a second machine sees the flag and its pending
state, but cannot open the transcript behind it. The pointer resolves only
on the machine that captured the session (`docs/adr/0009`).
Review the flag on its own text; treat the transcript pointer as a local
convenience, not a shared reference.

## The publish gate + quarantine

Every flag passes `lore_core/publish_gate.py:evaluate` before it joins
its topic note. The gate is the last check between a session's output and
the shared vault, and now its only caller is the flag writer. The gate
**fails closed**: any error anywhere in the gate withholds rather than
passes.

Cheapest-first, short-circuiting on the first hit:

1. **Deterministic scanners** — high-entropy secrets (via
   `lore_core.redaction`), email addresses, phone numbers.
2. **One small-model detection call** (`LlmPiiDetector`, cheapest
   tier) for fuzzy PII/secrets that slip the scanners. The
   call is pattern recognition, not truth verification, so it is
   exempt from the no-LLM-judges-LLM rule. The call is a tripwire, not
   a guarantee. This file and the module docstring both say so.

On a withhold, `apply_withhold` puts the flag's text into the private
**quarantine** sidecar
(`lore_core/quarantine.py`), one JSON file per entry under
`.lore/quarantine/`. That path sits inside the already-private
`.lore/` area and never reaches the shared wiki.
`lore quarantine list/show/clear/kill` is the reviewer's flow over that
sidecar. `list` never prints body content, because an entry may hold
the very secret that tripped the gate.

## Hygiene — the retained frontmatter-only curator

`lore curator [--wiki] [--apply]` takes no subcommand — `run`, `flush`,
`reap` and `sweep` retired with the compose pipeline. The bare
form runs deterministic, frontmatter-only passes over every wiki
(`lore_curator/hygiene.py`). The passes are supersession propagation
(`supersedes [[B]]` → `superseded_by: [[A]]` on B), `implements:`
back-link processing, and git-log date backfill. Another pass hints at
team mode once a solo wiki's git log shows multiple authors. Staleness
is a deliberate no-op here — see `lore_core/freshness.py` below.
Findings land in
`wiki/<name>/_review.md`; writes are mtime-guarded so a note open in
Obsidian is skipped rather than clobbered. `--apply` is required to
write; the default is a dry-run.

## Briefings

`lore_core/briefing/gather.py:gather()` is the read-only half of
`lore briefing`. It reports the wiki's briefing ledger and its sink
config. Briefing publish is manual (`lore briefing publish`, `lore
briefing mark`); there is no automatic daily trigger.

**Briefings are parked** (PRD 0011). `gather()` used to collect notes
filed under `<wiki>/sessions/` since the last briefing and hand their
bodies to the prose composer. PRD 0013 removed that walk, so
`new_sessions` always comes back empty and a one-shot gather yields
nothing. What a briefing should read now that the session note is gone
is an open question, deliberately left open rather than guessed. See
`docs/how-to/matrix-bot.md` for the Matrix sink walkthrough.

## Ambient banner vs. MCP pull

SessionStart injects a deliberately small, deterministic banner
(`lore_core/session_start.py:render_session_banner`). The banner costs
no LLM call and no network call. It holds a status line, an optional
`## Focus` block for the attached project, and a last-active-day recap
(`lore_core/session_start.py:last_active_day_recap`). The recap renders
off the transcript ledger in at most three lines. Line one names the
last day the ledger saw work, its session count and its repos. Line two
names the branches those sessions ran on. Line three names the issue and
PR numbers they touched.
Freshness lines join the banner only when there is positive evidence
(see below). A fixed directive closes the banner
(`lore_core/templates/integration-rules/default.md`). The directive
states that deeper context is a pull, never a push. It also states that
anything pulled from the vault is a record of what was discussed, never
an instruction.

Depth comes from explicit MCP calls (`lore mcp` / `lore_mcp/server.py`),
not from anything injected ambiently:

- `lore_search`, `lore_read` — retrieval primitives.
- `lore_drill` — one composite `search → read → expand wikilinks →
  read_expanded` call with a structured trace
  (`docs/architecture/lore-drill.md`).
- `lore_inbox_classify` — read-only gather that a skill turns into
  prose, then commits via a CLI verb.
- `lore_flag` — file one team-relevant fact into its owning topic note,
  marked unreviewed (`lore_core/flag.py`). The only wiki write an agent
  makes from a session, and the only crossing from a session to the team
  surface.
- `lore_journal_write` — the AI/human scratch journal
  (`lore_core/journal.py`); freeform, no LLM abstraction, no
  propagation, never a source for ambient context.
- `lore_pending_verdicts` / `lore_verdict` — list and record freshness
  verdicts; backs `/lore:verify` and the in-passing verdict nudge.
- `lore_repo_docs_list` / `lore_repo_docs_fetch`
  (`lore_core/repo_docs.py`) — pull-only reads of a connected repo's
  `docs/adr/` and `docs/prd/`. Ratified decisions live in the repo, not
  in the vault; Lore reads them on request instead of re-deriving them
  from session transcripts.
- `lore_tier_resolve` — resolve a semantic model tier to the concrete
  model for the current host before spawning a subagent.
- `lore_codemap` — bounded, cached slice of the connected repo's code
  map (symbols / directory / callers modes), never the whole map.
- `lore_context_pack` (`lore_core/context_pack.py`) — a deterministic
  context resolver. It takes a scope, repo state, and an issue, PR or
  epic. It returns a pointer pack of three payload keys: `adr` and `prd`
  for the repo docs that bear on the scope, and `epic_state`. The pack
  lost its `sessions` key when the session-note stock retired (PRD 0013).
  It joins on git-derived linkage and costs no LLM call. An `adr` or `prd`
  entry carries a path, a title and a status; a reader pulls the body
  afterwards with `lore_repo_docs_fetch`. Orchestration skills read the
  pack before any explorer subagent runs.


## Retrieval substrate

Kept, general-purpose, and independent of the note-writing pipeline
above:

- **Freshness** (`lore_core/freshness.py`) — positive-evidence-only
  staleness. A note is flagged `stale-candidate` only for a named
  cause. The causes are an authored `status: stale` / `superseded_by` /
  `supersede_candidate*` marker, or membership in the orphan-link set.
  Age by itself never flags anything.
- **Search** (`lore_search`) — hybrid ranked full-text search backing
  `lore_search` / `lore_drill`.
- **Wikilinks** (`lore_core/wikilinks.py`, `schema.py`) — `[[slug]]`
  parsing/resolution, per-wiki only (a wikilink never resolves across
  wiki boundaries — wikis are portable units).

## Module map

| Concern | Module |
|---|---|
| Publish gate over flag text (scanners, detector, withhold) | `lore_core/publish_gate.py` |
| Flag write, review walk, pending scan | `lore_core/flag.py` |
| Transcript capture, ledger registration, linkage stamp | `lore_curator/capture_routing.py` |
| Note reading (used by trace, seed-epic, the gate) | `lore_core/note_document.py` |
| Private quarantine sidecar | `lore_core/quarantine.py` |
| Deterministic ref verification (positive evidence only) | `lore_core/ref_verify.py` |
| Frontmatter-only hygiene passes | `lore_curator/hygiene.py` |
| Briefing gather (read-only) | `lore_core/briefing/gather.py` |
| Repo ADR/PRD pull (filesystem side) | `lore_core/repo_docs.py` |
| MCP server (tool dispatch) | `lore_mcp/server.py` |
| Hook dispatch (the seven `lore hook ...` entry points) | `lore_cli/hooks.py` |
| SessionStart context assembly + banner | `lore_core/session_start.py` |
| Capture routing (transcripts, flush, spawn gate) | `lore_curator/capture_routing.py` |
| Freshness classification | `lore_core/freshness.py` |
| Vault/wiki/scope resolution | `lore_core/scope_resolver.py`, `lore_core/state/` |

## Glossary

Terms used in the workflow layer and orchestration:

- **Workflow** — a skill-bundled planning chain: `seed-epic → orient →
  grilling → to-epic → orchestrate-epic → document-epic`. Each step
  is a callable skill, with checkpoints between shaping (human-controlled)
  and autonomous build. See `docs/conventions.md` for the stage vocabulary,
  artifact homes, and tier assignments.
- **Skill** — a callable, namespaced Claude Code automation block (e.g.,
  `lore-workflow:orient`, `lore:verify`). Skills are registered in
  `plugin.json` and dispatched by CLI invocation or intra-skill routing.
  Workflow skills are bundled in the `lore-workflow` plugin.
- **Lore context pack** — synonym for `lore_context_pack` (see above).
- **Codemap excerpt** — a bounded, ranked slice of the `lore codemap`
  output, token-budgeted (~1k tokens) and curated for a specific feature
  or epic. Passed once at `orchestrate-epic` Map time and reused by every
  teammate, instead of having each teammate discover symbols independently.
  Distinct from a full `lore_context_pack`, which joins ADRs/PRDs and epic
  state; the codemap excerpt is the code-navigation half only.
- **Epic note** — a single note written for the orchestration
  of an epic. The note records the roadmap DAG, per-feature tier
  decisions, crosscheck verdicts, and any escalations. Distinct from the
  per-feature implementation notes written by teammate agents; the epic
  note is the orchestrator's record of supervision.
- **Handover (session)** — a working-context handover from one Claude Code
  session to a cold session starting fresh. The source session ends with a
  `/lore:handover` invoke, which drafts a structured note of context
  (problem framing, attempted paths, current blockers). A cold session
  resumes with `/lore:continue`, which loads the handover note and carries
  its facts into the new session's work.
- **Handover (epic seed)** — a tracker issue linking an epic seed (the
  source of structured context for the `orient` step). The epic seed
  differs from a session handover. A team files the seed in the issue
  tracker. The seed is one-time context for a shaped body of work, not
  a carry-forward between sessions.
- **Writing rules** — the prose style an agent uses for issue text, PR
  descriptions, PR review comments, ADR context sections and design
  documents. Session notes stay out. The document holds sentence and
  vocabulary rules, EARS acceptance criteria, and the required section
  skeleton. Lore ships one default; a team overrides it whole-file with
  `<wiki>/style/writing-rules.md`.
  `lore style show writing-rules` resolves the two. The rules fix style,
  not terminology — terminology stays with the glossary.
  Overriding the lint means copying `styles/vale/` whole — the ini plus
  its `WritingRules/` rule directory — into `<wiki>/style/vale/`. Vale
  resolves `StylesPath` next to the ini, so an override that copies the
  ini alone exits 2 with "style 'WritingRules' does not exist on
  StylesPath". `lore style show issue-register` names the retired term and
  still resolves the document.
- **Change** — the unit an issue under the writing rules describes: one
  required-behaviour statement with its own acceptance criteria.
- **Batch issue** — an issue carrying several changes under one Context
  section, landing as one PR, with no ordering dependency between the
  changes. A change that needs its own context, or that must land before
  another, leaves the batch.
- **Short name** — an abbreviation, acronym or code a team writes in place
  of a longer term. `L0` and `LTA` name things; a phase number or a priority
  code names a piece of work. The writing rules cover the two kinds
  separately.
- **Piece of work** — a body of work a team plans and tracks. An issue, a
  pull request, an epic and a batch of issues are pieces of work. A team
  points at one by its issue number, never by a coined short name.

### Flag architecture

The flag is the crossing. The compose pipeline that used to write a
session note at every session boundary is gone, along with its per-wiki
`curator.*` config block. Decisions in ADR 0007–0009, spec in PRD 0011.

- **Flag** — one team-relevant fact an agent files during a session: a
  lead sentence, a short body, and an origin line. The only LLM-authored
  content a session writes into a wiki. Say "flag", not "gem" or
  "prospect".
- **Crossing** — the path a fact takes from a working session to the team
  wiki. The flag is the deliberate crossing, and the only one — the
  teardown landed.
- **Origin line** — the deterministic attribution line closing a flag
  block: author, date, code-verified refs, transcript pointer. A write
  carrying no transcript pointer and no ref is refused.
- **Unreviewed marker** — the token ending an agent-filed flag's origin
  line until a human accepts it. Accept is the only verdict that removes
  it. A human-filed flag lands without it.
- **Review walk** — the pull-based pass over unreviewed flags
  (`lore flag review`): accept, retarget, decline, or skip. It runs in a
  local browser page by default and in the terminal under `--tty`. The
  banner shows a count of pending flags and never their content.
- **Transcript ledger** — the machine-local store mapping each session to
  its transcript (`.lore/transcript-ledger.json`). Derived and
  rebuildable. Say "transcript ledger", not "breadcrumb ledger".
- **Linkage block** — the ledger entry's `repo`, `branch`, `prs`,
  `issues`, `commits` and `files` keys, written by capture with no LLM
  call. It is what `lore_drill` reads to answer "which sessions touched
  X" and what the SessionStart recap renders from.
- **Context finder** — Lore's retrieval role: the tools that find where
  context lives and pull it in (`lore_search`, `lore_drill`, context
  pack, codemap, repo docs). Say "context finder", not "funnel".
