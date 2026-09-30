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
  `inbox/`. Lore writes nothing into a wiki automatically. A human,
  `/lore:inbox`, or an agent's pull request writes it, when something is
  worth keeping.
- **Scope** — a colon-separated namespace inside a wiki
  (`ccat:data-center:data-transfer`), resolved from a working
  directory by longest-prefix match against `.lore/attachments.json`.

Full model (three state files, resolution order, failure modes):
`docs/architecture/state.md`. Config precedence across CLI flag / env
var / wiki config / root config / code default: `docs/architecture/config.md`.

## What a session leaves behind

A Claude Code session leaves a transcript-ledger entry in the vault, and
lore writes it without a model. Every fact worth keeping leaves as a repo
artifact instead (see the **filing rule** in the glossary).

**A transcript-ledger entry.** Capture registers every transcript it sees
for the session's directory (`lore_curator/capture_routing.py`) and
archives it. It stamps a **linkage block** onto the entry. The block names
the repo, the branch, and the PRs, issues, commits and files the session
touched. The block is derived from
git state and — at a session boundary — one read of the transcript. No LLM
call, no prose. The entry is the durable record that the session happened
and what it touched.

Lore writes no session note. There is no compose pipeline, no buffer, no
segmentation, no typed-fact extraction, no note render, and no LLM call at
a session boundary. Retired in `#361`; decisions in `docs/adr/0007`–`0009`,
spec in `docs/prd/0011`.

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

## Ambient banner vs. MCP pull

SessionStart injects a deliberately small, deterministic banner
(`lore_core/session_start.py:render_session_banner`). The banner costs
no LLM call and no network call. It holds a status line, an optional
`## Focus` block for the attached project, and a last-active-day recap
(`lore_core/session_start.py:last_active_day_recap`). The recap renders
off the transcript index in at most three lines. Line one names the
last day the index saw work, its session count and its repos. Line two
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
| Transcript capture, ledger registration, linkage stamp | `lore_curator/capture_routing.py` |
| Note reading (used by trace and seed-lift) | `lore_core/note_document.py` |
| Deterministic ref verification (positive evidence only) | `lore_core/ref_verify.py` |
| Frontmatter-only hygiene passes | `lore_curator/hygiene.py` |
| Repo ADR/PRD pull (filesystem side) | `lore_core/repo_docs.py` |
| MCP server (tool dispatch) | `lore_mcp/server.py` |
| Hook dispatch (the seven `lore hook ...` entry points) | `lore_cli/hooks.py` |
| SessionStart context assembly + banner | `lore_core/session_start.py` |
| Capture routing (transcripts, flush, spawn gate) | `lore_curator/capture_routing.py` |
| Freshness classification | `lore_core/freshness.py` |
| Vault/wiki/scope resolution | `lore_core/scope_resolver.py`, `lore_core/state/` |

## Glossary

Terms used in the workflow layer and orchestration:

- **Workflow** — a skill-bundled planning chain: `orient → grilling →
  to-epic → build → document`, with `handover` at any stop. Each step is a
  callable skill. The human checkpoint sits between shaping and the build.
  See `docs/conventions.md` for the stage vocabulary and tier assignments.
- **Skill** — a callable, namespaced Claude Code automation block (e.g.,
  `lore-workflow:build`, `lore:verify`). Skills are registered in
  `plugin.json` and dispatched by CLI invocation or intra-skill routing.
  Workflow skills are bundled in the `lore-workflow` plugin.
- **Lore context pack** — synonym for `lore_context_pack` (see above).
- **Codemap excerpt** — a bounded, ranked slice of the `lore codemap`
  output, token-budgeted (~1k tokens) and curated for a specific feature
  or epic. Built once at the Map step of `build` in `epic` mode and reused
  by every teammate, instead of having each teammate discover symbols independently.
  Distinct from a full `lore_context_pack`, which joins ADRs/PRDs and epic
  state; the codemap excerpt is the code-navigation half only.
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

### Filing and retrieval

Agents write nothing into the wiki on their own. Decisions in ADR 0012
and 0013, spec in PRD 0014.

- **Filing rule** — the rule saying which artifact holds each kind of
  fact. The artifacts are an issue, a comment, a dead end closed as
  not-planned, an ADR as a PR, and a topic-note edit as a PR. Say
  "filing rule", not "crossing".
- **Agent-filed** — an issue, comment or PR an agent created. The
  `agent-filed` label and the opening line carry the mark.
- **Retrieval miss** — a fact a Lore tool did not return, that the agent
  then found by reading files or running commands. An agent files each
  miss as an issue on the Lore repo when the user opts in.
- **Federated search** — one `lore_search` call that returns two lists.
  The first holds wiki notes from the local index. The second holds
  issues and PRs from a live GitHub search. Nothing is stored.
- **Transcript copy** — a Claude Code transcript Lore copied into a
  wiki's `.transcripts` folder. Machine-local, never pushed.
- **Transcript index** — the list of captured transcripts with what each
  session worked on (`.lore/transcript-ledger.json`). Derived and
  rebuildable. Say "transcript index", not "transcript ledger" or
  "breadcrumb ledger".
- **Linkage block** — the transcript index entry's `repo`, `branch`,
  `prs`, `issues`, `commits` and `files` keys, written by capture with no
  LLM call. It is what `lore_drill` reads to answer "which sessions
  touched X" and what the SessionStart recap renders from.

### Decision records and build

Decisions in ADR 0014 and 0015, spec in PRD 0015.

- **Home** — the one artifact that holds a kind of fact. Every other
  artifact links to the home and does not restate the fact. ADR 0014
  holds the table of homes.
- **Holds** — the section of an ADR that states how strong each part of
  the decision is. Every line is an invariant, a default or an
  incidental. Text outside the section is background.
- **Invariant** — a Holds line that a named test enforces. Only an
  invariant binds an agent. A line without a test is not an invariant.
- **Default** — a Holds line an agent follows unless the task gives a
  reason to deviate. The agent names the deviation and the reason in
  the PR.
- **Incidental** — a Holds line that records how the team built the
  decision at the time. An agent changes it freely.
- **Amendment** — a dated entry in an ADR's Amendments section that
  relaxes or sharpens a Holds line. A reversal of the whole decision
  takes a new, superseding ADR instead.
- **Shipped PRD** — a PRD whose epic merged. Its status is `shipped` and
  its first line points at the ADRs that stay current. Retrieval ranks it
  below ADRs and never injects it.
- **Ledger** — the working list of one build run: ADR candidates, term
  candidates, left-on-the-table items and resume lines. The ledger of
  `loop` and `issue` mode lives in the worktree's git directory. The
  ledger of `epic` mode lives in the board comment.
- **Ledger check** — `lore workflow ledger-check`. It blocks a finish
  point while a ledger line has no outcome: approved, dropped or filed.
- **Finish point** — the step where a build run ends: loop wrap-up, the
  `issue` PR, or the epic tail.
- **Resume line** — a ledger line holding the run's state and the next
  ask. The agent writes one after each merged round or feature.
- **Breakpoint** — a moment after a resume line where the agent tells
  the user that `/clear` or a compaction loses nothing.
- **Build mode** — one of `loop`, `issue` and `epic`, the three shapes of
  the `build` skill. `loop` is human-present rounds with local merges.
  `issue` takes one issue to one PR. `epic` runs a roadmap with
  teammates.
- **Risk level** — `low` or `high`, from `lore workflow risk <pr>`. The
  level sets the review depth. An agent raises the level and never
  lowers it.
- **Handover section** — the `## Handover` section of an epic issue or of
  a build PR body. It names what
  shipped, the decisions, the deviations from the PRD, the follow-ups and
  the known limits. `build` writes it at the finish point. The `handover`
  skill writes it for work that stops early.
- **Seed issue** — a tracker issue labeled `epic-seed` that the `handover`
  skill writes. It carries intent and findings for a cold session to
  orient on. It is no spec.

- **Context finder** — Lore's retrieval role: the tools that find where
  context lives and pull it in (`lore_search`, `lore_drill`, context
  pack, codemap, repo docs). Say "context finder", not "funnel".
