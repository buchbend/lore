# Changelog

All notable changes are recorded here. The version in this file mirrors
`pyproject.toml` and `.claude-plugin/plugin.json` — bumping the package
version is what makes `claude plugin update lore@lore` re-fetch.

Format: [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).
Versioning: [Semantic Versioning](https://semver.org/spec/v2.0.0.html)
(0.x means anything can change between minor versions until 1.0).

## [0.63.0] - 2026-07-12

Typed-fact session notes (PRD 0008, epic #282). All LLM work moves to session
end, and everything after extraction is deterministic. Notes used to be composed
*forward*, per flush, as prose chapters — so they recorded the **working** rather
than the **work**: process narration, interim states that later turned out false,
and model-authored phrasing that the next session ingested as authority. That last
one is the real hazard: circular context poisoning.

### Added

- **Logical chunker** (`lore_curator.chunker`) — at session end a cheap model reads
  a collapsed transcript view and returns **turn indices only**. Deterministic lints
  normalize them (monotone, in range, size band); oversized transcripts are windowed
  and stitched; any model failure degrades to fixed-size windows. The model's entire
  output surface is a list of integers — `Chunk` is a frozen two-int dataclass, with
  nowhere to put prose.
- **Typed-fact extraction** (`lore_curator.fact_extract`) — one call per chunk emits
  `Fact(kind, text, anchor_turn, thread, refs, why, quote)`, `kind` being one of
  `progress | done | decision | finding | open`. Three deterministic lints
  (anchor-in-chunk, kind-enum, decision-without-why) each earn exactly one corrective
  retry. The tool schema has **no quote field**: quotes are code-attached from the
  anchor turn, so the model cannot author the verbatim evidence for its own claim.
  Terminal-state rule — commits, PRs and verified-green states are `done`; edits en
  route are `progress`.
- **Deterministic note renderer** (`lore_core.note_document.render_note`) — the note
  body is written **once**, at close, as a pure function of the ledger: headline, then
  **Done**, **Decisions recorded**, **Findings**, **Open**. Items sort by anchor, empty
  sections drop. A `progress` fact is suppressed when its thread carries a later
  terminal fact — at render time only; the fact stays in the ledger. Failed chunks and
  any non-`facts` chapter render as one-line coverage gaps, so a partial note can never
  present itself as complete. No LLM call anywhere in the render path.
- **Ref verification + epistemic stamping** (`lore_core.ref_verify`) — commits, tags and
  files are verified against local git and the session's frontmatter facts; PRs and
  issues best-effort via `gh`, stamped `(unchecked)` when unreachable and **never**
  silently promoted. Phrasing templates are keyed on `(kind, verification)` and owned by
  code, so a hallucinated ref **demotes to hedged phrasing instead of acquiring
  authority**. A ref-less `decision` routes to **Open** as "Agreed in discussion,
  recorded nowhere". Offline never fails a render.

### Changed

- **`session-end` is now the only flush.** `capture_routing.CLOSE_TRIGGERS` is the single
  authority for which trigger flushes. Cap-trip and pre-compact **bookkeep only** — the
  buffer keeps accumulating, no chapter is appended, no model is called — so the close
  path reads the session whole. Session-end's unconditional drain is preserved: a buffer
  that trips the cap and keeps growing still drains the entire session at close.
- Nothing is written while a session runs. The note appears once, complete.

### Removed

- **`lore_curator.chapter_compose`** and the whole mid-session prose path (`synth_in_place`,
  `note_so_far`, `_slice_text`, `_apply_composed`, `_apply_withheld`). Forward composition
  is gone; notes are rendered from the ledger.

### Security

- Model and transcript content reaching a note body is marker-neutralized. A literal
  `<!-- lore:fact ... -->` string in fact text, `why`, a quote, a ref value, a prose
  chapter body or the headline would otherwise parse back as a **forged fact** carrying an
  attacker-chosen `kind`/`refs` and a **self-authored quote**, and an unclosed opener would
  **swallow the next legitimate fact**. Reachable with no model complicity, since the
  extraction view carries tool payloads. `_neutralize_marker` escapes the opener on every
  body-reaching path.
- A hallucinated `file` ref can no longer buy authority. Verification now requires presence
  in the session's captured files, or a path resolving inside the repo that is a regular file
  **and** tracked by git; traversal and directories are rejected. Previously any path that
  merely existed on the machine — `/etc/passwd`, `../../../etc/hosts`, `/tmp` — verified.
- PR and issue existence is checked with `gh ... --json state`, never `--json number`: `gh`
  answers `number` from the argument itself without contacting GitHub, so every fabricated PR
  number used to earn a check mark. Issue numbers scraped from agent-authored commit messages
  are no longer treated as existence evidence — `gh` is the sole oracle, and `UNCHECKED` is
  the correct degradation.

### Decisions

- **ADR 0003** — the note body is a deterministic render of the ledger. Append-only applies to
  the ledger; the rendered body is derived state (extends ADR 0001's carve-out).
- **ADR 0004** — authority phrasing is code-stamped from verifiable refs, never model-authored.
  The context-poisoning defense that constrains all future note features.

## [0.62.0] - 2026-07-12

Substrate trim (PRD 0007): folds redundant CLI groups into their parents and
drops MCP tools with no caller anywhere. No loss of capability — every
removed surface has a direct replacement below.

### Removed

- **`lore log` / `lore news` / `lore runs` / `lore proc`** — the aliases
  0.61.0 deprecated are now gone outright; `lore trace` / `lore status`
  fully absorbed their role.
- **Five no-caller MCP tools**: `lore_index`, `lore_catalog`,
  `lore_wikilinks`, `lore_journal_read`, `lore_briefing_gather`. 13 tools
  remain exposed (`lore_mcp/server.py` is the authoritative list).
- **`lore registry`** — folded into `lore scopes wikis` / `lore scopes
  doctor`.
- **`lore attachments`** — folded into `lore attach attachments`.
- **`lore detach`** — folded into `lore attach remove`; its `--json`
  envelope schema is renamed `lore.detach/1` → `lore.attach.remove/1`.
- **`lore curator --migrate-open-items`** and **`lore curator
  backfill-slugs`** — moved to `lore migrate open-items` / `lore migrate
  slugs`. The frontmatter one-shot flags (`--add-schema-version`,
  `--minimal-status`, `--strip-broken-wikilinks`) moved from bare `lore
  migrate` to `lore migrate frontmatter`.
- **`lore completions`** — superseded by Typer's native
  `--install-completion` / `--show-completion` on the root command.

### Changed

- **`lore journal`** no longer appears in `lore --help` — it's a parked,
  off-by-default feature now mounted `hidden=True`. Still fully invocable
  (`lore journal --help` works) for anyone who already turned it on.

## [0.61.1] - 2026-07-12

Epic #183 polish (#255).

### Fixed

- **`flush-spawn-failed` events now carry a `trace_id`** — the trace-id is
  minted before the spawn lock is taken, so both spawn-failure paths (the
  detached subprocess failing to start and the spawn-lock flock erroring)
  emit a traceable event that `lore trace` can correlate with the rest of
  the flush's story. Previously these events landed on the spine with
  `trace_id: null`.
- Stale `_system.jsonl` wording in a `hooks.py` docstring updated to name
  the shared `_system` spine stream; the now-fixed known-gap passages were
  removed from the troubleshooting and observability docs.

## [0.61.0] - 2026-07-11

Epic #183 — onboarding, connection config & observability revamp (PRD 0005).
Replaces three fragmented surfaces — first-run onboarding, write-once connection
config, and a seven-surface observability CLI — with one event spine, three
commands, one idempotent wizard, and writable validated config.

### Added

- **Event spine** (`lore_core.spine`) — one append-only JSONL family under
  `.lore/` behind a single mandatory envelope `{ts, v, source, event, level,
  trace_id, session_id, run_id, wiki, scope, error_code, data}` with a closed
  `ErrorCode` enum. O_APPEND-atomic (≤ PIPE_BUF) appends with flock-guarded
  rotation; a spine write failure degrades to a marker and never blocks the hook.
  Hook events, curator run logs, and drain telemetry all flow through it.
- **`lore trace`** — correlated drill-down of one flush, selected by trace-id,
  session-id, `last`, `dead`, or note path/wikilink: a chronological tree of the
  flush's spine events with per-step durations, highlighted error codes, and the
  flush's state-machine status.
- **`lore status` v2** — one glanceable health dashboard (capture, flushes,
  wikis, retention, news, alerts); per-wiki connection health with a time-boxed
  network probe and `--offline`; exit code mirrors alerts. Absorbs `lore news`.
- **`lore doctor --fix`** — state repair: rebuild `scopes.json` from accepted
  attachments, re-stamp drifted offer fingerprints, and migrate attachment paths
  after a vault/repo move (each repair shown before it runs and individually
  declinable). Plugin-cache drift is now a failing check.
- **`lore config get/set/unset/edit`** — writable, schema-validated config for
  root and `--wiki`; unknown keys and bad values are rejected naming the nearest
  valid alternatives; `edit` validates on editor close.
- **`lore init` wizard** — one idempotent, resumable guided path (vault → wiki →
  integrations → optional first attach → automatic doctor → handoff), with full
  flag parity (`--yes`, `--vault`, `--wiki-new|--wiki-clone|--wiki-link`,
  `--attach`, `--plain`). `_scopes.yml` is scaffolded with a commented example.
- **trace_id propagation** — minted at hook fire, carried via env to the detached
  curator, stamped on every run/drain/flush event and the published note's
  linkage frontmatter; concurrent flushes stay separable.
- **Flush lifecycle state machine** (`lore_core.flush_store`) — a persisted
  per-flush record `queued → running → published | withheld | dead-lettered
  (reason)` with an attempt counter and bounded backoff retries; formerly-silent
  failure paths (sidecar read, spawn, chapter append) now emit events or dead
  letters, and in-flight flushes are listable.
- **Unified retention janitor** (`lore_core.janitor`) — one flock-guarded,
  daemon-free, tiered (hot/cold + size caps) retention pass over the whole spine,
  driven by config (`observability.retention.*`); deletions are logged, delete
  failures warned, and unresolved dead letters are preserved.
- **Attach/offer hardening** — `.lore.yml` schema validation at attach time,
  `lore attach offer --dry-run`, `lore attach manual --write-offer`, declines
  keyed on `(path, scope)` instead of content fingerprint, a `lore scopes rename`
  stale-offer report, and an in-session offer-drift warning.
- **`docs/architecture/observability.md`**, **`docs/how-to/onboarding.md`**, and
  **`docs/how-to/troubleshooting.md`** — the event spine's envelope schema,
  producer list, trace_id lifecycle, retention tiers, and the three-command
  surface; install → `lore init` → first attach → verify; and a symptom-driven
  escalation guide. `session-note-lifecycle.md` gained a "How failures surface"
  section tying note-level marker chapters to the spine-level dead-letter state
  machine.

### Changed

- `lore install`'s interactive vault setup now delegates to the single `lore
  init` wizard — exactly one onboarding path. `install.sh` exits non-zero when
  `lore` is not runnable on PATH afterwards and chains into `lore init` on first
  install.

### Deprecated

- **`lore log`, `lore news`, `lore runs`, `lore proc`** are now thin aliases for
  `lore trace` / `lore status` — each prints a one-line pointer to its
  replacement on stderr, then still runs its original behavior. Kept for one
  minor release; will be removed in `0.62.0`.

### Removed

- **`lore drain prune`** (and the now-empty `lore drain` command group).
  Vestigial since drain-event emission moved onto the spine: nothing has written
  `.lore/drain/_system.jsonl` since, and spine drain events already get the same
  tiered retention as every other family. `lore_core.janitor.prune_orphans`
  (moved from `lore_cli.drain_cmd`) still runs automatically on every
  opportunistic janitor pass — no functionality lost, just the redundant manual
  trigger.
- The legacy `hook_log`, per-session run-log (`runs/<id>.jsonl` + `runs-live.jsonl`
  tee + `.trace.jsonl`), and per-session drain writers — all replaced by the
  spine.

## [0.60.0] - 2026-07-11

Epic #229 — workflow lightening & deepening on the lore substrate (PRD 0006).
Rewires the workflow skills to consume the deterministic substrate instead of
re-deriving state in prose.

### Added
- **`lore workflow` mechanics** — `epic-policy` (per-repo `{target_branch,
  deploy_gate}` resolved from git branch state + `AGENTS.md` markers),
  `validate-roadmap --json` (emits `{rows, repos, edges}` counts), `parse-board`
  (deterministic parser for the machine-readable supervision-board comment), and
  `seed-lift` (lifts a seed's Origin/Findings from the current session note).
- **`LORE_SUPPRESS_CAPTURE`** — env flag that makes SessionEnd/PreCompact capture
  a no-op, so dispatched teammate sessions leave no scattered notes; the default
  (unset) path is unchanged. Documented in `docs/architecture/config.md`.
- **Note-format v2** — session-note title is scope-prefixed (`scope: name`) and the
  body opens with an inline bold lead sentence; specified in `CONTEXT-FORMAT.md`.
- **ADR 0002** — supervision-state split: board on the GitHub issue, orchestrator
  working context on its own composed epic note, same-session writes only.

### Changed
- **`orchestrate-epic`** — put on a prose diet (293→192 lines): resolves
  target-branch/deploy-gate via `lore workflow epic-policy`, effort band via
  `validate-roadmap --json`, resume via `parse-board`; the homegrown "context
  pack" is renamed "codemap excerpt" (reserving "context pack" for the
  `lore_context_pack` MCP tool).
- **`orient` / `to-epic`** — fan-out is gated on `lore_context_pack`: the pack is
  pulled up front and a per-facet explorer spawns only when it comes back thin.
- **`seed-epic`** — Origin/Findings are lifted from the session note (freehand
  fallback preserved); the seed records a source-note reference.
- **`CONTEXT.md`** — glossary sync: `lore_context_pack`, codemap excerpt, epic note,
  the two handover senses, `workflow`, and `skill`; `TIER-DELEGATION.md` tier-choice
  review pass.

## [0.59.0] - 2026-07-10

Epic #162 — deterministic context, lights-out notes (PRD 0004).

### Added
- **Deterministic linkage frontmatter at capture** (`schema_version=1`): repo, branch,
  issue/PR/epic refs, files touched, commits, and author display name are extracted
  adapter-level with zero LLM/network cost and written on every session note.
- **`lore_context_pack` MCP tool**: given cwd/branch/issue, returns a bounded pointer
  pack (recent notes, co-touching ADRs/PRDs, open epic state) with relevance computed as
  a deterministic join on linkage keys — never an LLM call, never ambient injection. The
  front door planning skills call before spawning an explorer.
- **Compose hardening**: notes are built on a byte-stable deterministic skeleton with
  verbatim transcript quotes code-attached at their anchor turn; the model writes only
  connecting narrative. The publish gate stays fail-closed, and a usable skeleton note is
  produced even when the model call fails.
- **Display-name onboarding**: `lore init` captures `user.display_name`; note authorship
  and briefings use it instead of `$USER`.
- **Shared-vault sharing consent**: routing a scope to a shared (git-remote) wiki is an
  explicit opt-in that surfaces a consent prompt (team-visible; the gate reduces but does
  not eliminate leaks; a leaked secret persists in git history — remedy is rotation).
  `--confirm-shared` covers non-interactive callers.
- **Multi-author sync hardening**: collision-free note paths (author + session id) via an
  atomic `O_CREAT|O_EXCL` claim; lights-out fetch/rebase/retry under concurrent writers;
  unreachable-remote writes queue locally and recover on the next sync.
- **Briefings on linkage**: gather keys digests by linkage frontmatter (author, scope,
  epic, repos) rather than recency/FTS alone, with a drill-down chain
  (briefing → notes → ADRs/PRDs/issues → code). New `docs/architecture/briefing-compression-channel.md`.

### Changed
- Curator is now session-note compose (Curator A) only.

### Removed
- **Curator B (transcript decision curation) and Curator C (concept elevation)** deleted
  outright — code paths, CLI/MCP surfaces, and tests. Decisions enter context only through
  the ratified channel: ADRs/PRDs in connected repos, pulled on demand.

## [0.58.0] - 2026-07-09

### Added
- **lore-workflow plugin + deterministic substrate (PRD 0003, epic #161).** The
  ccat-agent-workflow plugin is folded into this monorepo as a second marketplace
  plugin, `lore-workflow`, with its deterministic underbelly absorbed into the `lore`
  package:
  - **CI**: GitHub Actions runs pytest (blocking) + ruff (advisory until #196) on push/PR.
  - **Two-plugin monorepo**: `lore-workflow` marketplace entry on an independent version axis.
  - **`lore codemap`**: gitignore-aware code-map generator — all-files inventory + ranked
    Python symbols (stdlib `ast`), git-blob-SHA fingerprint no-op fast path; multi-language
    symbols (JS/TS, Vue, Rust, Julia, HTML) via the optional `lore[codemap]` tree-sitter extra.
  - **`lore_codemap` MCP tool**: bounded, fingerprint-cached queryable slices of the code map.
  - **`lore tier resolve`**: host-keyed model-tier resolution (CLI + MCP), user-overridable.
  - **`lore workflow`**: roadmap validation + PRD scaffolding, ported into `lib/lore_workflow`.
  - **Spawn-model gate**: PreToolUse hook (Claude Code) blocking modelless subagent spawns,
    wired by the installer; retry guidance sourced from the tier resolver.
  - **Onboarding**: `lore attach --scaffold-workflow` idempotently scaffolds docs/prd, docs/adr,
    and the AGENTS.md shim — the standalone workflow-init path retired.
  - **13 workflow skills** ported into `lore-workflow` (verbatim + tier/codemap/script rewires,
    deduplicated tier reference); workflow conventions, tier explanation, and how-to guides
    migrated into lore's docs.


## [0.57.0] - 2026-07-06

### Fixed
- **One session, one note.** A same-host owner whose pid is gone is no
  longer judged dead outright — the reaper and startup sweep now treat it
  as uncertain and defer to the existing staleness threshold, instead of
  force-closing a session that is still running (the root cause of one
  session filing several unlinked, partly-duplicated notes). A resumed
  session whose buffer was archived reattaches to its own note instead of
  minting a new one: a `reopen_note` primitive reopens the closed note and
  the buffer restores from `_done/`, seeding the continued compose from the
  existing body so nothing is re-narrated. This relaxes "a closed session
  note is immutable" for session notes only (see
  `docs/adr/0001-session-note-reopen-relaxes-close-immutability.md`);
  derived/curated artifacts stay immutable.
- The composer no longer attributes material the user only pasted or
  quoted as an exemplar (formatting example, "an older version of", a
  fenced block with its own anchored leads) to the session's own work,
  unless working on that material was itself the topic.

### Added
- A soft same-anchor lint: when a chapter has more than two blocks all
  citing the same turn, one corrective retry nudges the composer toward
  distinct anchors. It never hard-rejects and never fabricates diversity —
  the chapter still publishes either way.

### Changed
- A note's filename is renamed to reflect its first composed lead once the
  first chapter lands, instead of staying pinned to the note-creation-time
  heuristic (commit subject / touched-file basename) for the note's whole
  life. Stub notes with no chapter yet, and same-minute collisions, keep
  the existing fallback/suffix behavior.

## [0.56.0] - 2026-07-04

### Changed
- The chapter-compose prompt is rebuilt around **essence extraction**: it
  records the work, not the working. Blocks are self-sufficient declarative
  claims — a finding, an outcome, or a gap stated as a fact — with the
  reasoning in an active-voice body. Session mechanics (greetings, test
  traffic, slash commands, sandbox/tooling hiccups) are excluded unless the
  tooling was the session's subject. Event narration ("The session was
  started") is banned.
- The publish gate is **safety-only**: the phrasing lint (and the 0.55.x
  soft-verdict plumbing) is gone. Voice is the prompt's job; PII/secret
  scanners and the optional detector still withhold.

### Added
- **No note is better than a noise note.** A compose may return zero blocks
  ("nothing of substance") — mid-session the judged span is consumed, and a
  session whose note never gained a chapter leaves no note at all. A
  deterministic trivial-session gate (≤8 turns, ≤4k chars, no file/commit/
  issue activity) discards the stub without spending an LLM call. The
  startup sweep reports these as `discarded`.

## [0.55.0] - 2026-07-03

### Removed
- Curator B (daily) and Curator C (defrag + its passes), the surface store/filer/CLI/MCP tools, the two-region renderer, and Part-N note splitting. Vault-lint, git-sync, scopes, and schema were decoupled from them.

### Changed
- Session capture is rebuilt around **lab-notebook notes**: one append-only note per session — chronological chapters of bold-lead topic blocks, one `@turn` anchor per block, a fixed genre disclaimer, never authoritative. Each chapter is composed in a single LLM call (the outline→compose two-call pipeline is gone).
- Briefings hand full note bodies to the composer.
- The SessionStart banner is trimmed to a minimal ambient signal (no issue/PR counts, one directive).
- `lore curator` keeps only its deterministic hygiene passes (supersession, implements-propagation, git-backfill, positive-evidence staleness).

### Added
- A blocking publish gate (deterministic PII/secret scanners + phrasing lint + optional fuzzy-PII detector) with a private quarantine sidecar and a `lore quarantine` review CLI; no chapter reaches a note without passing it.
- Failure semantics: silent mid-session defer, a 2x-cap give-up marker, and a singleton startup sweep of dead sessions.
- MCP pull tools that read a repo's `docs/adr/` and `docs/prd/` natively.

## [0.54.0] - 2026-05-18

### Changed — Curator A: two-call P2 (outline → narrative) session notes

Ports experiment 005's best-GPT-OSS-120B cell (P2, 0.804 mean / 0.964
hero) into Curator A's Phase 2. The single-call work/discussion-gated
schema is replaced by a two-call shape:

1. **Call A — `outline`**: 4-8 short outline items (≤8 words each, no
   grounding). Tight schema; ~3-5s on GPT-OSS-120B.
2. **Call B — `compose`**: expands the outline + transcript into
   `{title, summary_lede, narrative}`. The narrative is one markdown
   string with bold-led bullets, `@N` turn citations, optional sub-
   headings (`### Strategy / ### Detours / ### Outcomes`), and per-
   bullet epistemic prefixes (`Considered: / Leaning: / Tried: /
   Open:`) for tentative items.

Body shape gains a `## Narrative` section between `## Summary` and
`## Activity`. Frontmatter gains `outline:` as a retrieval breadcrumb.
The pre-P2 sections (`ADR candidates` / `What we worked on` /
`Discussion` / `Loose ends`) are no longer emitted; legacy notes
still round-trip through the parser.

Shape gating (`select_shape`, `NarrativeShape`,
`_coerce_title_for_shape`) removed from the call chain — kind
classification now lives in the per-bullet epistemic prefix.

**Tier caveat**: P2 is a GPT-OSS-class-or-better recipe. Mistral-119B
collapsed to 0.426 on this shape in experiment 005 (vs GPT-OSS 0.804)
because the narrative-string schema can't structurally block
`from_example` copy-paste. The 0.53.0 README's "Recommended openai-
backend setup" (GPT-OSS-120B + `reasoning_effort=high`) is the
intended production config.

**Orphans for follow-up cleanup**: `lib/lore_curator/adr_candidate.py`,
`lib/lore_core/narrative_kind.py`, `lib/lore_curator/summary_block.py`
are no longer imported by `synthesis.py`. Their tests still pass; a
follow-up PR can delete the modules.

**Tests**: new `tests/test_synthesis_p2.py` (10 tests covering
schemas, prompts, two-call dispatch, telemetry, empty-outline-
returns-None). `tests/test_synthesis.py` and
`tests/test_synthesis_narrative.py` skipped at module level —
they assert on the dead schema and need rewriting in a follow-up.
`tests/test_reasoning_effort_plumbing.py` adapted to the two-call
shape (helper routes outline vs compose by tool name).

## [0.53.0] - 2026-05-16

### Added — Curator A reasoning-mode narration (PRD #110, PRs 1–4)

Revamps the Curator A OpenAI-backend path so reasoning-capable models
(GPT-OSS-120B at `reasoning_effort=high`) can actually drive Phase 2
narration end-to-end. Previously, configured reasoning never reached
the wire and the 1024-token ceiling truncated the structured tool
response mid-stream once hidden reasoning tokens were in play.

- **PR 1 — `ResolvedModel` value object (#111)**: introduced a frozen
  `ResolvedModel(id, reasoning_effort)` in `lore_curator.llm_client`
  and routed `_resolve_openai_settings` through it. New
  `curator.openai.reasoning_effort_{simple,middle,high}` config keys
  and matching `LORE_OPENAI_REASONING_EFFORT_{TIER}` env-var overrides,
  with case-insensitive validation that raises `LlmClientError` naming
  both tier and bad value. Back-compat: `OpenAICompatibleClient`
  still accepts the legacy `dict[str, str]` shape.
- **PR 2 — plumb `reasoning_effort` end-to-end (#113)**:
  `_OpenAIMessagesAPI` now takes `dict[str, ResolvedModel]` directly;
  the down-conversion in `OpenAICompatibleClient.__init__` is gone and
  `create` drops its `**_extra: Any` so future silent-kwargs-drop
  regressions surface as a clean `TypeError`. Resolved
  `reasoning_effort` is forwarded as `extra_body={"reasoning_effort": ...}`
  on `chat.completions.create`; unset / literal pass-through omits
  `extra_body` for byte-identical opt-out. Telemetry: `LlmResponse`
  gains `reasoning_effort`; `synthesis.compose_session_note` enriches
  the `llm-response` event with `model_resolved` + `reasoning_effort`.
- **PR 3 — Phase 2 max output tokens (#112)**: bumped
  `PHASE2_MAX_OUTPUT_TOKENS` 1024 → 4000 — the smallest safe floor
  above the PRD's ≥4000 ask that covers reasoning payload + structured
  response on every cell of experiment 007 without inviting silent
  cost inflation. OpenAI SDK default 600s timeout comfortably covers
  the documented 80–100s GPT-OSS-120B latency; no explicit
  `LORE_OPENAI_TIMEOUT_S` knob added until a forcing function appears.
- **PR 4 — recommended openai-backend setup (#114)**: README gains a
  "Recommended openai-backend setup (Curator A narration)" subsection
  documenting the GPT-OSS-120B + `reasoning_effort=high` recipe that
  experiment 007 in lore-experiments showed cuts contradicted claims
  roughly in half (4 → 2 on the 4-cell sample), with latency caveat
  (80–100s vs ~10s) and cost note (both free on kiconnect.nrw).
  `docs/architecture/config.md` extended with the three
  `LORE_OPENAI_REASONING_EFFORT_{SIMPLE,MIDDLE,HIGH}` rows and the new
  `curator.openai` schema bullet.

Backwards-compatible: users on existing Mistral-119B / non-reasoning
configs see no behaviour change (no `extra_body`, no payload diff);
the recipe is opt-in via the documented config keys.

## [0.52.0] - 2026-05-12

### Changed — Streamlining track grandfather kill (issue #80, PRs 6a–6d)

Closes the 9-PR streamlining track of issue #80. Three autouse
"grandfather" fixtures in `tests/conftest.py` were pinning the test
suite to pre-production defaults (`LORE_BUFFER_FLUSH=0`,
`LORE_NOTEWORTHY_MODE=llm_only`, `LORE_PROJECT_FOLDERS=off`) long after
those defaults flipped on in production, keeping legacy code paths alive
under fixture protection. PR 6a flipped the fixtures off; 6b/6c/6d
deleted the now-unprotected legacy code paths one at a time.

- **PR 6a — conftest grandfather flip (#99)**: removed the three autouse
  fixtures; migrated every failing test to production defaults
  (preferred) or opted specific tests back into legacy mode inline via
  `monkeypatch.setenv` (only where the legacy mode itself was under
  test).
- **PR 6b — buffer-flush legacy delete (#108)**: deleted
  `lib/lore_curator/summary_merge.py` (-274 LOC), the
  `_process_chunk` / `_buffer_flush_enabled` branch in
  `session_curator.py`, the classify-per-chunk branches in
  `session_filer.py` and `session_writer.file_or_merge`, the
  `LORE_BUFFER_FLUSH` env var, and `root_config.use_buffer_flush`. Net
  ~-3,900 LOC across 18 files (lib + tests). Buffer-flush is now the
  only curator-A pipeline.
- **PR 6c — `noteworthy_mode=llm_only` delete (#103)**: removed the
  `llm_only` branch in `lib/lore_curator/noteworthy.py`, `NoteworthyMode`
  Literal, `_resolve_mode()`, the `mode` field on cascade-verdict
  events, the `LORE_NOTEWORTHY_MODE` env var, and the
  `curator.noteworthy_mode` config field. Net -290 LOC across 8 files.
  The cascade is now the only noteworthy gate.
- **PR 6d — `LORE_PROJECT_FOLDERS=off` delete (#106)**: removed
  `project_folders_enabled()` from
  `lib/lore_core/projects/router.py`, the toggle gate inside
  `project_dir_for_scope`, the env var, and the `skipped_toggle_off`
  bailout in `c_cross_scope_hoist.py`. Net -137 LOC across 7 files.
  Project-folder routing is now unconditional — folder existence is
  the only gate.

Grandfather track totals: -4,300 LOC across the four PRs. Combined with
the structural PRs 1–5 (already in 0.51.0), the full 9-PR streamlining
track removed ~5,500 LOC (~12% of the codebase) without dropping any
of the 27 ratified use cases.

`lint.py` split (PR 10 in the PRD) remains contingent on a future lint
rule / catalog generator expansion; not part of this release.

## [0.51.0] - 2026-05-12

### Added — Two-region session notes: reload-safe + human-only (PRD #92, slices #93–#97)

Past-self carries the investigation arc and in-passing reasoning;
future-LLM never sees it auto-loaded as authoritative. Each session
note has two regions in one file, separated by the
`<!-- lore:human-only -->` marker:

1. **Reload-safe** (top, structured): the existing schema — lede,
   outcomes / takeaways, worked_on, loose_ends, adr_candidates.
   Terse, LLM-facing.
2. **Human-only** (bottom, narrative): free-form prose. Investigation
   trail, experiments, in-passing decisions told as story not as
   claims. Gated retrieval — visible only to user-invoked surfaces.

- `lore_core.regions` (#93): new module owning marker semantics —
  `split_regions`, `render_regions`, `redact_human_only`, with
  code-fence-aware parsing and forgiving fallbacks (missing marker →
  whole body is reload-safe; multiple markers → first wins; old notes
  index unchanged).
- Curator emits both regions in one `compose` call (#94): tool schema
  gains a top-level `narrative` field; prompt teaches voice
  (tentative, past-tense investigation), length-to-substance
  calibration (empty is fine), anti-duplication with the structured
  section, and the discussion-shape takeaways tightening (markers
  must be self-contained references, not session-jargon shorthand).
- SessionStart hook applies `redact_human_only` to injected
  last-session note bodies (#95).
- MCP retrieval surfaces apply `redact_human_only` at every
  LLM-facing boundary (#96): `handle_search`, `handle_read` (default;
  bypass via `include_human=true`), `handle_surface_context`,
  `handle_briefing_gather`. User-invoked surfaces (`handle_resume`,
  `handle_drill`) return full content unchanged.
- FTS index splits body into two columns (#97): schema v2→v3 adds
  `body_reload_safe` + `body_human_only` on `notes_fts`. LLM-facing
  search wraps the MATCH expression in an FTS5 column-set filter
  (`{title description tags body_reload_safe}: (...)`), so a term
  that lives *only* in human-only content produces no hit at all —
  option (b) clean exclusion, no snippet preview the LLM could ask
  about.

### Added — Streamlining track: PRs 3–5 of the 9-PR cleanup (#80)

- PR 3: spawn module extraction + role registry (#87) — curator
  spawn paths converge on one module with role-keyed dispatch.
- PR 4: hygiene-pass protocol + registry (#88) — every curator
  hygiene pass is declared in one registry with a uniform
  before/after contract.
- PR 5: SessionStart refactor + v1 legacy delete (#90) — the
  pre-buffer-flush SessionStart code path and its tests are deleted;
  only the v2 (buffer-flush) path remains.

### Why

Single biggest UX complaint: past-self could not recognise their own
session notes a week later. The structured schema (correct for its
job — LLM auto-load contract) had no slot for the investigation arc,
the experiments, the in-passing decisions. Restoring free-form prose
without the gating would mean LLM sessions auto-load tentative
reasoning as authoritative claims and sidetrack future work.
Two-region split resolves both: past-self gets the prose, future-LLM
never sees it auto-loaded. Trust contract preserved by construction
through the retrieval filter — applied at every LLM-facing boundary.

### Migration

Old notes (no marker) treated as fully reload-safe by
`split_regions`'s forgiving semantics. Zero data migration. The FTS
schema bump (v2→v3) is transparent: `_migrate_if_needed` drops the
old table on connect and the next reindex repopulates with the
split.

## [0.50.0] - 2026-05-12

### Added — `lore_pending_verdicts` MCP tool, `/lore:verify` rewrite

The `/lore:verify` slash command no longer does ad-hoc bash discovery
to find pending freshness verdicts. One MCP call now returns the full,
picker-ready, pre-sorted list.

- New `lore_pending_verdicts` MCP tool — wiki-wide enumeration of
  notes currently flagged as stale-candidate. Returns the
  `lore.pending_verdicts/1` envelope with per-entry slug, cause
  (`authored_marker` / `orphan_broken`), reason text, personal
  `confirmed_at`, and the disagreement block when present.
- Entries arrive pre-sorted: disagreements first, then
  `authored_marker`, then `orphan_broken`; within each bucket, most
  recently marked stale first.
- `wiki` arg is optional — auto-resolves from the active cwd
  attachment in multi-wiki vaults.
- Slash command rewritten to call the tool once, then loop a picker
  per entry. Zero bash; the catalog + frontmatter + sidecar reads all
  live behind the MCP boundary.

### Changed

- `lore_core.freshness`: extracted `_is_pending_from_catalog_entry`
  shared predicate; `count_pending_verdicts` (the status-line chip)
  and the new `list_pending_verdicts` (the picker) both gate on it,
  so the chip count and the picker can no longer drift on what
  counts as pending.

### Why

The chip says "1 pending verdict" but bare `/lore:verify` previously
returned zero because it filtered by session-touched activity log —
which is empty at SessionStart. The slash command is the chip's
actionable surface, so it now matches the chip's scope exactly
(wiki-wide). The session-scoped narrowing was redundant with the
in-passing nudge (which already fires per-note, in-flow, per
session).

## [0.49.0] - 2026-05-08

### Added — Freshness verdicts: end-to-end (#65, slices 1–10)

Positive-evidence-only staleness with read-time, in-passing user
verdicts. Stack of ten vertical slices delivered together:

- Slice 1: read-only `freshness` block on every `lore_search` /
  `lore_read` / `lore_drill` MCP response.
- Slice 2: legacy 90-day age rule retired — `_pass_staleness` is now
  a no-op and the `--stale-threshold` CLI flag is removed.
- Slice 3: retrieval-time filter — `confirmed` ranks above
  `stale-candidate` at tied scores; SessionStart inject excludes
  `status: stale` and downranks soft markers; audit log surfaced via
  `/lore:context`.
- Slice 4: orphan-link signal — `lore lint` caches the orphan set
  in `_catalog.json`; freshness flags it with
  `cause: orphan_broken`.
- Slice 5: `lore_verdict` MCP tool with the stale + clear-stale
  branches; `stale_marker_writer` writes the four-field schema
  additively.
- Slice 6: per-user `wiki/<name>/_verdicts/<handle>.json` sidecar
  for personal confirms; soft-marker suppression with mtime gate +
  14-day recency window.
- Slice 7: in-passing nudge + dynamic-escalation directive added to
  the lore-managed CLAUDE.md template.
- Slice 8: SessionStart status-line `· N pending verdict[s]` chip
  with soft cap (`9+`) and zero-state suppression.
- Slice 9: team-mode disagreement detection — surfaces conflicts
  between team-stale frontmatter and per-user sidecar confirms.
- Slice 10: `/lore:verify` slash command for the explicit batch
  resolver.

### Removed — `lore curator --stale-threshold` flag (#65, slice 2)

PRD #65 (positive-evidence-only staleness) replaces the 90-day age
rule with read-time freshness signals derived from named causes only
(authored markers, broken wikilinks). The corresponding curator-C
pass `_pass_staleness` is now a no-op and the `--stale-threshold` CLI
flag has been removed. Existing notes that already carry
`status: stale` from prior runs are left untouched (additive-only
edit policy, #37). Cron-driven `lore curator --stale-threshold N`
invocations should drop the flag.

### Added — `/lore:verify` slash command (#65, slice 10)

Plugin skill registered as `lore:verify`; user-invocable batch
picker over the freshness backlog. Bare `/lore:verify` lists notes
flagged in the current session; `/lore:verify --all` lists every
currently-stale-candidate note in the active wiki. Each entry
prompts confirm / stale / skip; stale prompts for a one-line reason
and calls `mcp__lore__lore_verdict` accordingly. Wraps the verdict
MCP — no new write paths.

## [0.48.0] - 2026-05-08

### Changed — Plugin skill set reduced from 19 to 5 (#64)

The lore plugin now ships only 5 skills instead of 19. Every skill's
name and description is paid into the always-on available-skills
system reminder on every session start; the previous 19-skill list was
mostly thin CLI wrappers, deprecated aliases, and one-time admin
tools. The reduction is **subtractive only** — every removed skill's
function remains reachable through CLI, MCP, or natural-language ask.

**Surviving skills (judgment-required):** `inbox`, `surface`,
`resume`, `curator`, `context`. SKILL.md bodies were also trimmed
(541 → 266 lines total, -50.8%).

**Migration table:**

| Removed | Replacement |
|---|---|
| `/lore:loud` | `lore on citations` |
| `/lore:quiet` | `lore off citations` |
| `/lore:journal` | `lore journal write [--ai] "<text>"` (or ask Claude); `lore_journal_write` MCP for AI-side |
| `/lore:init` | `lore init` |
| `/lore:new-wiki` | `lore new-wiki <name>` |
| `/lore:import` | symlink the vault under `$LORE_ROOT/wiki/<name>` and run `lore lint`; ask Claude to enrich frontmatter if desired |
| `/lore:attach` | `lore attach` |
| `/lore:detach` | `lore detach` |
| `/lore:briefing` | `lore briefing --wiki <name>` |
| `/lore:lint` | `lore lint` |
| `/lore:on` | `lore on` |
| `/lore:off` | `lore off` |
| `/lore:search` | `/lore:resume <query>` (keyword mode); raw paths via `lore search <query>` shell or `lore_search` MCP |
| `/lore:surface-init` | `/lore:surface <wiki>` (answer "redesign" at the dispatch prompt) |
| `/lore:surface-add` | `/lore:surface <wiki>` (answer "add") |

**Why hard cut and not a deprecation cycle:** the cut surfaces are
infrequent (init / import / attach are one-time-per-machine; on / off /
loud / quiet are rare debug verbs; lint / briefing / search are
CLI-shaped already). A CHANGELOG entry plus clear "command not found"
errors covers the user impact.

**Plugin version bump is mandatory** for installed caches to invalidate
and pick up the new manifest. `claude plugin update lore@lore` after
upgrading.

Tracked in PRD #64; implemented across #67 (delete 12 skills), #68
(merge surface-init + surface-add), #69 (fold search into resume),
#70 (body-trim survivors), #71 (this release).

## [0.47.0] - 2026-05-07

### Fixed — Curator A: one note per `(transcript_id, local_date)` (#52)

Pre-compact and session-end no longer fragment a long-running
conversation into multiple session notes. Both events now route through
a new `synth_in_place` path that runs Phase 1 + Phase 2 against the
*live* buffer, refreshes the on-disk note with merged narrative, and
leaves the buffer in `accumulating` so the next chunk on the same
transcript continues into the same stub. Cap-trip and the reaper remain
the only paths that close a buffer (and legitimately produce a Part-N
continuation linked via `continues:` / `continued_by:`); they go through
`synth_and_close`.

The companion bug behind the user-visible "three split notes for one
transcript" symptom is also fixed:

- `_resolve_active_part` now scans `.lore/buffers/_done/` in addition to
  live buffers, so a closed Part-N legitimately yields Part-(N+1) on
  the next heartbeat instead of silently re-opening Part-1.
- `Buffer.close` raises rather than silently overwriting an existing
  `_done/<stem>.state.json`. Silent overwrite was masking the
  part-resolution failure; making it loud surfaces remaining bugs.

### Changed — Honest `files_modified` / `files_read` schema (#52)

New session notes write `files_modified` (writes only — load-bearing
for merge-gate Jaccard, retrieval, and narrative tense) and
`files_read` (reads NOT subsumed by edits — interview / code-tour
provenance). `files_touched` (the legacy union of edits + reads) is
no longer written by any new buffer-flush path.

- New helper: `lore_curator.stub_note.file_lists_for_frontmatter` —
  single source of truth for the "suppress reads when subsumed" rule.
- Merge gate (`_find_todays_open_note`) prefers `files_modified` over
  `files_touched`; legacy notes filed under the old schema still match
  via the `files_touched` fallback.
- `lore_core.threads` reads `files_modified` first, falls back to
  `files_touched` for legacy notes.
- Activity-summary text fed to Phase 2 surfaces `Files modified:` /
  `Files read:` rather than the old `Files touched:` line, so the LLM
  doesn't conflate reads with edits in the narrative tense.

Legacy `files_touched` on already-filed notes stays valid as an opaque
union; no migration. Read-side back-compat at every consumer.

### Added — Buffer state-machine `mode` field

`FlushRequest.mode` discriminates `"close"` (cap-trip / reaper —
existing behaviour, transitions through `ready -> flushing -> closed`
and archives) from `"in_place"` (session-end / pre-compact —
synthesises against the live buffer without closing). Default is
`"close"` for back-compat with sidecars written before the field
existed.

`state: stub` in frontmatter widens to mean "buffer is live": set on
first stub write, retained by `synth_in_place`, cleared only on
`synth_and_close`. The merge gate's stub-protection branch keeps
working under the new semantic.

## [0.46.0] - 2026-05-07

### Removed — Plan tracking infrastructure (clean slate)

The plan-as-tracked-artifact subsystem is gone. Plan-mode wrapping was
the wrong abstraction: every IDE has its own plan-mode shape, and lore's
attempt to mirror their state introduced fragile machinery (LLM-judged
commit closure, step-file frontmatter, `Plan:` trailers, attribution
bridge) that didn't deliver phase handover — it duplicated a signal
session notes already carry, and degraded loudly when the protocol
slipped (timeouts, low-confidence skips piling up in SessionStart).

Removed end-to-end:

- `lore plan` CLI subtree (filing, advance, migrate-step-files, status).
- `lore_plan_active` / `lore_plan_file` / `lore_plan_status` MCP tools.
- `/lore:plan-resume` and `/lore:plan-advance` skills.
- `PostToolUse:ExitPlanMode → lore hook plan-capture` hook.
- `PostToolUse:Edit/Write/MultiEdit/NotebookEdit → lore hook
  plan-edit-writeback` hooks.
- `Stop` hook plan-trailer-nudges + LLM-judged commit attribution.
- SessionStart Resume block + Pending Attributions block.
- `lore_core.plans/` module (parser, registry, writer, ingest, envelope,
  step_status, breadcrumbs, classifier, etc. — the full subtree).
- `lore_curator.closure_judgment` + `lore_curator.step_files_inference`.
- `curator.closure_judgment_enabled` config knob.
- `plans:` frontmatter on session notes + `[[plan/<slug>]]` wikilink
  validation in curator A.
- `Plan-authoring: declare files per step` directive from SessionStart.
- 21 plan-specific test modules.

Phase handover now rides exclusively on session notes (`lore_resume`,
last-session hints in SessionStart) + the journal. Plan files written by
external tools (Ultraplan, claude-code Plan mode) can still live in the
vault as ordinary notes — `lore_search` finds them — but lore no longer
parses, classifies, tracks step status, or judges commits against them.

### Changed — SessionStart minimisation

The injected context block trimmed from ~50 lines to ~10 in the typical
attached-repo case. Specifically:

- AI Journal directive collapsed from a 4-bullet sub-bulleted block to
  a single sentence (still behaviour-shaping; just compressed).
- Issue/PR title list dropped — status line still shows the count;
  agents fetch the actual list via `gh issue list` on demand.
- Linked-notes 6-name preview dropped (use `lore_search`).
- "+N more" tails removed from issues/PRs.
- Subtree-issue aggregation removed.
- `_subtree_issues` / fallback "no issues matched filters" line removed.
- Last-session hints capped at 2.
- Override-syntax instructions and Resume-block trailer hints removed
  (they were reference, not actionable signal).

### Migration

- Plan files stored under `<wiki>/plans/` are no longer special; they
  remain as files but are not catalogued, indexed differently, or
  surfaced via SessionStart. `lore lint` no longer manages
  `plans/_recent.txt`.
- Existing `Plan:` trailers in commits are inert; remove them at your
  leisure.
- Existing `plans:` frontmatter on session notes is preserved on
  read but no longer written or consumed.



### Added — `lore config show / get / set / schema` (one-stop config UX)

`lore config` now grows four typed-configuration subcommands so users
don't have to hand-edit ``$LORE_ROOT/.lore/config.yml`` (and never
have to wonder which file a knob lives in).

- ``lore config show [path] [--changed]`` — render the resolved
  ``RootConfig`` as a table with value, default, and provenance
  (``file`` if set in YAML, ``default`` otherwise). Optional
  positional ``path`` filters by dotted prefix; ``--changed`` shows
  only fields whose value differs from the schema default.
- ``lore config get <path>`` — print one value (e.g. ``true``,
  ``openai``). Group paths (``curator``) raise a clear error.
- ``lore config set <path> <value>`` — persist a value to
  ``$LORE_ROOT/.lore/config.yml`` with type validation. Bool fields
  accept ``true/false/yes/no/on/off/1/0`` (case-insensitive); ints
  and floats parse strictly. Unknown paths and bad values fail with
  exit-2 and a typed error message. Existing siblings in the YAML
  are preserved.
- ``lore config schema`` — print the full ``RootConfig`` tree
  (paths, types, defaults, group docstrings) from dataclass
  introspection. Single source of truth for "what can I configure?"

Bare ``lore config`` (no subcommand) keeps the existing vault-layout
view. The footer of that view now points users at the new
subcommands.

### Added — Public introspection helpers in ``lore_core.root_config``

``walk_fields(lore_root)`` → list of ``FieldInfo``;
``get_field(lore_root, path)`` → single FieldInfo;
``set_field(lore_root, path, value_str)`` → write-back with type
coercion + path validation; ``schema_tree()`` → schema rows.

These are the seam adapters / external tools should use rather than
re-parsing YAML or duplicating ``RootConfig``'s defaults. 19 unit
tests covering provenance, write-back round-trip, type validation,
unknown-path errors, and bool spelling tolerance.

### Known limitations (tracked in #51)

- PyYAML round-trip in ``set`` does not preserve inline comments;
  they will be stripped when the file is written back. Migration to
  ``ruamel.yaml`` for comment preservation is a follow-up.
- Provenance distinguishes ``file`` vs ``default`` only;
  env-variable overrides (``LORE_LLM_BACKEND``, ``LORE_BUFFER_FLUSH``,
  ``LORE_DISABLE_LLM_JUDGMENT``, etc.) are not yet surfaced as a
  distinct source — the per-call resolver functions
  (``_resolve_backend``) interpret env vars and would need
  registration to bubble up. Tracked for the next iteration.
- ``lore config schema`` doc column shows the parent dataclass'
  docstring; per-field descriptions live as inline comments in
  ``root_config.py`` and aren't reachable via ``inspect``. Source
  parsing / docstring conventions are a follow-up.
- ``secrets_env.load_into_environ`` still injects any key it finds
  into ``os.environ``; the deprecation warning for toggle-shaped
  keys is part of the broader #51 work, not this release.

## [0.45.2] - 2026-05-06

### Added — ``curator.closure_judgment_enabled`` config flag

The Stop-hook closure-judgment kill switch lives in
``$LORE_ROOT/.lore/config.yml`` now, alongside ``curator.backend``
and the other curator settings. Default ``true`` (preserves
existing behaviour). Set to ``false`` to skip
``_attribute_commits_with_judgment`` entirely and fall back to
trailer-only nudges:

```yaml
curator:
  backend: openai
  closure_judgment_enabled: false
```

The env var ``LORE_DISABLE_LLM_JUDGMENT=1`` from v0.45.1 is retained
as an ops override (CI, debugging) but is no longer the primary
user knob — putting feature toggles in ``.lore/secrets.env`` was
the wrong shape (that file is for credentials). Config-level
schema, validated by ``CuratorBackendConfig``, surfaced by
``lore config``.

(Tracked in #51 for the broader "one-stop-shop config UX" work.)

## [0.45.1] - 2026-05-06

### Fixed — closure-judgment fork bomb at Stop hook

Critical hotfix for a regression introduced in commit `b873843`
(`feat(plans): LLM-gated commit attribution at Stop`, step-3b).

**Symptom.** With an active plan that has ``step_files`` declared and
recent commits whose changed files overlap them, every ``Stop`` hook
fired ``claude -p`` via ``_attribute_commits_with_judgment``. The
spawned ``claude -p`` ran the Lore plugin and fired its *own* Stop
hook, which spawned another ``claude -p``. Because the per-session
seen-set is keyed by ``sid`` and each spawned ``claude -p`` has a
fresh ``sid``, idempotency didn't carry across the recursion. The
result: a runaway tree of ``claude -p`` processes, each writing
extractor JSONLs that the capture pipeline then enrolled as new
sessions (and Curator A confabulated session notes from).

**Root cause.** Two compounding bugs at the same call site
(``hooks.py:1836``):

1. ``_attribute_commits_with_judgment`` called ``make_llm_client()``
   with no arguments. The factory only consults ``LORE_LLM_BACKEND``
   from the env, never ``curator.backend`` from ``.lore/config.yml`` —
   so the user's explicit choice was ignored and the call fell
   through to auto-probe. Subscription / ``claude -p`` is a
   first-class backend (and stays one — many users have no
   non-Claude LLM API on hand); the bug was using it as a *fallback*
   that overrode the user's configured backend.
2. ``SubprocessClient.create`` invoked ``subprocess.run`` without
   propagating ``LORE_CURATOR_MODE=1`` — the spawned ``claude -p``'s
   plugin hooks did not see the re-entry guard and recursed back
   into ``_attribute_commits_with_judgment`` with a fresh per-session
   seen-set, fanning out across (recent_commits × plan.step_files).

**Fixes (both in this release).**

- ``hooks.py`` now resolves the backend via
  ``defrag_curator._resolve_backend(None, lore_root)`` and passes
  ``backend=`` and ``lore_root=`` to ``make_llm_client``. Same path
  the curator commands have always used; hooks just forgot to copy
  it.
- ``llm_client._SubprocessMessagesAPI.create`` now sets
  ``env={**os.environ, "LORE_CURATOR_MODE": "1"}`` on the
  ``subprocess.run`` call. Defense in depth: even if a future call
  site requests the subscription backend explicitly, the spawned
  ``claude -p`` cannot recurse via plugin hooks.

### Added — ``LORE_DISABLE_LLM_JUDGMENT`` kill switch

Set ``LORE_DISABLE_LLM_JUDGMENT=1`` to skip
``_attribute_commits_with_judgment`` entirely and fall back to the
trailer-only nudges. Provided as a safe re-enable knob for users
who hit the runaway during the v0.42.0–v0.45.0 window and want a
belt-and-braces guarantee while migrating to v0.45.1+.

## [0.45.0] - 2026-05-05

### Added — `lore plan migrate-step-files` (deterministic + LLM)

New CLI for backfilling ``step_files`` frontmatter on plans authored
before the ``Files:`` convention shipped. Two-tier extraction:

- **Deterministic** — re-runs the canonical parser; conservative merge
  (never overwrites non-empty existing entries).
- **LLM-judged** (``--llm``) — falls back to one
  ``step_files_inference`` tool-use call per plan when the parser
  leaves gaps. Per-step confidence below ``--confidence-floor``
  (default 0.5) is dropped; high-confidence empty lists are recorded
  explicitly so the migrate is idempotent.

The companion module is ``lore_curator/step_files_inference.py``,
following the ``closure_judgment`` pattern (frozen dataclass +
``tool_choice`` + structured-output schema with
``additionalProperties: false``).

This unblocks the closure pipeline (commit attribution, edit-flip
writeback) for plans whose bodies don't carry ``Files:`` directives —
without that backfill, the Stop-hook attributor short-circuits at the
``step_files`` empty-set check and plans never auto-close.

### Fixed — `Files:` parser handles backtick-wrapped directives

Plan-authoring LLMs commonly render the directive as inline code
(``` `Files:` ```) when the surrounding step body is heavy on
backticked identifiers. The previous regex anchored on the literal
``files`` keyword and silently failed to match — so
``2026-05-05-plan-conditional-decisions-discussion-aware-session-note-nar``
filed with empty ``step_files`` despite every step body carrying a
``Files:`` block.

The parser now also handles annotated-bullet style
(``- `lib/foo.py` — explanation``) including multiple backticked
paths per bullet line. Em-dash slicing happens before backtick
extraction so identifiers in the explanation (``_helper``,
``MyClass``) aren't mis-detected as paths.

## [0.44.0] - 2026-05-05

### Added — conditional Decisions / discussion-aware narrative shape

Session notes now render a narrative shape (work / discussion) that's
deterministically chosen at flush time from edit activity + user assent
signals. This fixes a class of bug where exploration / brainstorm
sessions filed notes claiming Decisions and "What we worked on" for
work that never happened (motivating case:
``05-1212-refactor-ccat-transfer-docs-into-di-taxis-spine``: 0 Edit
calls, 12 Reads, user said "no code change just exploration" twice —
note declared 5 decisions and 8 worked-on bullets).

Plan: ``yes-do-that-keen-yeti``, 9 steps shipped:

- **``files_modified`` split from ``files_touched``.** The deterministic
  primitive that biases narrative tense. ``_files_touched_from_turns``
  used to merge ``file_edit`` and ``file_read`` categories; the new
  ``_files_modified_from_turns`` filters to edits only. Buffer events
  carry both fields; old v1 buffers replay without mis-classifying.
  Frontmatter writes ``files_modified`` alongside any pre-existing
  ``files_touched`` (vault-edit policy: never delete a frontmatter
  field on append-merge).
- **``decision_signals.py`` deterministic prefilter.** Four regex banks
  (no-edit-intent, hedge, assent, override, ADR cue) operating on
  user-turn text. Hedge-in-same-sentence downgrades assent confidence
  to ``weak`` rather than suppressing globally; question-form sentences
  drop matches entirely.
- **``NarrativeShape`` selector.** Frozen dataclass of booleans
  (``has_edits``, ``decisions_allowed``, ``no_edit_intent``,
  ``adr_flagged``) composed from the prefilter signals plus
  ``files_modified``. The renderer and Phase-2 schema gate switch on
  bits, not on a string — adding a future shape is a one-bit change.
- **Phase-2 schema + prompt gating.** The ``compose`` tool schema is
  now a function of ``NarrativeShape``: discussion shape strips
  ``decisions[]`` and ``worked_on[]`` entirely and offers
  ``discussion[]``; ``additionalProperties: false`` plus caller-side
  filtering close the gate structurally rather than instructionally.
  Prompt grows a kind-specific clause that explains the schema
  narrowing and the title-shape rule.
- **Renderer adds ``## Discussion`` section.** Conditional, omitted
  when empty, slots between Summary and Decisions. ``BodySections``,
  ``parse_body_sections``, ``render_body_sections``,
  ``merge_body_sections`` extended additively — no merge-semantics
  changes. Mixed-kind continuation merge produces the union of section
  types (documented limitation).
- **Title-verb gate.** Post-LLM coercion in discussion shape: leading
  deliverable verbs (Refactor, Add, Fix, Implement, Migrate, Build,
  Ship, Create, Delete, Remove, Update, Replace, Land, Rewrite +
  tense variants) are stripped and ``Discussed:`` is prepended.
  Already-discussion-led titles and noun-phrase titles pass through.
- **Curator B Discussion fallback.** ``daily_curator._section_aware_summary``
  falls through Decisions → Discussion → Summary alone → ``body[:800]``
  so cluster topic-discrimination keeps a rationale anchor for
  discussion-shape notes.
- **``adr_flagged`` frontmatter.** When the user explicitly invoked
  ADR vocabulary ("ADR this", "let's record this as an ADR"), the
  frontmatter carries ``adr_flagged: true``. **No vault mutation** —
  promotion to a real ADR note is a future workflow.
- **Norms doc + golden-file prompt regression.**
  ``session_templates/standard.md`` rewritten with the conditional
  taxonomy; ``surface_templates/standard.md`` lightens the "CANDIDATE
  — not license" warning since Decisions is now high-signal-by-
  construction. ``tests/fixtures/prompts/phase2_{work,discussion}.txt``
  pin the prompts as golden files so silent regressions diff loudly.

The bad note's failure mode is regression-pinned in
``test_phase2_e2e_05_1212_pattern_yields_discussion_shape``.

Deferred (tracked as issues): Stage-2 LLM judge for ambiguous
prefilter candidates, ADR promotion flow that reads ``adr_flagged``,
``lore curator repair`` for re-flushing pre-existing bad notes.

## [0.43.1] - 2026-05-04

### Changed — bullet line cap raised 120 → 280 chars

The Phase 2 ``BULLET_LINE_MAX = 120`` was too aggressive for technical
session notes. Substantive decisions / loose-ends bullets routinely
need PR refs, file paths, section numbers, and a clause of reasoning;
120 chars routinely chopped them mid-word with an ellipsis ("…
provisioners-add.sh as a thin alias of provisioners-bootstrap.sh (or
delete) for now, updat…"). Raised to 280 (length of an old tweet,
two terminal lines) — fits substantive bullets without going
unbounded. Caps stay defensive: ``decisions <= 5``, ``worked_on
<= 8``, ``loose_ends <= 5``.

Existing notes whose bullets were already truncated stay as-is on
disk; rerunning Phase 2 against their buffer (manual flush) would
regenerate full bullets, but that's a per-note opt-in.

## [0.43.0] - 2026-05-04

### Fixed — synthesis no longer fabricates from boilerplate

Phase 2 has been operating without ANY conversation content since
buffer-and-flush shipped (PR 2 of the very-good-thats-the-mossy-lobster
plan). ``synthesis._read_slice_text`` calls
``adapter.transcript_path_for_id(session_id, cwd)`` to rebuild a
TranscriptHandle from the buffer sidecar — but the method was added in
that PR and **never implemented on any adapter**. Every call hit the
silent ``tx_path is None: return ""`` branch, so the LLM prompt always
ended with ``Conversation slice:\n`` (empty).

Symptoms in user vaults: prompts capped at ~1.2K chars (boilerplate +
comma-joined files-touched list), and mid-tier models confabulated
confident-but-fictional narratives — e.g. a session about lore attach
work was filed as ``Storage Engine Disk Identifier Tests Located``,
and ccat HSM playbook work was filed under unrelated dockerfile
basenames.

- **Adapter Protocol** — adds ``transcript_path_for_id(session_id,
  cwd) -> Path | None`` as a required method. Implementations:
  ``ClaudeCodeAdapter`` (path math via ``_session_file_path``),
  ``CursorAgentAdapter`` (per-uuid agent dir + glob fallback),
  ``VSCodeCopilotAdapter`` (walks every VSCode-family user dir for the
  workspace hash), ``ManualSendAdapter`` (returns None — no canonical
  layout, Phase 2 falls back to deterministic flush).
- **Empty-signal guard** — ``flush_buffer`` now skips the LLM entirely
  when ``turns_text`` is empty AND the activity has no commits / plans
  / projects to anchor on. The flush is marked degraded; the
  deterministic Phase 1 stub stays. Prevents fabrication when the
  conversation slice can't be retrieved (deleted transcript, adapter
  returning None, etc.) and the file list alone is too thin to write
  a substantive narrative.
- **Prompt hardening** — when ``turns_text`` is empty (rare, but
  possible: stub note for an issues-only session, etc.), the prompt
  explicitly instructs the model to stay strictly on the activity
  bullets and produce a terse factual summary rather than padding.
- **Telemetry** — the ``llm-prompt`` event now includes
  ``turns_text_chars`` and ``activity_summary_chars`` so this class of
  regression is visible in run-log review.

## [0.42.0] - 2026-05-04

### Changed — session-note slugs follow the synthesised title

The deterministic stub slug (set at first heartbeat from a commit
subject / files-touched basename / `session-<scope>-<HHMM>` fallback)
was never re-derived after Phase 2 wrote a real title. Filenames like
`04-1101-session-lore-1101.md` and `04-1318-attach.md` lingered
forever, even though the in-content title was meaningful.

- **Phase 2 rename.** `_phase2_apply` now derives a slug from the
  composed title and renames the stub when it differs from the seed.
  Skips part-2+ continuation chains (renaming would orphan
  `continued_by:` cross-refs on the prior part) and notes whose new
  slug matches the existing one. The old stem is written into
  `aliases:` so existing `[[old-stem]]` references resolve.
- **Wikilink resolver follows aliases.** `existing_slugs` now reads
  frontmatter `aliases:` (cheap pre-filter — only files containing
  literal `aliases` get a YAML parse) so the alias trail is honoured
  by `sanitize_for_write` and `strip_broken_wikilinks`.
- **Backfill CLI.** `lore curator backfill-slugs [--wiki <name>]
  [--apply]` walks the historical backlog and applies the same
  rename retroactively. Skips stubs awaiting synthesis, continuation
  chains, placeholder-title notes, and already-canonical names.

### Changed — reaper reaps known-dead owners immediately

The reaper required `staleness_threshold_s` (30 min default, doubled
on macOS) to elapse before reaping any buffer. Short Claude sessions
that died without `SessionEnd` would leave stub notes stuck in
`synthesis pending` for the full window, even though the owner pid
was already gone.

- **`reaper._judge`.** When `is_owner_alive` returns `False`
  (unambiguous: host mismatch, `ProcessLookupError`, or start-ts
  mismatch indicating PID reuse), reap immediately. The uncertain
  branch (`alive_verdict is None` — no `/proc`, network fs, macOS)
  still falls back on staleness to avoid false positives.

## [0.41.0] - 2026-05-04

### Changed — Step-9: migration session + flip default

Closes the projects-as-canonical-surface plan.

- **`LORE_PROJECT_FOLDERS` default flipped to on.** The env var is now
  an off-switch (set `LORE_PROJECT_FOLDERS=off` for emergency rollback
  to flat paths) rather than an opt-in. Empty / unknown values default
  to on.
- **Vault migration.** One-off Claude Code session re-homed flat
  `concepts/`, `decisions/`, `plans/` notes into `projects/<slug>/...`
  under both private (single `lore` scope) and ccat (14 project
  orientations promoted to folders, plus auto-stubs for `telescope` and
  `primecam` scopes). Cross-wiki re-homing of one note from ccat to
  private. Promoted `concepts/ccat-observatory.md` to a project
  orientation. Stripped bare `project` tag from `tags:` lists where
  present.
- **Tests.** Added `tests/conftest.py::_default_project_folders_off`
  autouse fixture grandfathering pre-flip tests onto the legacy flat
  layout (matches the `LORE_BUFFER_FLUSH` and `LORE_NOTEWORTHY_MODE`
  patterns). Dual-mode tests opt-in via inline `monkeypatch.setenv(...)`.

## [0.40.0] - 2026-05-02

### Added — Projects-as-canonical surface rollout (8 phases)

Reshapes the vault around scope-aware project folders. Every change
ships behind `LORE_PROJECT_FOLDERS=on|off` (default off) so existing
vaults stay untouched until the migration session runs.

- **Phase 1** — Lint generates `_concepts.txt` / `_decisions.txt` /
  per-type collection files at the wiki root. Existing
  `sessions/_recent.md` / `plans/_recent.md` / `threads.md` migrated
  to `_recent.txt` / `_threads.txt`. Universal `_<name>.txt`
  convention for generated leaves keeps them out of the wikilink
  graph.
- **Phase 2** — Curator B strict scope partitioning. Session notes
  are grouped by exact `scope:` value before clustering. New
  `unscoped-notes` / `cluster-scope-overridden` telemetry events.
- **Phase 3** — Dual-mode schema substrate. New
  `lore_core.projects.router` resolves surface paths through
  `projects/<slug>/<surface-dir>/` when toggle is on AND the project
  folder exists. `stub_project_note(bare=True)` for hoist auto-stubs.
- **Phase 4** — Curator C cross-scope concept hoist proposal pass.
  Title-slug fuzz ≥0.6 across ≥2 sibling project folders; auto-stubs
  the parent project; proposal-only.
- **Phase 5** — Plans co-locate at
  `projects/<slug>/plans/YYYY-MM-DD-<slug>.md` when toggle is on.
  Toggle-off path stays byte-for-byte identical to pre-rollout.
- **Phase 6** — SessionStart auto-injects project orientation note
  body (capped at 3000 chars). Slug validated against
  `[A-Za-z0-9._-]+` before path join.
- **Phase 7** — AGENTS.md ↔ orientation `## Agent guidance` sync.
  New lint check + `lore project sync SLUG --to-repo|--from-repo`.
- **Phase 8** — `lore scopes rename` cascade rewrites frontmatter
  across wikis (`lore_core.surfaces.rewrite_scopes_in_frontmatter`)
  and appends to `$LORE_ROOT/_scope_renames.txt`. New
  `lore scopes reconcile` for multi-host catch-up.

### Migration

Existing flat `concepts/`, `decisions/`, `threads/`, `plans/` notes
are not auto-relocated — that's a one-off Claude Code session
rather than built tooling. Until then, `LORE_PROJECT_FOLDERS=off`
(the default) keeps everything where it is.

## [0.39.0] - 2026-05-02

### Changed

- **Buffer-and-flush is now the default heartbeat path**
  (`lore_core/root_config.CuratorBackendConfig.use_buffer_flush=True`).
  Each Curator A heartbeat now appends deterministic chunk deltas
  (slice pointers, files_touched, plans, projects, commit SHAs,
  pre-rendered Activity bullets) to a per-`(transcript_id, local_date)`
  buffer under `.lore/buffers/` and writes a live stub note at the
  canonical session path with frontmatter `state: stub`. The narrative
  (title, description, Summary, Decisions made, What we worked on,
  Loose ends) is composed by **one** Phase-2 LLM call at flush time —
  triggered by SessionEnd, PreCompact, cap-trip, or the liveness
  reaper — instead of one classify-per-chunk + one anchored
  summary-merge call per heartbeat. Outcome targets: ~70 % LLM-call
  reduction, one note per transcript per day baseline, sub-5 s
  perceived handover latency, zero lost work under any LLM failure
  mode (Phase 1 of flush always lands an Activity-only note before
  Phase 2 runs).
- **Escape hatch:** set `LORE_BUFFER_FLUSH=0` (env, takes precedence)
  or `curator.use_buffer_flush: false` in `$LORE_ROOT/.lore/config.yml`
  to fall back to the legacy classify-per-chunk path. The legacy path
  stays compiled in (and tested via `tests/conftest.py`'s autouse
  grandfather fixture) until PR 4 of the
  `very-good-thats-the-mossy-lobster` plan deletes it.

### Deprecated

- **`lore_curator.summary_merge`** is deprecated. The buffer-and-flush
  path's Phase-2 LLM call (`lore_curator.synthesis.compose_session_note`)
  covers the same responsibility in one shot per session note. The
  module emits a one-shot `DeprecationWarning` on first call to
  `merge_descriptions` and stays load-bearing only when
  `LORE_BUFFER_FLUSH=0` keeps the legacy path active. Removal
  scheduled for PR 4.

### Migration notes

- Operators running on a low-capacity local LLM that struggled with
  the old anchored-merge prompt should observe a meaningful drop in
  per-heartbeat LLM load — but Phase 2 still uses a cloud-tier model
  by default (`curator.synthesis_model_tier="middle"`); shrink via
  `curator.synthesis_model_tier="simple"` per wiki in
  `.lore-wiki.yml` if local-only is required.
- The first heartbeat after upgrade in an active session writes a
  stub at `sessions/.../<slug>.md` that only carries Activity until
  Phase 2 finalises it. SessionStart polls up to 5 s for the close
  before injecting the wikilink — beyond that the banner reads
  "previous session note still being synthesised" and the wikilink
  appears in the next heartbeat.
- Half-rolled-back state is safe: an orphan buffer left behind by a
  v0.38.x crash gets picked up by the v0.39.0 reaper on the next
  curator pass; legacy notes already filed remain valid. One
  transcript may produce both a legacy note and a buffer-flushed
  note for the same day during cutover — acceptable.

## [0.38.2] - 2026-05-01

### Changed

- **SessionStart status line shows the scope, not a project wikilink**
  (`lore_cli/hooks._session_start_from_lore`). The identity bit between
  `lore <ver>: active` and `last note: …` was rendering
  `[[<repo-name>]]` (the project note's filename) — readable as a wiki
  citation but semantically wrong: it's the routing identity that
  matters at the top of a session. Status line now reads e.g.
  `lore 0.38.2: active · ccat:ops-db-api-client · last note: …`.
  Wikilink fallback preserved for legacy attachments where the offer
  has no `scope`. Regression test:
  `tests/test_hooks_v2.py::test_status_line_shows_scope_not_project_wikilink`.

## [0.38.1] - 2026-05-01

### Added

- **Attach wizard: one-click accept of parent-derived defaults** when
  the cwd is under an attached ancestor (`lore_cli/attach_cmd._config_wizard`,
  `_execute_attach`). Previously the wizard required 5 keystrokes to
  attach a child of an attached parent (Enter wiki → Enter scope → type
  backend → type y for offer file → Enter to proceed). Now it shows a
  single `[A]ccept / [s]tep through / [c]ancel` prompt with the proposed
  config (wiki=parent.wiki, scope=parent.scope:dirname, backend=github,
  write .lore.yml). Bare Enter accepts. Step-through preserves the old
  per-field flow with the suggestion still surfaced as defaults.
- **Default backend is now `github`** in the wizard's manual flow
  (was `none`). Offer-driven flows still inherit the offer's choice.

### Fixed

- **No phantom DRIFT immediately after the wizard writes a `.lore.yml`**
  (`lore_cli/attach_cmd._stamp_offer_fingerprint`). The wizard called
  `_do_manual` (which records `offer_fingerprint=None`) and then wrote
  the offer file, so the very next SessionStart reported "the offer for
  this repo has changed since you attached" — confusing noise the user
  couldn't reconcile because they'd just created the file. Fix: after
  writing the offer, parse it, compute its fingerprint, and re-add the
  attachment row with `source="accepted-offer"` and the matching `fp`.
  Best-effort: a parse failure leaves the row alone so a real DRIFT can
  still surface. Regression test in
  `tests/test_attach_interactive.py::test_one_click_no_drift_after_writing_offer`.

- **Curator B no longer files session-shaped notes into `sessions/`** when
  `SURFACES.md` declares `session` with `authored_by: curator_a`. The
  `_extractable_surfaces` filter (now public `extractable_surfaces`) was
  applied at the cluster step and merge-inventory step but missed in
  `lore_curator.abstract` — the high-tier LLM was offered `session` in
  its tool enum and naturally picked it for clusters of session notes,
  bypassing `session_writer`'s date-sharded layout. Surfaced on the ccat
  wiki. Fix:
  - `lore_core.surfaces.extractable_surfaces` / `is_curator_a_surface`
    promoted to public helpers.
  - `lore_curator.abstract` filters vocab, prompt, and tool-schema enum
    via `extractable_surfaces`.
  - `lore_curator.surface_filer.file_surface` raises `ValueError` when
    asked to file into a Curator-A-authored surface — defensive guard
    against future consumers missing the filter.
  - 3 new regression tests in `tests/test_abstract.py` and
    `tests/test_surface_filer.py`.

  Tracked in #43 (deeper question of dropping `session` from user-facing
  `SURFACES.md` entirely).

## [0.38.0] - 2026-05-01

### Added

- **Attach wizard suggests a child scope when a parent dir is attached**
  (`lore_cli/attach_cmd._ancestor_attachment_suggestion`,
  `AncestorSuggestion`). Running `lore attach` inside a nested directory
  whose ancestor is already attached (e.g. a repo under `~/orgs/ccat/`
  where `~/orgs/ccat/` is wiki=ccat, scope=ccat) now pre-fills wiki=ccat
  and proposes scope `ccat:<dirname>` as the default scope-picker choice.
  Walks up via `AttachmentsFile.longest_prefix_match` against `cwd.parent`
  (strict ancestor only); leaf segment uses `cwd.name` to keep the wizard
  offline. Suggestion is suppressed when an `.lore.yml` offer is present
  (offer wins) or when the user picks a wiki different from the
  ancestor's. Two new tests in `tests/test_attach_interactive.py`.

## [0.37.2] - 2026-05-01

### Added

- **Spawn pile-up detector — refuse fresh `_spawn_detached` while a prior
  child of the same role is still hung** (`lore_cli/hooks._prior_spawn_runaway`,
  `_process_is_ours`). The 60s cooldown stamp gates *new* spawns but
  doesn't notice that the previous child is still alive, so any future
  child-hangs bug (the v0.37.0 lock spin was one) re-creates the same
  pile-up pattern: every minute another child joins the broken state.
  The detector reads `<role>.meta.json` (written by `_proc_wrapper`),
  refuses the spawn when `exit_code is None` AND `start_ts` older than
  `cooldown_s * 5` AND the pid is still alive AND its `/proc/<pid>/cmdline`
  contains `lore_cli` (PID-recycle dodge). On detection a single
  `spawn-throttle outcome=prior-runaway` record is appended to
  `hook-events.jsonl`, throttled to once per `cooldown_s * 10` window
  via `curator-<role>.runaway.stamp` to avoid log spam on every
  UserPromptSubmit. Surfaces hung curators to `lore status` /
  `hook-events.jsonl` greps without auto-killing the orphan (too much
  foot-gun risk). 11 unit + integration tests in
  `tests/test_spawn_runaway_detector.py`. Related follow-up: issue #42
  (lockfile silent-cleanup hardening).

## [0.37.1] - 2026-05-01

### Fixed

- **Stale curator lock with orphaned `owner.json` no longer spins forever**
  (`lore_core/lockfile.curator_lock`). When a Curator A holder was killed
  by signal, its `finally:` cleanup never ran and `owner.json` survived
  inside the lock dir. The next acquirer detected the dir as stale by
  mtime, called `os.rmdir(lock_dir)` — which raises `OSError: Directory
  not empty` — and the bare-except + `continue` swallowed the failure
  into a tight `mkdir → exists → stale → rmdir-fails → continue` loop
  at ~90% CPU. Process pile-up followed (every 60s of spawn cooldown,
  another Curator A joined the spin), wiki pending buckets stayed
  unprocessed, and run jsonls truncated at `run-start`. Fix unlinks
  `owner.json` before `rmdir` in the stale-cleanup branch. New
  regression test
  (`tests/test_lockfile.py::test_stale_lock_with_owner_json_reclaimed`)
  uses a thread + bounded `join` so a future regression times out
  instead of hanging the test session.

## [0.37.0] - 2026-04-30

### Added

- **LLM-merged summary on Curator A appends**
  (`lore_curator/summary_merge.py`,
  `lore_core/session_writer.SessionInput.summary_merger`,
  `lore_curator/session_filer._make_summary_merger`). When a chunk
  merges into an existing same-day note, Curator A now asks the LLM
  via `merge_descriptions` to compose a 1-2 sentence summary that
  anchors on the existing framing and works the new chunk's context
  in. The merged value drives BOTH the body `## Summary` paragraph
  and the frontmatter `description`. Prior behaviour either clobbered
  the existing summary with the latest chunk's framing (Frankenstein
  fusion of unrelated topics) or, in the interim sticky-existing fix,
  silently dropped the new chunk's progress. The merger short-circuits
  without an LLM call when `existing` or `new` is empty (legacy notes,
  cascade-trivial chunks) and falls back to `existing` on any LLM
  failure — additive contract, never blanks an existing summary.
  Reuses the `cfg.models.middle` tier the noteworthy classifier uses
  so quality bar / cost are aligned. Test coverage in
  `tests/test_summary_merge.py` (12 cases) +
  `tests/test_session_filer.py::test_append_with_llm_client_*`.

### Changed

- **Topic-merge Jaccard threshold raised: 0.3 → 0.5**
  (`lore_core/session_writer._TOPIC_OVERLAP_MIN_JACCARD`). A real-world
  Frankenstein merge fused a GitHub-issue-curation session and a
  step_files plan session that incidentally shared one helper module
  (`hooks.py`) — Jaccard 1/3 ≈ 0.33 cleared the old gate. The new
  threshold preserves the two-of-three continuation case (`auth.py +
  auth_test.py + a` ↔ `auth.py + auth_test.py + b`, Jaccard 0.5) but
  rejects single-shared-file false-positives. Tests:
  `tests/test_session_filer.py::test_phase_c_single_shared_file_does_not_merge`
  and the updated overlap-merge case.
- **No-LLM filing path: summary stays sticky-existing on append**
  (`lore_core/session_writer.merge_body_sections`,
  `_append_to_note`). When `file_session_note` is called without an
  `llm_client` (tests, dry-runs, the explicit `/lore:session` path),
  the writer falls back to keeping the existing body Summary and only
  backfills frontmatter `description` on legacy notes that never had
  one. Previous behaviour clobbered both with each substantive append.

## [0.36.0] - 2026-04-30

### Added

- **LLM-gated commit→step attribution at Stop**
  (`lore_curator/closure_judgment.py`,
  `lore_cli/hooks.py:_attribute_commits_with_judgment`).
  New `closure_judgment` module asks the LLM via structured tool-use
  whether a single commit completes a plan step or merely touches its
  files. Returns `done`/`in_progress`/`skip` + confidence + reason.
  The Stop hook scans recent untrailed commits, intersects changed
  files with each active plan's `step_files`, and feeds non-trivial
  matches through `judge_closure`. On `done`/`in_progress` with
  confidence ≥ `_JUDGMENT_CONFIDENCE_FLOOR` (0.6) it auto-closes the
  step via `set_step` and emits a confirmation line. Trailer-bearing
  commits short-circuit the LLM via the existing path. Reuses the
  curator `LlmClient` infrastructure (subscription / SDK / OpenAI-
  compatible backends; configurable via `LORE_CLOSURE_JUDGMENT_MODEL`,
  default `claude-haiku-4-5-20251001`).
- **Stop → next-session pending-attributions bridge**
  (`lore_cli/hooks.py:_append_pending_attribution`,
  `_pending_attributions_block`). When LLM judgment returns `skip`,
  low confidence, or no client is available, the row is persisted to
  `~/.cache/lore/sessions/<sid>/pending-attributions.json`. The next
  SessionStart in the same repo reads every session's cache, filters
  to attributions referencing currently-active plans for the current
  repo, dedups across sessions, and surfaces them as a
  `## ⚠ Unresolved plan attributions from recent sessions` block in
  the SessionStart additionalContext. Closes the loop the user-
  terminal Stop warning couldn't — the *next* implementing model sees
  the attribution and can amend or `/lore:plan-step --done` to clear.

### Changed

- **Trailer demoted to override in Resume block**
  (`lore_cli/hooks.py:_render_one_plan_card`). The literal
  `Commit trailer: \`Plan: <slug>#<step>\`` line is replaced with
  `Step status: edits → in_progress; commits → LLM-judged. Override:
  \`Plan: <slug>#<step>\` trailer or \`/lore:plan-step --done\`.`
  The trailer literal stays in context (the override is still useful
  signal) but framed correctly: auto-attribution rides `step_files`,
  not "Claude must remember the trailer convention."

### Removed

- **Dead code from the prose-regex era**: `_FILE_PATH_RE`,
  `_extract_paths_from_text`, `_missing_trailer_nudges_for_stop`,
  `_suggested_step_for_card`, and the 11 tests that exercised them.
  Replaced wholesale by the `step_files` + LLM-judgment path. Lore's
  young; we don't carry the prose-regex fallback alongside the new
  path.

## [0.35.0] - 2026-04-29

### Added

- **Per-step file lists in plan frontmatter** (`lore_core/plans/types.py`,
  `lore_core/plans/canonical.py`, `lore_core/plans/markdown_adapter.py`,
  `lore_core/plans/writer.py`). The plan-authoring LLM declares a
  `Files:` line under each `### step-N:` heading (inline comma list or
  bulleted continuation); the parser populates `PlanStep.files`; the
  writer emits `step_files: {step-N: [paths]}` under plan frontmatter.
  Authoritative replacement for the regex-on-prose file extraction the
  Stop hook used. `step_files` is omitted from frontmatter entirely
  when no step declared files, so legacy plans don't acquire empty-dict
  noise.
- **Plan-authoring directive** in
  `lore_core/templates/integration-rules/default.md`. Instructs the
  plan-authoring model to enumerate files per step in either inline
  (`Files: lib/foo.py, tests/test_foo.py`) or bulleted form. Same
  directive flows into Cursor rules via the existing installer.
- **PostToolUse:Edit/Write/MultiEdit/NotebookEdit auto-flip**
  (`lore_cli/hooks.py:cmd_plan_edit_writeback`,
  `.claude-plugin/plugin.json`). New hook intersects the just-edited
  file with each active plan's `step_files` and flips matching
  `pending` steps to `in_progress`. Deterministic — no LLM call.
  Already-in_progress / done steps are left alone (idempotent). Closes
  the manual-only `pending → in_progress` gap surfaced during the
  trailer-demotion design pass.

### Changed

- **AI Journal SessionStart directive** rewritten with trigger-moment
  framing (work / yourself / user / conversation / just because) and
  the bar cue flipped from "don't write filler" to "default toward
  writing." Includes outward observations explicitly so the journal
  stops being a self-improvement log.
- `ActivePlanCard` gains a `step_files` field (per-step path lists)
  parsed from frontmatter for downstream consumers.

## [0.34.0] - 2026-04-29

### Fixed

- **Per-session commit attribution** (`lore_curator/session_activity.py`,
  `lore_curator/session_filer.py`). Replace the time-window
  `git log --since/--until` query with SHA capture from this chunk's own
  Bash `git commit` tool-results. Fixes two failure modes from one root
  cause: the **inter-chunk gap** (commits whose committer-date falls
  between Curator A's chunk windows were silently dropped) and
  **parallel-session bleed** (commits in the same repo fanned out to
  every session whose chunk window happened to overlap). The transcript
  is now the authoritative source for session ↔ commit identity. New
  helper `_commit_shas_from_bash_results` extracts SHAs via tokenised
  `git commit` detection and `tool_call_id`-keyed result pairing
  (parallel `tool_use` blocks return results in arbitrary order, so
  adjacency is not enough). New `collect_commits_by_sha` resolves SHAs
  via `git cat-file --batch-check` then `git log --no-walk -z`, dropping
  unresolvable SHAs silently. New telemetry event `commit-shas-captured`
  in run logs.

## [0.33.0] - 2026-04-29

### Added

- **Cursor is now a first-class lore integration.** `lore install --integration cursor`
  materializes a packaged Cursor 2.5+ plugin at `~/.cursor/plugins/local/lore/`
  with manifest, sentinel, copied skills tree, copied rules tree,
  plugin-local `mcp.json`, and a generated `hooks.json` mapping all six
  Claude Code hook events to Cursor's schema (`SessionStart→sessionStart`,
  `PreCompact→preCompact`, `UserPromptSubmit→beforeSubmitPrompt`,
  `SessionEnd→sessionEnd`, `Stop→stop`,
  `PostToolUse:ExitPlanMode→postToolUse{matcher: "ExitPlanMode"}`).

- **Three Cursor-specific advisory checks in `lore doctor`** — plugin-dir
  state + manifest version match, MCP `command` resolves to an existing
  executable (catches sticky-abs-path drift after pipx upgrade), and
  `hooks.json` parses with non-empty events. All gated on `~/.cursor/`
  existing so Claude-only users see clean "skipped" lines.

- **Cursor installer dispatcher detects GUI-only installs** via
  `~/.cursor/` directory existence, in addition to `shutil.which("cursor")`.
  AppImage / `.deb` / `.dmg` users no longer slip through.

- **`CURSOR_PROJECT_DIR` env-var fallback** in `_resolve_cwd()` and
  `_resolve_cwd_capture()`, defensive for older Cursor versions where
  the documented `CLAUDE_PROJECT_DIR` alias hasn't been wired.

### Fixed

- **`spawn lore ENOENT` from Cursor's MCP client.** `lore_mcp_entry()`
  now resolves `shutil.which("lore")` to an absolute path; Cursor's
  GUI subprocess inherits a minimal PATH from systemd / desktop
  launchers and can't find pipx-installed binaries by bare name.

- **Schema-version drift no longer prompts for path-only fixes.** When
  an existing `mcpServers.lore` entry differs from canonical only in
  the `command` field (relative `lore` → absolute path) and is
  recognizably lore-managed (args == `["mcp"]`, no surprise keys),
  the installer emits `KIND_MERGE` (silent re-merge) instead of
  `KIND_REPLACE` (prompt). User-customized entries with wrapper
  scripts, extra args, or env vars still get the prompt.

### Changed

- **Plugin-local `mcp.json` is canonical** when plugin packaging
  succeeds; the installer skips the global `~/.cursor/mcp.json` merge
  in that mode and emits a delete for any pre-existing legacy global
  entry to dedupe registration. Legacy global-mcp.json fallback still
  works when the lore source-of-truth doesn't resolve.

### Notes

- Skill name divergence — Cursor sanitizes `:` → `-` in SKILL.md
  `name:` fields silently. Lore keeps canonical `lore:foo` (Claude
  Code shows `/lore:journal`); Cursor users see `/lore-journal`
  natively. Keystroke divergence accepted; not breaking anyone's
  muscle memory by renaming.

- Pipx-installed `lore` binary needs `pipx install --force <repo>`
  after pulling this release for `lore install` (without `python -m`)
  to use the new installer logic. Runtime hook commands didn't change,
  so existing installs keep working.

## [0.32.0] - 2026-04-29

### Added

- **`lore briefing` now coordinates the wiki git repo across teammates.**
  Before gather, `auto_pull` fast-forwards the wiki repo so we see
  teammates' ledger marks. After mark, the ledger update is committed
  (`briefing(<wiki>): incorporated N session(s)`) and pushed via
  `auto_push`. Single-user wikis without a remote continue silently.
  Aborts on dirty or diverged working trees with a clear remediation
  message — no more silent double-publish when a teammate forgot to
  push their ledger update.

- **`--no-git` flag** opts out of coordination (testing, intentionally
  out-of-sync repos, local-only flows). `--dry-run` already skipped
  publish and now also skips git ops.

### Notes

- Power-user subcommands (`gather` / `publish` / `mark`) are unchanged
  — operators using the manual flow handle their own git. Coordination
  applies only to the one-shot pipeline (`lore briefing --wiki <name>`).
- Residual race not addressed: if A pulls clean, publishes, but hasn't
  pushed yet, B can still pull the same pre-publish state and
  double-publish. A real fix needs a remote lease (e.g. claim-commit
  before publish). Documented as a known limitation; out of scope for
  this release.

## [0.31.0] - 2026-04-29

### Added

- **`lore briefing --wiki <name>` is now a one-shot pipeline.** Gathers
  new sessions since the last briefing, composes prose via the
  configured LLM backend, publishes through the wiki's sink
  (`.lore-briefing.yml`), and updates the ledger — all without a
  separate LLM round-trip from the calling skill. The `/lore:briefing`
  skill collapses to a thin wrapper around this command. Power-user
  subcommands (`gather`, `publish`, `mark`) remain for scripted flows.
- **LLM-composed briefing prose.** New
  `lore_core.briefing.compose_briefing_prose` builds a bounded prompt
  (capped to 60 sessions, summary truncation, includes
  `summary` + `what we worked on` + `decisions made`) and produces the
  structured `### What happened / Key decisions / Open items / Vault
  health` shape the `/lore:briefing` skill used to hand-author. The
  CLI auto-detects an LLM backend via the existing
  `make_llm_client()` (subscription `claude` binary →
  `ANTHROPIC_API_KEY` SDK → openai-compatible from
  `.lore/config.yml`) and uses the wiki's `models.middle` tier.
- **Deterministic fallback render.** New
  `lore_core.briefing.render_briefing` emits a bullet-list digest
  using each session's `summary` frontmatter (or first bullet of
  "what we worked on" / `description` fallbacks). Used automatically
  when no LLM backend is configured, the model errors, or the user
  passes `--no-llm`. Briefings always publish; the LLM is an
  enhancement, not a gate.
- **`--dry-run`, `--no-mark`, `--no-llm`, `--since`, `--sink` flags**
  on the unified command for preview / republish / deterministic /
  ledger-floor / sink-override workflows.

### Fixed

- **Sharded session layout in `lore_core.briefing.gather`.** The date
  parser only recognized the flat legacy form
  `sessions/YYYY-MM-DD-slug.md`, silently skipping every team-mode
  sharded note (`sessions/[<handle>/]YYYY/MM/DD-[HHMM-]slug.md`). On a
  real ccat wiki this meant briefings reported "no new sessions" even
  with 41 unbriefed notes on disk. New `_parse_session_path()` helper
  tries flat first, then derives the date from `YYYY/MM` parents plus
  the 2-digit `DD` prefix; slug strips the optional 4-digit `HHMM-`
  segment.
- **`lore briefing publish` no longer hangs on a TTY.** When invoked
  without `--file` or piped input, it now errors with a clear
  "pipe markdown on stdin or pass --file" message instead of blocking
  on `sys.stdin.read()` indefinitely.

## [0.30.0] - 2026-04-29

### Fixed

- **Mid-session transcript registration closes a SessionStart race.**
  When `SessionStart` sampled the projects directory in the sub-second
  window before Claude Code had created the transcript file, the
  transcript was never registered in the ledger and curator A had
  nothing to digest until `SessionEnd` fired. Long sessions could run
  for hours without ever filing a note (real symptom: a 5-hour
  step-ca session with a 7 MB transcript and zero session note).
  `cmd_user_prompt_submit` now re-lists transcripts on every prompt
  and `bulk_upsert`s into the ledger via the shared
  `_register_pending_transcripts` helper (also used by `capture`).
  `last_mtime` updates propagate so `pending()` / the heartbeat
  spawn-gate see work growing across the session, enabling true
  semantic mid-session capture rather than waiting for session
  boundaries.

### Changed

- **Session-note merges refresh summary + description.** Previously
  the first chunk's `## Summary` paragraph and frontmatter
  `description` were frozen forever — subsequent appends only added
  bullets. With mid-session inline filing this leaves the note framed
  as "started X" long after the work converged on "completed X across
  N services". `merge_body_sections` now takes the latest non-empty
  summary; `_append_to_note` mirrors the update for frontmatter
  `description`. Title (and thus slug/filename) stays sticky to avoid
  orphaning wikilinks. Empty-summary chunks are defensively ignored,
  so cascade-trivial appends never clobber a real summary with `""`.

## [0.29.0] - 2026-04-29

### Added

- **Generic plan ingestion architecture.** Replaces the closed regex
  union (5 brittle patterns) with a typed shape classifier + structured
  envelope path + producer-keyed adapter registry, designed for lore
  to act as the glue layer between AI coding tools (Claude Code today,
  Cursor / Aider / Cline / custom CI tomorrow).
  - **`canonical.py`** is the single source of truth for the step
    heading + ID format. Three modules previously held their own
    regex copy; now they all import from here.
  - **`shapes.py` + `markdown_adapter.py`** — typed verdicts
    (`ShapeATXSteps`, `ShapeHierarchical`, `ShapeNumberedList`,
    `ShapeCheckboxList`, `ShapeAmbiguous`, `ShapeUnknown`) plus a
    structural classifier that asks one question — *do ≥2 sibling
    headings at one level form a monotone sequence under any
    interpretation?* — subsuming all five legacy regexes plus the
    hierarchical case (`## Phase N` + `### N.M`) that triggered the
    redesign.
  - **`envelope.py`** — `lore.plan.envelope/1` schema with a
    hand-rolled validator. Tools that can emit JSON skip markdown
    shape detection entirely.
  - **`adapters/`** — producer-keyed registry (`claude-code` shipped;
    `cursor` stub) so future tool dialects plug in via a small
    adapter without churning the parser.
  - **`ingest.py`** — single producer-facing entrypoint
    (`ingest_plan(IngestSource)`) that routes envelope / hook /
    markdown to the right path and returns an `IngestResult` with
    typed `confidence` + structured warnings.
- **`lore plan file --json <path>`** — CLI command for filing a plan
  via the envelope schema. Validates the envelope, writes the canonical
  plan note. (Markdown filing remains under `lore plan import` and
  the ExitPlanMode hook.)
- **`lore_plan_file` MCP tool** — envelope-only ingestion path for
  agents. Markdown filing stays hook-only by design — agents wanting
  to file via MCP must produce structured input.
- **`lore plan migrate-ids`** CLI command — one-shot legacy
  `### s<N>:` → canonical `### step-<N>:` rewrite across every plan
  in every wiki under `$LORE_ROOT`. Idempotent; preserves mtime when
  no changes needed; skips non-plan files and malformed YAML
  gracefully; `--dry-run` and `--json` flags supported.
- **Hierarchical step grouping.** Plans authored with `## Phase N
  — title` containers + `### N.M` leaves now lift the leaves into
  flat `step-1..step-N` IDs and stamp each with `PlanStep.group =
  "Phase N — title"`. The hierarchical structure survives the IR
  flattening for downstream consumers that want to render groups.
- **FTS query telemetry** (`$LORE_CACHE/query-log.jsonl`) — every
  `lore_search` call writes one JSONL record capturing both the AND
  attempt count and the OR fallback count, so weight/recall regressions
  are observable. Reindex-throttle skips share the sink as
  `event: "reindex_skip"` records.
- **Catalog `slug_index`** — `lore lint` now writes a top-level
  `slug_index: {stem: relpath}` to `_catalog.json`, giving the MCP
  server O(1) wikilink resolution. Duplicate stems within a wiki
  produce a `duplicate_stem` lint WARNING listing all colliding paths
  so the user can rename one (ambiguous slugs break wikilink
  resolution; first-by-sort-order wins until you fix it).
- **`lore_drill` truncation transparency.** When the expand cap fires,
  the `read_expanded` trace stage now records `truncated_slugs: [...]`
  listing the dropped links. New optional `expand_only: list[str]` arg
  intersects the discovered wikilink set with this list, letting an
  agent re-drill exactly the dropped slugs without recomputing
  search/expand.
- **`lore_read` section extraction.** New optional `section: str` arg
  returns just one H2 section (first match in document order,
  case-insensitive substring; nested H3+ included). Code-fence aware:
  `## not a heading` inside ` ``` ` or `~~~` blocks is correctly
  ignored. Useful for long surface notes when only one heading is
  needed.
- **`lore_core/errors.py`** — canonical MCP error envelope helper
  (`mcp_error`) plus error-code constants (`NO_VAULT`, `WIKI_NOT_FOUND`,
  …) shared by `lore_core` handler modules and the MCP server.

### Changed

- **Canonical plan step heading + ID renamed.** On disk: `### s<N>:`
  → `### step-<N>:`. Step IDs: `s1`/`s2`/… → `step-1`/`step-2`/…
  Wikilinks: `[[plan/slug#s2]]` → `[[plan/slug#step-2]]`. Commit
  trailers: `Plan: slug#s2` → `Plan: slug#step-2`. The on-disk shape
  is now self-explanatory. **Read compat is permanent** — historical
  `s<N>` trailers in git logs and unmigrated plans on disk continue to
  resolve forever (the trailer regex and registry both accept either
  shape). The piecemeal-migration writer also rewrites legacy plans on
  the next re-capture; for vaults that won't re-capture every plan,
  `lore plan migrate-ids` does the same in one pass.
- **Plan parser renamed shapes:** the `mode` field on `StructuredPlan`
  now reports `"hierarchical"`, `"checkbox"`, `"envelope"` in addition
  to the legacy `"headings"` / `"list"` / `"single"` strings. Pure
  telemetry; consumers that branch on `mode` should match the new set.
- **`lore_core/plans/parser.py` shrunk to a 70-line compatibility
  shim.** The 480-line monolith with closed regex union is gone;
  step detection lives in `markdown_adapter.py`, hook-payload
  extraction in `adapters/claude_code.py`, the dispatcher in
  `ingest.py`. Public API (`parse`, `parse_payload`) preserved
  verbatim — existing imports keep working.
- **Plan ATX titles now strip leading `:`/`—`/`–`/`-`/`.` separators**
  from titles after the prefix (e.g. `### P1 — Foundation` →
  title=`"Foundation"`, not `"— Foundation"`). Cleaner rendering.
- **FTS search now AND-first, OR-fallback.** `_sanitize_fts_query`
  builds an AND-style MATCH (FTS5 implicit AND) with each token
  double-quoted; the OR-joined variant is used only when AND yields
  zero hits. Quoting also neutralises FTS5 keywords (`AND`, `OR`,
  `NOT`, `NEAR`) — user queries containing those words no longer
  crash with `sqlite3.OperationalError`.
- **Bare-string error envelope migration complete.** All `lore_core`
  handler modules (`resume`, `inbox`, `briefing/gather`, `lint`) now
  return the structured `{"error": {"code", "message", "next"}}`
  shape. The previous bare-string `{"error": "..."}` returns are
  gone. CLI callers updated to format `error.message` instead of
  printing the dict.
- **`_resolve_slug` is now three-tier:** `slug_index` (O(1)) → section
  iteration (fallback for pre-`slug_index` catalogs, removed in v0.31.0)
  → rglob (last resort for uncatalogued notes — drafts, inbox,
  freshly-written). The rglob fallback stays indefinitely; without it
  `lore_drill` would silently drop wikilinks to brand-new notes.

### Removed

- **Silent single-mode plan filing.** When the markdown shape
  classifier can't recognize a step structure, the
  `PostToolUse:ExitPlanMode` hook **refuses to file** instead of
  silently dropping a one-step degraded plan into the wiki (the bug
  that triggered this whole redesign). The hook surfaces a structured
  `systemMessage` to Claude Code naming the failure code
  (`shape_unknown` / `shape_ambiguous`) and example heading shapes
  the next attempt should use. `lore lint` flags any
  `ingest_confidence: fallback` plan that did slip through other
  paths. Pre-1.0; no deprecation cycle. Plans authored with
  recognizable structures (ATX headings with Phase/Step/P/S/numeric
  prefixes, hierarchical `## Phase N` + `### N.M`, top-level numbered
  lists, checkbox lists) keep working unchanged.
- **`StructuredPlan.mode == "single"`** is no longer a load-bearing
  filing path. The IR keeps the field for telemetry but the writer
  never emits a step-less plan note from real producers.
- **The cross-module `_STEP_HEADING_PATTERNS[1]` index reach in the
  writer is gone.** Step-ID extraction now goes through
  `canonical.extract_step_ids` everywhere — reordering parser
  patterns is safe again.
- **Minimal JSON-RPC MCP fallback** (`_run_minimal_server`) — deleted.
  The `mcp` SDK is now a hard dependency in `[project].dependencies`
  rather than an optional `[mcp]` extra. **Breaking install change:**
  users who installed via `pip install lore` (without `[mcp]`) need to
  reinstall to pull in the SDK; subsequent installs gain it
  automatically.

## [0.28.0] - 2026-04-29

### Fixed

- **Briefing sinks now load `.lore-briefing.yml` end-to-end (closes
  #15, partial #4).** Previously `lore briefing publish --sink matrix`
  required `LORE_MATRIX_*` env vars at every invocation even when the
  wiki had a populated `.lore-briefing.yml`. The publish path now
  threads the parsed yaml through the sink dispatcher.

  Changes:
  - `dispatch(uri, text, config=None)` — sink registry sender
    signature gains a `config` arg (`Sender = Callable[[str, str,
    dict | None], None]`).
  - `lore briefing publish --wiki <name>` — new flag that loads
    `<wiki>/.lore-briefing.yml` and passes it to the sink. Without
    `--wiki`, behaviour is unchanged (env-only).
  - Matrix sink: resolves `homeserver`/`user_id`/`room_id` via env >
    yaml > error, mirroring the `curator.openai` precedence in
    `root_config.py`. Nested `matrix:` block is the recommended
    schema; flat top-level keys still work (one-time deprecation
    warning per process).
  - Markdown sink: URI target still wins, falls back to
    `markdown.path` in yaml, errors if both absent.
  - Sink mismatch (`--sink` ≠ yaml's `sink:`) now refused with
    `SinkConfigMismatchError` (CLI exit 2).
  - Curator B's auto-publish forwards `gather_result["sink_config"]`
    into `dispatch`, so daemon-driven publishes no longer require
    env vars on the curator host.
  - Skill `/lore:briefing` updated to call `lore briefing publish
    --sink <name> --wiki <wiki>`.

  New docs: `docs/how-to/matrix-bot.md` (end-to-end recipe).
  `docs/architecture/config.md` adds `.lore-briefing.yml` as
  configuration source #5.

## [0.27.0] - 2026-04-28

### Fixed (BREAKING — `.lore.yml` walk-up semantics)

- **`.lore.yml` no longer auto-applies to descendant directories.**
  Closes #24 ("Stop filesystem walk-up in find_lore_yml to prevent bleed
  into unattached repos"). Previously a nested git repo silently
  inherited a parent's `.lore.yml`, triggering unsolicited SessionStart
  prompts and wizard flows in repos the user never attached. The walk-up
  is now gated by an explicit opt-in: an ancestor `.lore.yml` only
  applies to descendants when it sets **`inherit: true`**.

  **Migration**: setups where one `.lore.yml` covers multiple sibling
  repos under a workspace directory (e.g. `/home/.../orgs/ccatobs/`)
  need to add `inherit: true` to that file to keep working. Without it,
  descendants will see no offer and Lore will be inert in them — which
  is the correct behavior for the bleed case but breaks intentional
  workspace setups until the flag is added.

  Already-attached subtrees are unaffected: once a repo is registered
  in `attachments.json`, descendants continue to classify as ATTACHED
  regardless of `inherit`. The flag only governs the OFFERED-prompt
  surface, which is where the bleed entered.

  Also adds `find_lore_yml_raw` for diagnostic-only path discovery and
  improves the `lore attach accept`/`decline` error message when the
  hit case is "parent without `inherit: true`" rather than "no file
  found at all". The wizard prints "Inherited from {path}" when the
  applicable offer was discovered via inheritance.

## [0.26.0] - 2026-04-28

### Changed (BREAKING — config field rename)

- **Curator A spawn gate is now turn-aware with an age fallback, and
  fires from `UserPromptSubmit` (every prompt) in addition to session
  boundaries.** A 4-hour active session in a single repo could
  previously sit on megabytes of unfiled work because `threshold_pending`
  counted *transcripts* (one per session, growing in place) rather
  than turns. The new gate is per-wiki:

      sum(total_turns − digested_index_hint) ≥ threshold_pending_turns
        OR  oldest pending mtime ≥ max_pending_age_s

  Defaults: `threshold_pending_turns: 30`, `max_pending_age_s: 600`.
  The `threshold_tokens: 50_000` field (declared in `wiki_config.py`
  since v0.6 but never read by the spawn-decision code) is dropped —
  turns are the unit of work.

  **Migration**: existing `.lore-wiki.yml` files setting
  `threshold_pending` or `threshold_tokens` will warn-and-ignore on
  load (the existing unknown-key path in `_load_with_warnings`).
  Rewrite to `threshold_pending_turns: 30` for the rough equivalent —
  or lower for snappier filing.

- **Session-end / pre-compact handover guarantee.** Both events now
  unconditionally spawn Curator A when there is *any* pending work,
  bypassing the gate. This stops short sessions from leaving unfiled
  work stranded for the next session-start to clear. Outcome label
  in `hook-events.jsonl` is `spawned-curator-eos` to distinguish
  from the gate-driven `spawned-curator`.

### Added

- **`llm_backend` and `llm_model` provenance in note frontmatter.**
  Curator A and Curator B notes now record which LLM produced them
  (e.g. `llm_backend: openai`, `llm_model: Mistral Small 4 119B 2603 KI:EZ`).
  Makes regression triage possible after backend swaps. Empty on
  cascade-trivial early-return paths (no LLM was called).

- **`lore doctor` `pending` row.** Surfaces gate-readiness per wiki
  at a glance: `<wiki>: N pending, T/threshold_t turns, oldest A/B s
  [spawn|wait]`. Answers "why isn't curator A firing?" without
  ledger grep.

- **`total_turns` field on `TranscriptLedgerEntry`.** Stamped by
  `transcripts sync` when files change (or backfilled lazily for
  legacy entries). Drives the gate without per-evaluation file I/O,
  so the every-prompt heartbeat stays cheap.

- **Mid-session `_heartbeat_spawn_curator_a`** — every
  UserPromptSubmit hook evaluates the gate for the current wiki and
  spawns Curator A on its own 120s cooldown. Combined with the
  existing 60s spawn-lock, two layers of rate-limiting prevent
  storms regardless of prompt cadence.

### Fixed

- **Crash log no longer pollutes `~/.cache/lore/crashes/` from
  pytest runs and `--dry-run` debug invocations.** Today's
  `lore doctor` reports "21 hook crashes in 7 days" because test
  fakes that simulate hook failures (e.g.
  `tests/test_directive_template.py`) write through to the real
  cache, and any `lore curator run --dry-run` that fails for a
  transient config reason adds another. `_crash_log.write_crash`
  now skips the write when `argv[0]` contains `pytest` or
  `--dry-run` is in argv.

## [0.25.1] - 2026-04-28

### Fixed

- **Stop hook missing-trailer nudge no longer bleeds across sessions or
  fans out per commit.** When a parallel session was working a plan
  without `Plan:` trailers, every Stop in an unrelated session emitted
  one ⚠ line per recent commit (8+ lines was typical). Two fixes in
  `_missing_trailer_nudges_for_stop`:
  - **Cross-session bleed guard** — drop commits whose committer-time
    predates the session's transcript first record. Synthetic test
    sessions and pid-fallback (no transcript on disk) skip the filter
    so existing behavior is preserved.
  - **Coalesce per `(slug, anchor)`** — accumulate matching SHAs into a
    single line (`⚠ N commits (sha1, sha2, ...) touched files in
    plan/<slug>#<anchor> ...`) capped at 5 SHAs + `+N more` tail. The
    seen-set still keys per-SHA so re-runs in the same session stay
    quiet.

## [0.25.0] - 2026-04-28

### Added

- **AI + human journal side-chains** — opt-in feature flag, off by
  default. Two newest-first markdown logs at the top of the vault:

      $LORE_ROOT/journals/ai.md      — model writes whatever it likes
      $LORE_ROOT/journals/human.md   — user scratch pad

  These are deliberately **non-derived** channels — observations,
  criticism, half-formed ideas, jokes, weather. Distinct from the
  per-day `journal` *surface* (auto-extracted by Curator B into a
  wiki). The journals here sit outside the curator/surface graph
  on purpose — the bar to write must be near-zero or the channel
  fails. No frontmatter, no schema, no extraction pass.

- **CLI** — `lore journal write [--ai|--human] "<text>"`,
  `lore journal read [--ai|--human] [-n N]`, `lore journal
  enable|disable|status`. Author tag auto-resolves from
  `LORE_USER_HANDLE` / git config / email local-part for humans;
  `LORE_AI_AUTHOR` / `CLAUDE_MODEL_ID` for the model.

- **MCP tools** — `lore_journal_write`, `lore_journal_read`. Always
  registered (so the model can be told about them via prompt), but
  the SessionStart invitation only fires when the flag is on.

- **SessionStart prompt fragment** — when `journal.enabled` is set
  in `$LORE_ROOT/.lore/config.yml`, the directive cluster gains an
  AI Journal invitation: "*you may call `lore_journal_write` any
  time you have an observation, criticism, idea, joke, or weather
  note that would otherwise be lost. Bar: would this be lost
  otherwise, not does this serve the user. Don't write filler.*"

- **`/lore:journal` skill** — thin dispatcher over the CLI:
  `/lore:journal` reads, `/lore:journal "<text>"` appends a human
  entry, `/lore:journal ai` reads the AI side.

The journals are intentionally outside the wiki/curator pipeline
because the entire premise is *anti-extraction*: this is the
release-valve channel for things that wouldn't otherwise be
captured. Auto-curation would defeat that.

## [0.24.0] - 2026-04-28

### Added

- **Resume block surfaces the canonical `Plan:` trailer literal.**
  Layer A in v0.23.0 made plans self-close *if* commits carry
  `Plan: <slug>#sN` trailers. The reliability of that promise
  depended on the model remembering the convention at commit time —
  a textbook LLM forgetting failure mode. Each active-plan card in
  the SessionStart Resume block now ends with:

      Commit trailer: `Plan: <slug>#<step>`

  Anchored to the in-progress step (or first pending). The exact
  string the model needs to paste is in context for the whole turn.

- **Stop hook flags commits that touched plan files without a
  `Plan:` trailer.** A second pass in the Stop hook identifies
  commits made this session that:

  1. Touched at least one file path that appears verbatim in an
     active plan's step body, AND
  2. Carry no `Plan:` trailer in the commit message.

  Each such commit produces a soft prompt:

      ⚠ commit X touched files in plan/<slug>#sN but has no `Plan:`
        trailer — add `Plan: <slug>#sN` to a follow-up commit?

  The suggested step is the plan's current in-progress step (or
  first pending). Per-session seen-set namespaced separately from
  the action pass — `<sha>!missing#<slug>#<step>` — so the prompt
  fires once per session and doesn't conflate with the auto-advance
  log entries. Soft prompt only: never auto-mutates anything (the
  inference is heuristic; only explicit trailers trigger writes).

  Together with the Resume-block literal, these reinforce the
  convention at two distinct moments: *before* the model writes a
  commit (literal in context), and *after* it forgets (specific,
  actionable nudge in the next turn's input).

### Compat

- The Stop hook adds two short `git log` / `git show` calls per
  recent-commit batch. Both are wrapped in 5-second timeouts and
  best-effort try/except so a slow git or missing repo never blocks
  Stop. Capped at 20 most-recent commits per scan.

## [0.23.0] - 2026-04-28

### Changed

- **Plans now self-close from commit trailers — no manual
  `/lore:plan-step` needed.** Two coordinated changes close a gap
  observed when a sibling session shipped the drain-notification-chain
  plan in `9cece6b`: the implementation landed but the plan note stayed
  `status: active` with empty `step_status`, because the chain from
  commit-trailer → step-status mutation → plan-status flip had three
  manual hops in it.

  - **Layer A — Stop hook auto-advances on trailers.** Until 0.22.0
    the Stop hook scanned recent commits for `Plan: <slug>#sN`
    trailers and emitted suggestion lines like `⚠ commit X references
    plan/Y#sN — /lore:plan-step Y sN --done?`. The trailer is already
    a binding promise from the author, so the hook now calls
    `set_step(slug, sN, DONE)` directly and emits a confirmation
    line: `✓ marked plan/Y#sN done from commit X`. The per-session
    seen-set still prevents re-firing on every Stop. Per-trailer
    try/except so a malformed plan can't cascade into the next
    plan's processing.

    Removed the `is_nudge` timestamp gate from this codepath: it
    was right for the manual-advance flow but caused same-second
    sibling commits to be silently filtered out once the first
    advance bumped `step_status_updated`. The per-step done check
    plus the seen-set are sufficient and don't have the
    same-second collision.

  - **Layer B — `set_step` auto-flips plan status when the last step
    lands.** Inside `_mutate_under_lock`: if the mutation is a
    forward step→done transition AND every parsed step ID is now
    `done` AND the current top-level status is `active`, set
    `fm["status"] = "done"` and bump `last_reviewed`. One-way
    ratchet — clearing a previously-done step (`status=None`) does
    NOT revert the plan to active. Manual terminal statuses
    (`superseded`, `abandoned`) are left alone.

  Net effect: a developer / model who writes `Plan: <slug>#sN` in
  commit messages gets the entire plan-as-authority machinery for
  free. End of turn: trailers found → step_status updated → final
  step lands → plan auto-closes → drops out of `lore_plan_active`,
  `plans/_recent.md` shows `· done`, `_session_start_plans` resume
  block stops surfacing it. Stale plans (active with no progress)
  remain a curator concern, deliberately out of scope here.

### Compat

- `_active_plans_resume_block`'s "All steps done — `/lore:plan-advance
  --complete`?" suggestion is now an edge-case path. It still fires
  when a user manually re-opens an auto-closed plan
  (`status: done → active`), but the typical flow no longer reaches
  it. Tests pin both the new auto-drop behavior and the manual-reopen
  fallback.

## [0.22.0] - 2026-04-28

Maintenance + observability batch. Two themes:

1. **Crash visibility.** Hook failures now persist a traceback to disk
   so the user can attach it to a bug report; `lore doctor` surfaces
   recent crashes; the top-level main() backstop catches failures
   that escape the per-hook shield.
2. **Drain hygiene + cursor correctness.** Authoritative `_system`
   cursor + emit-side guard + `lore drain prune` CLI for cleaning up
   legacy orphan rows. Plus a clutch of linter-exemption fixes that
   were generating spurious warnings on real session notes.

### Added

- **Hook crash logging.** Every hook crash now persists a timestamped
  traceback under `$LORE_CACHE/crashes/<ISO>-<HookEvent>.log` so the
  maintainer / user can attach the file to a GitHub issue. Two layers:

  - `_shield_hook` (per-hook decorator) writes the log and folds the
    path into the friendly banner Claude Code sees:
    `Full traceback: <path>`.
  - `__main__.main()` adds a top-level except backstop that catches
    failures escaping the shield (import errors, typer parameter
    resolution, anything before the hook body runs). Hook-shaped
    callers still get the JSON envelope; non-hook callers get a
    one-line stderr advisory plus the log path.

  Best-effort: if the cache dir isn't writable, `write_crash` returns
  `None` rather than triggering a second crash inside the handler.

- **`lore doctor` reports recent crashes.** New advisory check —
  surfaces the count + most recent hook event when files exist under
  `$LORE_CACHE/crashes/` within the last 7 days. Flips `ok=False` but
  doesn't fail the install (informational).

- **`lore drain prune`** (with `--dry-run`). New CLI for maintenance
  of the per-vault drain stores. Walks `_system.jsonl` and atomically
  drops `note-filed` / `note-appended` / `surface-proposed` rows
  whose referenced path is gone. Append-only by design; `prune` is
  the explicit escape hatch for cleanup. Atomic via
  `_system.jsonl.tmp` + `os.replace`.

- **Producer-side guard in `DrainStore.emit`** rejects rows targeting
  `_system.jsonl` from non-system callers. Only `transcript-synced`
  events legitimately target `SYSTEM_SESSION`. Prevents new pollution;
  `lore drain prune` excises legacy rows that predate the guard.

- **Linter wikilink-discipline exemptions.** `_is_non_note_link_target`
  predicate suppresses `broken_link` warnings for targets the session-
  template wikilink discipline already forbids: file/dir paths, PR/issue
  refs, URLs, env vars, version strings. Concept-style names still
  flag — those are real candidates either to be promoted into notes or
  to be removed by the author.

- **`papers/` exempt from `oversized` check.** Each note in `papers/`
  is one paper by design; flagging them as split candidates was noise.

### Changed

- **Authoritative system cursor.** `DrainStore.read_or_init_cursor()`
  cold-starts the system-events cursor to `now` so a fresh install
  never reaches back through history. The system cursor becomes the
  single "shown through" mark for SessionStart, heartbeat, and
  `lore news`. Replaces the per-pid cursor file for system events;
  parallel Claude windows no longer steal each other's notifications.
  Session cursor stays pid-keyed because that's per-process state.

- **`_render_drain_lines` skipped under `--probe`** so `lore doctor`
  stays side-effect-free. The cold-start init writes a cursor file
  on first read, which a probe must not do.

- **Hierarchy check tightened: same-named folders in different
  knowledge dirs no longer pool.** A `concepts/lore/` folder no longer
  implies an index-relationship with `decisions/lore/` — the namespace
  match was a bug producing spurious `index_too_large` /
  `unlinked_subnote` warnings.

- **Index detection requires ≥ 70% of siblings actually link to the
  prefix-matched note** (`INDEX_PREFIX_LINK_RATIO`). A long topical
  note that happens to share a name prefix with its folder is no
  longer auto-promoted to "the index", which was generating
  obligation warnings for children that had no reason to backlink.

### Compat

- New optional dependency: none. `_crash_log` and `drain_cmd` live in
  `lore_cli/` and use only stdlib.

## [0.21.0] - 2026-04-28

### Added

- **Intra-day session ordering via `<DD>-<HHMM>-<slug>.md` filename
  shape.** Previously two sessions on the same day sorted
  alphabetically by slug, making it impossible to tell at a glance
  which one was newer. Session files now carry a 4-digit zero-padded
  24h start time after the day prefix. The `_recent.md` index reflects
  this immediately. Layout invariant in
  `lib/lore_core/session_writer.py:14`.

- **`plans/_recent.md`** — generated by `lore lint` alongside
  `sessions/_recent.md`. Lists up to 20 plans ordered by
  `max(last_reviewed, step_status_updated)` with a `· <status>` badge
  on every line so done/abandoned/superseded plans are visually
  distinct. Slug-stable: filenames don't move (commit trailers and
  wikilinks keep resolving), but the index gives a "latest first" front
  door so flat-list rot is no longer a problem as the vault grows.

- **`session_path_sort_key(path)`** in `lore_core.session_writer` —
  layout-aware sort key returning `(year, month, day, hhmm, slug)`.
  Reused by both `generate_recent_md` and the SessionStart status-line
  walker. Legacy `<DD>-<slug>.md` notes (no HHMM) collapse to
  `hhmm=0` so they sink to the *end* of their day in newest-first
  rankings — we don't pretend to know what time of day they were filed.

### Changed

- **`generate_recent_md` and `_last_session_hint` now use the
  layout-aware sort key** instead of naive reverse-string sort.
  Without this, legacy notes would have *preceded* timed peers within
  their day under reverse-lexicographic comparison (because `-` is
  less than digits).

### Compat

- Existing legacy `<DD>-<slug>.md` notes keep working — readers
  (lint, status-line, `_session_note_date`, `_find_todays_open_note`'s
  glob) all accept both shapes. No backfill required.

## [0.20.0] - 2026-04-28

### Added

- **Config-file fallback for `LORE_ROOT`** at `~/.config/lore/config.yml`
  (XDG-aware via `$XDG_CONFIG_HOME`). Resolution order is now env →
  config-file → `~/lore` default. Hosts that don't have a Claude
  Code-style `settings.json` env block to mutate (Cursor, Codex, Gemini)
  no longer need a per-shell `export LORE_ROOT=...`. The file format
  is YAML with a single top-level `lore_root: <path>` key — unknown
  keys warn but don't break, leaving room for future user-level
  preferences.

  ```yaml
  # ~/.config/lore/config.yml
  lore_root: ~/git/vault
  ```

  Resolves issue #6.

- **`resolve_lore_root() -> Path | None`** as a first-class resolver
  alongside the existing `get_lore_root()` (silent default) and
  `require_lore_root()` (strict). Returns `None` when neither env nor
  config-file provides a value — useful for code paths that need to
  distinguish "user genuinely never configured anything" from "user
  pointed somewhere that doesn't exist."

- **`lore doctor` reports the resolution source** — labels are now
  `[$LORE_ROOT]`, `[config-file]`, or `[unconfigured (fallback)]`.

### Changed

- **`secrets_path()` always returns a `Path`** (was `Path | None`).
  Falls back through `get_lore_root()` so the config-file fallback
  applies uniformly. `load_into_environ` no-ops when the file doesn't
  exist regardless. **Behavior change:** users who previously relied
  on `$LORE_ROOT` being unset to skip secrets-loading should now
  ensure the secrets file itself is absent or use a process-level
  env override.

- **Resolver unification.** Nine call sites that bypassed the canonical
  resolvers and read `os.environ.get("LORE_ROOT")` directly are now
  routed through `get_lore_root` / `require_lore_root` /
  `resolve_lore_root` so the new config-file fallback applies
  everywhere — `lore registry`, `lore ingest`, `lore curator`, scope
  resolver, secrets loader, and the SessionStart / capture hooks.

- **`hooks._infer_lore_root` precedence** is now env → walk-up →
  config-file → default. Walk-up beats config-file (but not env)
  because in a hook context the path argument is the explicit signal:
  a user with a global config pointing at `~/personal-vault` who is
  currently editing inside `~/work-vault/wiki/foo/` should resolve to
  `~/work-vault`. The function also accepts either a file (CLAUDE.md)
  or a directory (cwd) — fixes a latent caller-shape bug at
  `hooks.py:2845` where a directory was being passed and `.parent`
  skipped one level too high.

### Fixed

- **`surface_cmd` no longer defaults to `~/git/vault`** when
  `$LORE_ROOT` is unset. The original developer's path had been baked
  in as a default, silently routing surface commands to a path on
  someone else's machine.

### Deprecated

- `LoreRootNotSet` (exception class) — use `LoreRootNotConfigured`.
  The old name is kept as an alias for one release.

## [0.19.2] - 2026-04-28

### Fixed

- **Plan capture preserves the source structure.** Plans with `### P1`,
  `### P2`, … phase headings (Claude Code's compact phase form) now
  parse via heading mode instead of falling through to list mode. The
  recognizer was previously hard-coded to the long `### Phase 1` /
  `### Step 1` form, so phased plans with rich bodies (code blocks,
  migration tables, sub-headings) silently collapsed into "step lists"
  built from whatever scattered numbered lists the document happened
  to contain (Goals, Verification smoke tests, Risks). The saved vault
  note bore little resemblance to the plan as presented.

  Three coordinated fixes in `lore_core.plans.parser` and
  `lore_core.plans.writer`:

  - `### P<N>` joins the step-heading regex set.
  - List mode now requires either an explicit `## Steps` heading
    immediately preceding the list, or a single contiguous numbered
    list. Multiple disjoint numbered lists (separated by ATX headings)
    are ambiguous and fall through to single mode rather than being
    flattened into `s1..sN`.
  - Detection still runs on the fence-stripped text, but body slicing
    now uses the raw source — code blocks and tables in step bodies
    round-trip into the vault note instead of being blanked.
  - Single-mode plans render their body verbatim; the previous
    `## Steps / ### s1: s1` wrapper mis-nested the source's own H2
    sections under H3.

  Six new regression tests pin the behaviour, including a Ritchie-shaped
  end-to-end test that reproduces the failure mode.

## [0.18.5] - 2026-04-28

### Added

- **Mid-session plan-trailer nudges (Stop hook).** The Stop hook now
  scans active plans for `Plan: <slug>#sN` commit trailers landed in
  this session and emits ⚠ nudges when ``step_status`` doesn't reflect
  them yet. Closes the gap between commit-time and the next
  SessionStart — Claude sees the nudge in the systemMessage envelope
  immediately after finishing a turn and can advance via
  `/lore:plan-step <slug> <step> --done`.

  Per-session seen-set at
  ``~/.cache/lore/sessions/<sid>/plan-nudges.seen`` records every
  ``<sha>#<step_id>`` already nudged so the same trailer doesn't
  re-fire on every Stop until you advance. Robust against same-second
  commits (two trailers landed in the same second still get distinct
  nudges and dedup keys).

  Best-effort: any error inside the helper is swallowed so a
  malformed plan or git failure can't break the Stop hook.

## [0.18.4] - 2026-04-28

### Fixed

- **Plan step titles no longer truncate at hard wraps.** When a numbered
  list item's first sentence wrapped across multiple indented lines, only
  the first line landed in `step.title`; the tail orphaned into `step.body`
  and was lost from the rendered `### sN: <title>` heading. The list-mode
  parser (`_steps_from_list`) now reflows indented prose continuation
  lines into the title. A blank line or a sub-list marker (`-`, `*`,
  `+`, `N. `) ends the title block — sub-lists still go to body.
  Visible in `lore plan list` and MCP `lore_plan_status` step titles.

## [0.18.3] - 2026-04-28

### Fixed

- **`plan-capture` reads from `tool_response` when `tool_input` is
  empty.** Second smoke test after v0.18.1 surfaced that Claude Code's
  hook payload sometimes delivers the plan in `tool_response.plan`
  with `tool_input: {}` — specifically when the model calls
  ExitPlanMode without a `plan` argument and the harness loads the
  plan from the plan file. `parse_payload` now searches both sections
  in order: `tool_input.<field>` → `tool_input` longest-string fallback
  → `tool_response.<field>` → `tool_response` longest-string fallback.
  Telemetry source-field strings now name which section matched
  (`tool_response.plan`, etc.). The previously-orphaned payload from
  the 0.18.2 smoke test now files correctly.

## [0.18.2] - 2026-04-28

### Performance

- **SessionStart cold-start cut by ~50%+ (#27).** Two independent
  fixes:
  - `gh` issue + PR + subtree-sibling fetches in
    `_session_start_from_lore` were sequential — ~1.7s each, summing
    to ~3.7s of wall time. They now fan out via
    `_run_gh_parallel(calls)` (`hooks.py`), routed through the
    existing `_run_gh` monkeypatch surface so the `test_hooks_v2`
    contract is preserved. Wall time now tracks the slowest single
    call.
  - `lore_cli/__main__.py` eagerly imported ~30 sibling cmd modules
    to mount their typer subapps; every hook fire paid the full cost.
    Those imports + the `add_typer` calls + the `uninstall` alias
    now live inside `_build_app()`, cached as a module-global `_app`
    singleton served via PEP 562 `__getattr__`. `main()` short-
    circuits `lore hook <event>` straight to
    `lore_cli.hooks.hook_app`, skipping `_build_app()` entirely.
    ~240ms saved per hook fire.

  Combined: typical SessionStart drops from ~2.4-3.5s to ~0.9-2.0s.
  See issue #27 for the cProfile measurements and where the
  remaining gh-bound time goes.

## [0.18.1] - 2026-04-28

### Fixed

- **`plan-capture` no longer silently drops every plan.** The
  `PostToolUse:ExitPlanMode` handler gated capture on
  `tool_response.approved`, but Claude Code's actual ExitPlanMode
  payload has no `approved` field — the field was a defensive guess
  during initial implementation. Every accepted plan was logged as
  `outcome: "rejected"` and dropped, contradicting the handler's
  "Never silent loss" guarantee. The handler now treats hook firing
  as the authoritative approval signal (Claude Code never fires
  `PostToolUse` on rejection — rejection bounces back into plan
  mode without producing a `tool_result`). Added a regression test
  using the real captured payload shape (`tool_response: { plan,
  isAgent, filePath, hasTaskTool }`).

## [0.18.0] - 2026-04-28

### Changed

- **Session-note revision Phase 6 — Curator B section-aware input
  + decision prompt strengthening.** Curator B's
  `_load_recent_session_notes` now feeds clustering and the abstract
  prompt a section-aware extract (`## Summary` paragraph + first 300
  chars of `## Decisions made`) instead of `body[:800]`. The
  rationale-rich layer lands in B's prefix window first, regardless
  of whether the Summary paragraph runs 4 sentences or 6. Legacy
  notes (pre-revision, no Summary/Decisions sections) fall back to
  the original `body[:800]` slice so existing cluster fixtures keep
  working until the vault rolls forward.

  `surface_templates/standard.md` `decision` `extract_prompt` now
  anchors on rationale-presence (six-month rule) rather than section
  presence. The prompt explicitly notes that a `## Decisions made`
  bullet is a *candidate* — apply the rejection rules — not license
  to extract. This complements the per-section authoring norms that
  Curator A's prompt now injects (Phase 5), so trivial bullets that
  slip through the writer get caught at the surface-extraction
  step.

### Notes

- Existing wikis with their own `<wiki>/SURFACES.md` won't pick up
  the strengthened `decision` prompt automatically — only new wikis
  initialised after this release get it. A `lore surface` resync
  command is a separate follow-up.

## [0.17.0] - 2026-04-28

### Added

- **Session-note revision Phase 5 — per-wiki session templates.** New
  `lib/lore_core/session_templates/standard.md` documents the locked
  body shape and per-section authoring norms (Decisions promote-vs-don't
  rule, Loose ends past-tense / stative grammar, bold-substance-phrase
  bullet style, etc.). New `lib/lore_core/session_template.py` loads
  it with a per-wiki override at `<wiki>/templates/session.md`, parallel
  to how `surface_templates/standard.md` works. `classify_slice` and
  `_build_prompt_text` accept a `wiki_dir` kwarg and inject the active
  template's `## Section-authoring norms` block into the noteworthy
  LLM prompt — so when a wiki tunes its norms the LLM picks them up
  on the next call without code changes. Drift test asserts every
  heading the renderer emits is mentioned in `standard.md` so the
  documentation can't silently diverge from the code.

## [0.16.4] - 2026-04-28

### Fixed

- **`_last_session_hint` walks the sharded layout.** The previous
  `sessions/*.md` flat glob only matched `_recent.md` (a stale cached
  pointer) — SessionStart's "last note: …" line silently went empty
  against any real vault. Now `rglob`s the full `sessions/<YYYY>/<MM>/`
  subtree, filters to date-prefixed filenames via the new
  `_session_note_date` helper, and skips non-session frontmatter.
- **No more 1024-byte head-read cap.** Real notes with SHA-256 hashes
  in `source_transcripts` plus a long paragraph routinely sat past the
  cap, dropping the field. Cap removed; `parse_frontmatter` stops at
  the closing `---` regardless.
- **`_recent_open_items` walks the sharded layout** with the same
  `rglob` fix and the new date helper.

### Changed

- **Status-line preference: `title` → `description` → `summary`.**
  Revised notes' `title` (explicit slug source) wins for the status
  line; legacy notes fall through to `description` (legacy short
  headline) and finally `summary` (legacy paragraph).
- **`_OPEN_ITEMS_RE` recognises both shapes.** `## Open items` (legacy
  v1) and `## Loose ends` (current) match. `## Issues touched` (v2 gh
  reference section) is intentionally excluded — it's resolved-work,
  not "things discussed but not pursued".
- **SessionStart copy softened.** "Open items" → "Loose ends from
  recent sessions". Loose ends are informational, not a TODO list;
  surviving work belongs in the configured PM backend.
- **`threads.py` reads `title`/`description`/`summary` with the new
  preference.** Title prefers `title`, falls back to `description`.
  Summary prefers `summary`, falls back to `description` only when
  `title` is also present (signals revision shape).

### Added

- **`docs/audit-description-readers.md`** — inventory of every
  session-note `description` / `summary` / `title` reader in
  `lore_core/` and `lore_cli/`, with the per-site Phase 4 decision.
- **6 new sharded-layout / cross-shape tests** in
  `test_hooks_last_session_hint.py` covering: title preference,
  description fallback, summary fallback, sharded walking, full
  frontmatter reads, non-session note skipping.

## [0.16.3] - 2026-04-28

### Added

- **Session-note revision Phase 3 — mechanical Activity section +
  cross-note frontmatter.** New module
  `lib/lore_curator/session_activity.py` collects:
  - **Commits** in the chunk's work window via `git log --since/--until`.
  - **Issues opened / closed** by extracting `<verb> #<N>` patterns
    from turn text + commit subjects, then intersecting with `gh issue
    list --state open/closed` so only issues actually mentioned this
    session land in the section.
  - **Plans advanced** from `Plan: <slug>#sN` commit trailers and
    `[[plan/<slug>(#sN)?]]` body wikilinks, validated against
    `wiki/<wiki>/plans/`. Hallucinated slugs drop silently.
  - **Projects** from cwd repo + repo prefixes in `files_touched`,
    validated against `wiki/<wiki>/projects/`.
  Results render under the body's `## Activity` parent (with
  `### Commits` / `### Issues opened` / `### Issues closed`
  subheadings, omit-when-empty) and into the frontmatter `plans:` /
  `projects:` lists. All collectors are best-effort: missing git, gh,
  network — return empty lists, never block. 26 new tests cover the
  collectors, renderers, and integration end-to-end.

### Changed

- **Append-mode Activity union.** `merge_body_sections` now unions
  Activity sub-sections (commits / issues) across appends with
  exact-line dedup, so re-running the collectors on a new chunk
  doesn't double-list a commit seen earlier.

## [0.16.2] - 2026-04-28

### Changed

- **Session-note revision Phase 2 — body shape.** The body now opens
  with `# <title>` (no `Session:` prefix), then a rationale-first
  layout: `## Summary` paragraph → `## Decisions made` → `## What we
  worked on` → `## Activity` (parent for Phase 3's `### Commits` /
  `### Issues opened` / `### Issues closed`) → `## Loose ends`.
  Curator B's prefix window now lands on the durable rationale layer
  first. The legacy `### Summary` bullets, `### Files touched` (which
  duplicated frontmatter), and freeform `Entities:` line are gone.
  Append-mode merges new chunks into the existing sections — no more
  per-chunk `## <chunk title>` H2 wrappers; first chunk's title and
  Summary win, subsequent chunks contribute bullets only. Activity
  sub-sections stay empty in this phase; Phase 3 populates them from
  git/gh. Section parsing is permissive — old-shape content (e.g. a
  v2-old `### Summary` block on a same-day open note) fades out the
  next time the note is appended to. Part of
  [plans/ok-file-issues-and-harmonic-lagoon.md].

## [0.16.1] - 2026-04-28

### Changed

- **Session-note revision Phase 1 — frontmatter shape + slug.** New
  `title` field carries the content-named headline (slug source);
  `description` keeps a 1-2-sentence status-line preview; the old
  paragraph `summary` field is gone (its content moved into
  `description`); `draft: true` is dropped — sessions are immutable
  historical records and the flag never flipped. The slug logic now
  truncates at the last hyphen boundary instead of the naive
  `[:60]` cut, so filenames stop ending mid-word
  (`...rebase-onto-pha`). Old notes (with `summary` + `draft`) keep
  validating via permissive `OPTIONAL_FIELDS`. Curator A's noteworthy
  prompt explicitly steers titles toward content names (no "Phase 12"
  / "v0.13.0" headlines) and asks for past-tense `loose_ends` bullets
  that read as state-of-the-world, not as TODOs. Body rendering is
  unchanged in this phase — Phase 2 picks that up. Part of
  [plans/ok-file-issues-and-harmonic-lagoon.md].

### Added

- **`install.sh` bootstrap installer.** Replaces the deprecation shim
  that lived at the repo root since the v0.10 migration. Picks
  `pipx` / `uv tool` / `pip --user` (in that preference order),
  installs or upgrades the `lore` binary, then chains into
  `lore install` to wire up integrations. Curl-pipeable from
  `https://raw.githubusercontent.com/buchbend/lore/main/install.sh`
  for first-time installs; re-run for upgrades. Detects Claude
  plugin presence to skip the full integration plan on upgrade
  reruns and just refresh the manifest cache instead.
- **`lore install --upgrade` / `-u`.** Delegates to `install.sh
  upgrade` via subprocess so the binary self-replacement happens
  outside the running Python process. Single command, full roundtrip
  (binary + integrations + plugin cache).
- **`lore install` auto-runs `claude plugin update lore@lore`** after
  a successful install when the Claude integration was part of the
  run, so the plugin manifest cache no longer drifts behind the
  binary. Falls back to a copy-pasteable hint when `claude` isn't on
  PATH or the plugin isn't yet marketplace-installed.
- **`lore doctor` plugin-cache drift check.** Reads
  `~/.claude/plugins/installed_plugins.json` and compares the cached
  `lore@lore` version to the installed pip version; flags the inverse
  footgun (cache updated, binary not) with the exact fix command.

### Fixed

- **Templates ship as package data.** `templates/` moved into
  `lib/lore_core/templates/` and declared in
  `[tool.setuptools.package-data]`. Previously `lore install` (cursor
  integration) and SessionStart's directive injection assumed a
  repo-root layout via `Path(__file__).parent.parent.parent.parent` —
  fine for editable installs, FileNotFoundError under pipx/uv wheel
  installs.

## [0.16.0] — 2026-04-28

P3.2 from the multi-agent synthesis review — composite multi-stage
retrieval. Settles the trace-vs-composite design question with one
round-trip + structured stage breadcrumbs in the response envelope.

### Added

- **`lore_drill` MCP tool** — composite chain `search → read → expand
  → read_expanded` in one envelope. Returns
  ``{trace: [...], result: {notes: [...]}}`` with `elapsed_ms` per
  stage and `skipped` reasons (`search_returned_zero`,
  `no_wikilinks`) for short-circuited intermediates. Cap on the
  expand stage via `expand_limit` (default 5) keeps hub-note
  blowups bounded; truncation is recorded in the trace as
  `{truncated: N, kept: M}` only when the cap actually fires (not
  when the candidate set was simply smaller after unresolvable
  slugs were skipped). Failed reads are surfaced as `read_failed`
  arrays so divergence between `paths` and `result.notes` stays
  debuggable.
- **`lore drill <query>` CLI verb** — calls the same handler;
  renders the trace as a rich Tree and the result as a paginated
  note list. `--json` returns the raw envelope. Supports
  `--wiki`, `--k`, `--expand-limit`. Mounted under the Knowledge
  panel in `lore --help`.
- **Trace contract documented** in
  ``docs/architecture/lore-drill.md`` — clients should always
  check ``"skipped" in step`` before reading data keys.

## [0.15.0] — 2026-04-28

P3.3 from the multi-agent synthesis review — settles slash-toggle
vocabulary AND closes the long-standing UX honesty bug where
`/lore:on`, `/lore:off`, `/lore:loud`, `/lore:quiet` described
sentinel-backed mute behaviour that was never wired in code.

### Added

- **`lore on [scope]` and `lore off [scope]` CLI verbs.** Scope
  defaults to `all`. `all` mutes hooks, the capture pipeline, MCP
  retrieval, and inline citation affordances for the session.
  `citations` mutes only the inline `› consulted [[X]]` affordance.
  Sentinel files live under `$TMPDIR/lore-off-{scope}-{sid}` so the
  OS reaps them at session boundary. See
  ``docs/architecture/slash-toggles.md``.
- **`/lore:off citations` takes effect mid-session.** The citation
  suppression directive is now also injected on every
  `UserPromptSubmit` while the sentinel is set, so the agent stops
  emitting `› consulted` lines on the very next turn rather than
  waiting for the next SessionStart.
- **`docs/architecture/slash-toggles.md`** — full vocabulary +
  semantics + check-site contract.
- **`docs/architecture/lore-drill.md`** — settles P3.2 design
  (composite MCP call with structured trace + result envelope, plus
  server-side short-circuit on empty intermediate results).
  Implementation pending.
- **`lib/lore_core/toggles.py`** — `is_off(scope, sid)` /
  `set_off(scope, sid)` / `clear_off(scope, sid)` — single source of
  truth for "is this session muted?" queries.

### Changed

- **`/lore:off` is now security-honest:** every Lore touchpoint is
  gated. Hook entries (`cmd_session_start`, `cmd_pre_compact`,
  `cmd_stop`, `cmd_user_prompt_submit`), the capture pipeline
  (SessionEnd / SessionStart-capture / PreCompact-capture), and the
  MCP `_dispatch` all check the per-session sentinel and short-circuit
  cleanly when set. Previously the four toggle skills described this
  behaviour but the SKILL.md prose had no implementation behind it.
- **MCP refusal envelope** — every tool returns
  `{"error": {"code": "session_off", "message": ..., "next": ...}}`
  while `off all` is active. Code string follows the existing
  snake_case taxonomy.
- **Skill bodies** for `/lore:off`, `/lore:on`, `/lore:loud`,
  `/lore:quiet` now invoke the new CLI verbs rather than describing
  imaginary behaviour.

### Deprecated

- **`/lore:loud` and `/lore:quiet`** are now thin aliases for
  `/lore:on citations` and `/lore:off citations`. Kept for one minor
  release with a deprecation note in the SKILL.md `description`; will
  be removed in `0.16.0`.

## [0.14.0] — 2026-04-28

Plans-as-Lore-core: capture, store, and surface multi-step plans
directly in the wiki. Headline outcome is the **zero-handover demo**
— form a plan in Claude Code, accept ExitPlanMode, ``/clear``, restart
in the same repo, and the SessionStart banner brings Claude back
oriented on the right step with no manual ritual.

### ⚠️ Required upgrade step

Run ``/plugin update lore`` (or ``claude plugin update lore@lore``)
after upgrading the pip package. The new ``PostToolUse:ExitPlanMode``
hook only fires once Claude Code re-fetches the plugin manifest;
without the update, plan capture silently doesn't happen. ``lore
doctor`` warns when the installed plugin version diverges from the
pip version.

### Added

- **``type: plan`` surface** at ``wiki/<wiki>/plans/<slug>.md`` with
  stable step anchors (``s1..sN``) and a ``step_status`` frontmatter
  dict that is the single authoritative signal for "where are we" in
  a plan. Set semantics support out-of-order completion (Claude
  reorders steps for efficiency) and parallel agents (multiple
  ``in_progress`` is normal). Three values: ``done | in_progress |
  blocked``; pending is implicit (absence).
- **``PostToolUse:ExitPlanMode`` hook** (``lore hook plan-capture``)
  captures accepted plans automatically. Top-level except writes the
  raw payload to ``~/.cache/lore/orphan-plans/<ts>.json`` and emits a
  recovery hint — never silent loss.
- **``lore plan`` CLI**: ``list``, ``delete [--force]``, ``import
  [--from-orphan|--from-markdown]``, ``step <slug> <step_id>
  --done|--in-progress|--blocked|--pending``, ``advance <slug>``.
  ``advance`` is sugar — marks the in-progress step done if any,
  else the next pending step.
- **Per-slug ``flock``** in plan writer + step_status mutator;
  concurrent same-slug writes serialize without data loss.
- **Project-note auto-stub on attach** (Phase 3, not yet wired):
  ``stub_project_note`` composes a project note from CLAUDE.md /
  AGENTS.md / .cursorrules / README / pyproject metadata. Canonical
  headings (``## Overview``, ``## Conventions``, ``## Architecture``,
  ``## Key decisions``) regenerate on re-stub; user content under
  any other heading is preserved.
- **Breadcrumb scan** (Phase 4 surface): trailers of the form
  ``Plan: <slug>#s<N>`` in recent commits + session note wikilinks
  surface in the SessionStart Resume block as informational nudges
  ("commit abc123 references s4 — ``/lore:plan-step s4 --done``?").
  Never authoritative; the ``step_status`` field is law.

## [0.13.1] — 2026-04-28

Fix #29 — mid-stream session notes now surface in the active session.

### Fixed

- **Hooks read the JSON payload Claude Code passes on stdin** and
  republish ``session_id`` as ``CLAUDE_SESSION_ID`` for the duration of
  the hook process. ``cmd_session_start``, ``cmd_pre_compact``,
  ``cmd_user_prompt_submit``, ``cmd_capture``, and ``cmd_stop`` all go
  through the new ``_read_hook_payload`` helper. Until now lore never
  read the payload, so :func:`lore_core.drain.resolve_session_id` had
  to guess — and with multiple concurrent Claude sessions the
  transcript-freshness heuristic could pick a *different* session at
  curator-write time vs. heartbeat-read time, leaving curator-filed
  ``note-filed`` events in the wrong drain file. The published
  ``CLAUDE_SESSION_ID`` is automatically inherited by detached curator
  subprocesses (``_spawn_detached`` already does
  ``env = os.environ.copy()``), so writer and reader now agree on the
  same drain file.

### Added

- **``Stop`` hook is now declared in ``.claude-plugin/plugin.json``.**
  Previously only ``examples/settings.json`` listed it, so users on
  the plugin path never got the post-turn capture hint.

## [0.13.0] — 2026-04-27

Phase 12 — P3.1 + P3.4 from the multi-agent synthesis review:
**typer-app lift + CLI contract layering test**. The `lore_runtime`
package — a 301-LOC workaround that existed solely so lower layers
could host typer apps without inverting the dependency graph — is gone.

### Changed

- **Typer apps lifted into `lore_cli/<verb>_cmd.py`.** Previously
  `lore_core.lint`, `lore_core.migrate`, `lore_curator.defrag_curator`,
  `lore_mcp.server`, and `lore_search.cli` each ended with a typer
  block that the dispatcher mounted via cross-package import
  (`from lore_core import lint as lint_cmd`). Now every verb lives in
  its own `lore_cli/<verb>_cmd.py` file and the lower layers contain
  only business logic. The dispatcher in `lore_cli/__main__.py`
  imports siblings via a single `from lore_cli import (...)` block.

- **`lore_runtime` package deleted.** Its two helpers moved to where
  they're used: `argv_main` → `lore_cli/_argv_compat.py` (internal
  compat shim wrapping typer apps in the legacy `main(argv) -> int`
  contract) and `run_render` (pure-Python run-log renderers, no I/O)
  → `lore_core/run_render.py`.

- **`lore_mcp.server._start_server` renamed to `start_server`.** The
  function now crosses a package boundary (it's imported by
  `lore_cli/mcp_cmd.py`), so the leading-underscore "private to the
  module" convention no longer applies.

- **`lore_curator.__init__` and `lore_mcp.__init__`** dropped their
  `main` re-exports — those re-exports surfaced typer entry points as
  package public API, which they aren't. (Pre-1.0 cut-hard policy.)

### Added

- **`tests/test_cli_contract.py`** — CLI contract layering test
  (P3.4): asserts every `lore_cli/<verb>_cmd.py` defines a module-level
  `app: typer.Typer` and that `lore_cli/__main__.py` only mounts
  subapps via `add_typer()` (the documented `cmd_uninstall_alias` is
  the single grandfathered exception). Catches accidentally-hidden
  verbs and inline command drift in the dispatcher.

- **`docs/architecture/cli-contract.md`** — the seam doc the
  synthesis flagged as P5.2: shape of a verb file, the four rules
  (enforced by the layering tests), where helpers live, and how to
  break the rules cleanly.

### Tests

1554 → **1585 pass** (+31 from the new contract file). All five
lifted verbs render `--help` correctly under `lore <verb> --help`.

## [0.12.0] — 2026-04-27

Phase 11 — eight P2 structural cleanups from the multi-agent review.
No new features; all internal. The codebase is materially smaller and
more uniform; no behaviour change.

### Changed

- **`lore_core.git.git_user_email`** is now the single source of truth
  for resolving git's configured author email. Three sites that each
  reimplemented the same `subprocess git config user.email` shape
  (`lore_core/session.py`, `lore_curator/session_filer.py`,
  `lore_cli/hooks.py`) collapse to one helper with a documented
  resolution order (env override → git config → optional hostname
  fallback → empty).

- **`lore_core.lockfile.flocked`** is now the single source of truth
  for `fcntl.flock` context managers. Three sites that each spelled
  the same `os.open` + `fcntl.flock(LOCK_EX[|LOCK_NB])` + cleanup
  pattern (`lockfile.try_acquire_spawn_lock`,
  `hook_log.MaintenancePolicy._maybe_rotate`,
  `install/_helpers._flocked`) now go through the helper. Yields a
  ``bool`` so callers branch on acquired-or-not for non-blocking
  cases.

- **`session_curator.run_curator_a`** dry-run vs locked branches
  deduplicated via a closure (`_iterate_pending`). Pre-Phase-11 this
  was ~22 lines of nearly-identical code that drifted the moment a
  fix touched only one branch — exactly the failure mode Phase 6's
  `run_curator_c` decomposition targeted, missed in this function.

- **Curator C defrag passes register at call time, not import time.**
  Replaced the `_DEFRAG_PASSES` global + import-side-effect
  ``_register()`` calls in `c_*.py` modules with a single
  `_all_defrag_passes()` function that lazy-imports the three pass
  callables. Order is now deterministic; `importlib.reload` doesn't
  lose passes; debugging "where did this pass come from?" is one
  grep instead of a registry walk.

- **`hooks._gh_*` wrappers inlined** where they were one-line
  passthroughs to `lore_core.gh.*`. `_run_gh` stays — it's
  monkeypatched by `tests/test_hooks_v2.py` to feed deterministic
  JSON. `_split_filter`, `_format_issue_line`, `_format_pr_line`
  are gone; tests now call `gh.*` directly.

### Removed

- **Curator role-name aliases** (`run_session_curator`,
  `run_daily_curator`, `run_defrag_curator`). The 0.10.x rename never
  stuck — usage skewed 195:11 toward A/B/C across `lib/` and
  `tests/`, and the aliases existed only to soften a transition that
  didn't happen. Per-wiki memory and `feedback_curator_naming` agree:
  user-facing copy says "Curator"; A/B/C live in code only. Public
  exports from `lore_curator/__init__.py` now name the canonical
  A/B/C entry points.

- **Legacy `pending-breadcrumb.txt` migration call** removed from the
  hot SessionStart path (`lore_cli/breadcrumb.consume_pending_breadcrumb`).
  The migration helper itself stays for hand-rolled use on long-dormant
  vaults; the unconditional call was 0.9.0-era, ran on every
  SessionStart, and was paying ~10μs of idempotent FS overhead on
  every Claude session start.

### Internal

- Pinned removal target on the two ``# legacy — retained during
  deprecation`` comments in `lore_core/lint.py` (NoteInfo.status,
  serialiser): scheduled for 1.0 removal once vaults converge to
  the `lifecycle` field.

### Tests

- Test count unchanged (1555/1555). All P2 work was structurally
  invariant — same behaviour, smaller surface.

## [0.11.1] — 2026-04-27

Phase 10 follow-ups: surface diverged wikis in `lore status`; clean up
the cosmetic "host" debt in test files that the Phase 9c rename pass
left behind.

### Added

- **`lore status` divergence indicator.** Walks attached wikis and
  reports any whose local branch has commits the remote doesn't AND
  vice versa. `auto_pull` (Phase 10) skips diverged trees silently —
  `lore status` is now the canonical "you have unfinished sync work"
  surface. New public helper `lore_core.git_sync.is_diverged(wiki_dir)`
  — cheap local check, never fetches.

### Internal

- Test-file cosmetic cleanup: `def lookup(host: str)` → `(integration: str)`
  in `test_curator_a.py`, `test_auto_diagnostics_e2e.py`,
  `test_adapter_protocol.py`. Stub class `_MissingHostAttr` →
  `_MissingIntegrationAttr`. Bodies were already correct from Phase 9c;
  these were the names/strings that Phase 9c's mechanical sweep
  intentionally left for a follow-up. Caught by the Phase 9c review.

- Reviewer-flagged Phase 10 corrections (already shipped in 0.11.0;
  noted here for completeness):
  - `git_sync.auto_push` step 3 now calls `git merge --abort` on
    commit-failure, mirroring step 4.
  - LLM-merge result validation requires `parse_frontmatter` to return
    a dict containing a `type` key — malformed responses can no longer
    clobber a real surface.
  - Push-after-merge failure messages now say "merge committed locally,
    push failed" so the user knows the next auto_push retries via the
    fast path.

### Tests

- 4 new `is_diverged` tests in `test_git_sync.py`. Total: 1555/1555.

## [0.11.0] — 2026-04-27

**Cross-host sync.** This is the release that turns Lore from "a
beautifully-architected single-host tool that *describes* a multi-host
product" (verbatim from the multi-agent review) into the cross-host
AI ↔ human knowledge vault its README pitches. Three orthogonal
mechanisms ship together:

### Added — auto-pull at SessionStart

`lore_core.git_sync.auto_pull(wiki_dir)` does a fetch + fast-forward on
the scope's wiki repo when SessionStart fires. Strictly read-only on
dirty or diverged trees; never disturbs in-flight user work. The
`lore_cli/hooks.py:cmd_session_start` handler invokes it once for the
attached scope; warnings (`· wiki [[name]] diverged from origin —
``git pull`` manually`) render into the banner footer. Per-wiki opt-in
via `.lore-wiki.yml`'s `git.auto_pull: true` (default true; was a dead
dataclass field through 0.10.x).

### Added — auto-push with LLM-merge surface conflict resolution

`lore_core.git_sync.auto_push(wiki_dir, *, llm_client=None)` pushes
local commits, classifies conflicts, and resolves surface conflicts
inline via an LLM call. **No temp `.host-A.md` artefacts. No wait for
Curator C to defrag.** When two hosts independently produce
`concepts/foo.md`, the second host's push triggers the merge, runs
the LLM with both versions + the merge-base, writes one canonical
file, commits "merge(auto-llm): N surface(s)", and pushes.

Conflict classification:
- **Surface** (`concepts/`, `decisions/`, `results/`, …): LLM-merge
- **Session note**: LLM-merge (rare in steady state — pre-pull eliminates)
- **Regenerable** (`_catalog.json`, `_index.txt`, `threads.md`):
  take ours; lint reconciles
- **Unknown** (e.g. hand-edited `CLAUDE.md`): `git merge --abort`,
  surface to user

`lore_curator.session_curator._maybe_auto_commit` and
`lore_curator.daily_curator._maybe_auto_push` invoke `auto_push` when
the wiki opts in via `.lore-wiki.yml`'s `git.auto_push: true` (default
false — flip per-wiki when ready). Both curators already have an
`llm_client`; they pass it through so merges happen at curator cost,
not at user-prompt cost.

### Added — fs-watch reindex invalidation

`lore_mcp.reindex_watcher.start_watcher` boots a `watchdog`-backed
daemon thread inside the MCP server (one per Claude session). On
`*.md` create/modify/delete under `<lore_root>/wiki/`, it marks the
affected wiki dirty. The next `lore_search` reindexes that wiki
*regardless* of the 5-second throttle — so post-pull edits surface
immediately, not after the throttle's natural decay.

`watchdog>=4` is in the `[search]` extras (was already declared, just
unused). When the dep isn't installed, behaviour falls back to
throttle-only invalidation — same as 0.10.x. Kernel-level monitoring
(inotify on Linux, FSEvents on macOS) makes this near-zero-overhead.

### Added — sink registry

`lore_core.briefing.sinks` is now a real registry: schemes register
themselves at import (`markdown`, `matrix`), and
`dispatch(uri, text)` looks up the scheme prefix and calls the
registered sender. Adding Slack / Discord / GitHub Discussion is one
new module and one `register("slack", _send)` call — no edits to
`daily_curator.py` or `briefing_cmd.py`.

`lib/lore_curator/daily_curator.py:435`'s hardcoded
`if sink.startswith("markdown:")` chain is gone; `briefing_cmd.py`'s
`_KNOWN_SINKS` static set is gone. Both go through `dispatch`.

### Changed (breaking — pre-1.0)

- **`lib/lore_sinks/` deleted.** Contents moved to
  `lib/lore_core/briefing/sinks/` per the simplifier review's CC1
  finding. Anyone running `python -m lore_sinks.markdown` directly
  needs to switch to `lore briefing publish --sink markdown:<path>`.
- **`lib/lore_core/briefing.py` is now a package** (`lib/lore_core/briefing/`).
  `gather()` and `mark_incorporated()` re-exported from
  `lore_core.briefing` — public import path unchanged.
- `_strip_frontmatter` / `_split_frontmatter` test no longer required:
  the existing helpers are now `from lore_core.schema import …`
  re-exports; they continue to work but the canonical path is direct
  import of `split_frontmatter` / `strip_frontmatter` from `schema`.

### Architecture

`docs/architecture/sync.md` (new) documents the conflict policy, the
fs-watch lifecycle, and the **MCP-daemon-as-fast-path principle**:

> MCP-daemon work is the fast-path; hooks are the correctness fallback.
> If the MCP server crashes, hook-driven work still fires on Claude
> lifecycle events and produces the same end state.

The fs-watcher is the first daemon thread inside the MCP server; the
ADR documents the principle so future work (e.g. detaching Curator A
trigger from hooks) inherits the same shape.

### Tests

- 21 new tests for `git_sync` against bare-repo+two-clone fixtures
  (auto_pull, auto_push happy path, LLM-merge stub, regenerable
  ours-wins, unknown bail, classifier parametrisation)
- 9 new tests for `reindex_watcher` (state primitives, watchdog
  integration, optional-dep fallback)
- 2 new tests for the dirty-flag bypass of the throttle
- 1 reworked test in `test_curator_b_briefing_integration` (renamed
  from "unsupported sink" to "unknown sink" — matrix is now
  registered, so the test now uses an unregistered scheme to exercise
  the same skip-and-log path)
- Total: 1551/1551 passing (was 1520 in 0.10.6)

## [0.10.6] — 2026-04-26

Phase 9d — frontmatter splitter consolidation. Six sites collapsed to
one canonical implementation in `lore_core/schema.py`.

### Changed

- **`lore_core.schema` adds `split_frontmatter()` and `strip_frontmatter()`**
  as the canonical YAML-frontmatter splitter. `parse_frontmatter()` now
  delegates to `split_frontmatter()` rather than repeating the
  start-fence/closing-fence logic inline.

- **Six call sites consolidated** — `lore_core/migrate.py`,
  `lore_curator/defrag_curator.py`, `lore_curator/daily_curator.py`,
  `lore_core/session_writer.py`, `lore_search/fts.py`, and
  `lore_mcp/server.py` (which had its own inline `txt.startswith("---\\n")`
  parser) now all share the canonical implementation. The local helpers
  (`_split_frontmatter`, `_strip_frontmatter`) remain as one-line
  re-export aliases so call sites don't need rewriting.

- **`lore_curator/abstract._strip_leading_frontmatter`** — kept as a
  specialised variant (it `lstrip("\\n")`s first and requires the
  candidate to yaml-parse-to-dict, both LLM-output safety nets the
  canonical doesn't need) but rebuilt on top of the shared splitter
  rather than reimplementing the fence search.

### Why

Five different bespoke implementations of "find `---` … `\\n---`" was
a low-stakes invitation for divergence. The simplifier review flagged
this; consolidation costs nothing and makes the next bug-fix obvious.

## [0.10.5] — 2026-04-26

Phase 9b — P1 quick wins from the multi-agent review synthesis. No new
features; cleanup the Phases 0–8 sweep missed plus README polish.

### Fixed

- **Telemetry leaked vendor naming** — `skip_reason="no_anthropic_client"`
  (and the matching log event `reason="no-anthropic-client"`) survived
  Phase 0's `anthropic_client → llm_client` rename in two places
  (`lib/lore_curator/session_curator.py:385`, `lib/lore_curator/daily_curator.py:83`).
  Renamed to `no_llm_client` / `no-llm-client`. The whole point of
  Phase 0 was to stop spelling "anthropic" in user-facing telemetry.

- **Legacy SessionStart cache writer was perpetually populated.** The
  pre-PID-keying cache file `last-session-start.md` was documented as
  "deprecated, read-only fallback" since 0.9.0, but `hooks.py:996-999`
  kept overwriting it on every SessionStart. Its mtime never aged out,
  so the deprecation could never fire. Removed the writer; the read-
  fallback in `_context_log` stays for one release and drops in 0.11.0.

### Removed

- **`lore_search/backend.py`** (-57 LOC). The `LoreBackend` Protocol had
  one implementer (`FtsBackend`) and zero typed consumers — every
  caller imported `FtsBackend` directly. Moved `SearchHit` next to
  `FtsBackend` in `fts.py`. When a second backend lands (Qdrant, Chroma,
  …), reintroduce the Protocol *with* the second implementer in the same
  patch, not before.

### Internal

- **`ruff --select F401` sweep** removed 35 unused imports across `lib/`
  (`lore_adapters/cursor_agent.py`, `lore_curator/session_curator.py`,
  `lore_curator/daily_curator.py`, etc.). No behaviour change.

### Documentation

- **README install path unified.** The canonical install used to live in
  `## Install`, but `## Bootstrap > Fresh install` re-instructed with a
  different ordering and `[capture]` extras, and `## As a Claude Code
  plugin (via marketplace)` added a third path. New: `## Install` is
  the single canonical block (with `[capture]` extras as default);
  `Fresh install` was removed in favour of a back-link; `Update from
  an older install` now mirrors the canonical command.

- **README Observability now leads with `lore status`.** The previous
  three-row table mentioned `lore runs show latest` but not the
  exemplary 7-line activity-first dashboard `lore status` — promoted
  to the first row with the framing "Is Lore doing anything for me
  right now?" plus a one-paragraph explainer.

- **CONTRIBUTING broken link removed.** Internal artifact (a
  `~/.claude/plans/give-these-considerations-to-melodic-castle.md`
  reference) leaked into the public file in a prior commit. Replaced
  with a generic "PR description / docs/REVIEW-*.md" pointer.

## [0.10.4] — 2026-04-26

Phase 9c — second half of the host → integration rename. 0.10.3 covered
the install/CLI surface; this release renames the **runtime data model**
(adapters, ledger, frontmatter) so the codebase is fully consistent.

### Changed (breaking — pre-1.0; one-release back-compat for ledger reads)

- **Adapter Protocol** (`lore_adapters.Adapter`): `host: str` →
  `integration: str`. All four built-in adapters (claude-code, cursor,
  manual-send, vscode-copilot) renamed accordingly.
- **Registry**: `UnknownHostError` → `UnknownIntegrationError`,
  `registered_hosts()` → `registered_integrations()`. `get_adapter()`
  parameter `host` → `integration`.
- **Turn dataclass** (`lore_core.types.Turn`): `host_extras` →
  `integration_extras`. In-memory only; no persistence impact.
  Adapter-specific keys inside (`cursor.raw_content`, `claude_code.unknown_block`,
  etc.) keep their integration-name namespacing.
- **TranscriptHandle**: `host: str` → `integration: str`.
- **TranscriptLedgerEntry**: `host: str` → `integration: str`. JSON
  field key in `transcript-ledger.json` renamed `"host"` → `"integration"`.
  **Reads accept either key for one release** (drop in 0.11.0); writes
  always emit `"integration"`. Existing ledgers continue to work.
- **session-note frontmatter**: `source_transcripts[].host` →
  `source_transcripts[].integration`. Existing notes continue to read
  via parse, but new notes are written with the new key. No reader inside
  Lore programmatically depends on the field; it's display + drill-down.
- **`classify_tool_name`** (`lore_core.tool_categories`): first parameter
  renamed `host` → `integration`.
- **Capture hook**: `lore hook capture --host <name>` →
  `--integration <name>`. Hook event JSON envelope renames the same key.
- **`lore ingest`**: `--host <name>` → `--integration <name>`. Internal
  `manual_send.declared_host` extras key → `manual_send.declared_integration`.
- **Adapter call signature**: `read_from(declared_host=...)` →
  `read_from(declared_integration=...)`.

### Why a hard cut

Pre-1.0 cut-hard policy. The single carve-out is the ledger JSON read
fallback — keeps existing user vaults' transcript-watermark history
intact across the upgrade so the curator doesn't re-derive everything
from scratch.

## [0.10.3] — 2026-04-26

Vocabulary cleanup: **"host" now means *machine*, "integration" means
*AI tool*** (Claude Code, Cursor, OpenCode). Two reviewers (architect +
UX) flagged the overload — `attachments.json` calls itself "host-local"
to mean "this machine," while `lore_cli/hosts.d/*.toml` used "host" for
"AI integration target." The same word for two unrelated concepts was
making cross-host sync discussions ambiguous.

### Changed (breaking — pre-1.0; no compat alias)

- **Directory rename**: `lib/lore_cli/hosts.d/` → `lib/lore_cli/integrations.d/`
- **Directory rename**: `templates/host-rules/` → `templates/integration-rules/`
- **Env var**: `LORE_HOSTS_DIR` → `LORE_INTEGRATIONS_DIR`
- **CLI flag**: `lore install --host <name>` → `lore install --integration <name>`
  (also affects `check`, `upgrade`, `uninstall`, `reinstall` subcommands and
  `lore uninstall`).
- **JSON envelope**: `lore install --json` now emits `"integrations": [{"integration": ...}]`
  in place of `"hosts": [{"host": ...}]`.
- **Public API** (`lore_core.install`): `known_hosts()` → `known_integrations()`,
  `get_host()` → `get_integration()`. `Action.on_failure` constant
  renamed `ON_FAILURE_ABORT_HOST` → `ON_FAILURE_ABORT_INTEGRATION`
  (string value also: `"abort_host"` → `"abort_integration"`).
- **Launcher API** (`lore_cli.launcher`): `HostConfig` → `IntegrationConfig`,
  `list_hosts` → `list_integrations`, `load_host` → `load_integration`.
  `lore resume --launch HOST` → `--launch INTEGRATION` (metavar only;
  flag name unchanged).
- **TOML schema**: `hosts.d/*.toml`'s `lore.host/1` schema label →
  `lore.integration/1`. (Comment-only — nothing parses the label.)
- Internal data-model field renames (`Adapter.host`, `Turn.host_extras`,
  `LedgerEntry.host`, etc.) ship in **0.10.4** — the second half of the
  rename.

### Why a hard cut

Pre-1.0 policy is "nothing is sacred yet." The compat-alias path for
`LORE_HOSTS_DIR` and `--host` would carry deprecation comments without
removal targets — the simplifier review specifically flagged that
pattern as anti-discipline.

## [0.10.2] — 2026-04-26

### Fixed

- **`threads.md` headline counts full paths, not basenames.** Real-world
  bug: a thread of 6 notes about curator development was labelled
  `SKILL.md` because one note touched 4 different `SKILL.md` paths
  (`skills/quiet/SKILL.md`, `skills/off/SKILL.md`, …) and the
  label-counter aggregated by basename — so one note's 4 different
  paths beat `curator_b.py`'s 3 cross-note votes. Union-find was
  already correct (it used full paths to *group* notes); only the
  display-label aggregation was wrong. Now each `(note, path)` pair
  contributes one vote, and the chosen full path's basename is used
  purely for display. Two new tests lock the behaviour in.

## [0.10.1] — 2026-04-26

### Fixed

- **`lore install` and `lore doctor` now detect Python-package version
  drift** (issue #28). The Claude Code plugin and the Python CLI binary
  update via separate channels (`claude plugin update` vs `pipx
  install`); when they drift, SessionStart's status line silently
  shows the binary's old version even after a successful plugin
  update. Both commands now compare `importlib.metadata.version("lore")`
  against the on-disk `pyproject.toml` and surface a copy-pasteable
  fix command (`pipx install --force --editable <repo>`) when they
  disagree. Advisory check (does not block installs) — the CLI still
  functions at the older version, just visibly.

## [0.10.0] — 2026-04-26

Cleanup-roadmap closeout (Phases 0-8). User-visible CLI/slash changes
warrant a minor bump; no breaking changes — every legacy form keeps
working via aliases.

### Added

- **`lore wiki new <name>`** — canonical home for wiki-lifecycle
  verbs going forward; matches `lore surface init/add/commit`. The
  legacy `lore new-wiki <name>` keeps working as an alias and prints
  a one-line stderr hint pointing at the new form.
- **Role-named curator modules**:
  - `lore_curator/session_curator.py` (was `curator_a.py`) — files
    session notes from completed transcripts.
  - `lore_curator/daily_curator.py` (was `curator_b.py`) — extracts
    surfaces and regenerates `threads.md`.
  - `lore_curator/defrag_curator.py` (was `curator_c.py`) — weekly
    defrag/stale-flag/supersession.
  Function aliases `run_session_curator` / `run_daily_curator` /
  `run_defrag_curator` are added alongside the legacy `run_curator_a/b/c`
  names; all old import sites continue to work.

### Changed

- **Slash command renamed**: `/lore:surface-new` → `/lore:surface-add`
  for symmetry with `lore surface add`. The skill directory was
  renamed via `git mv` (history preserved); autocomplete now shows
  the new name.
- **Skill cleanup**: `skills/lint/SKILL.md` and `skills/curator/SKILL.md`
  now call the `lore` CLI directly (`lore lint`, `lore curator`,
  `lore migrate`) instead of leaking internal package paths
  (`python -m lore_core.lint` etc.).
- **SKILL.md description sharpening**: `/lore:lint` and `/lore:curator`
  descriptions revised to make their distinct roles obvious to Claude
  (mechanical-vs-judgment) so picker reliability improves.

### Notes

- `tests/test_skill_cli_drift.py` is the static guard against future
  `python -m lore_*` regression in skills.
- `tests/test_cli_wiki.py` pins both the canonical `lore wiki new`
  path and the legacy `lore new-wiki` alias.
- Curator module renames are mechanical: ~25 import sites migrated;
  function aliases mean ~188 callers of `run_curator_a/b/c` keep
  working unchanged.

## [0.9.0] — 2026-04-25

Surface-extraction quality push (full notes in
`docs/superpowers/HANDOVER-2026-04-19.md` and the v0.9.0 commit). This
entry also closes a long version-sync drift: `.claude-plugin/plugin.json`
was stuck at 0.5.0 while `pyproject.toml` advanced to 0.9.0, meaning
`claude plugin update lore@lore` silently reused cached code. A pytest
guard (`tests/test_version_sync.py`) now fails CI if the two sources
disagree.

> **Note on the gap.** Versions 0.4.0 through 0.8.2 shipped without
> changelog entries; their notes live in commit messages
> (`git log --grep="Lore v0\."`). They will be backfilled in a future
> docs pass.

## [0.3.0] — 2026-04-22

Local-Lore-state release. Replaces the distributed `## Lore` CLAUDE.md
routing model with a host-local registry + optional `.lore.yml` offer.
CLAUDE.md is no longer a routing artifact. See issue #22 and
`docs/superpowers/plans/2026-04-22-local-lore-state-plan.md`.

### Added

- **Host-local state** — `$LORE_ROOT/.lore/attachments.json` (which
  paths route where) and `$LORE_ROOT/.lore/scopes.json` (the scope
  tree, flat ID-as-path with wiki-inheritance).
- **`.lore.yml` offer format** — optional checked-in repo file
  declaring `wiki`, `scope`, `backend`, `issues`, `prs`, and
  `wiki_source`. Fingerprinted over routing fields only so non-routing
  tweaks don't re-prompt users who accepted.
- **Consent state machine** — `UNTRACKED | OFFERED | ATTACHED |
  DORMANT | MANUAL | DRIFT` surfaced by a non-blocking notice in
  SessionStart when `.lore.yml` is pending acceptance.
- **Registry CLI** — `lore attach {accept, decline, manual, offer}`,
  `lore attachments {ls, show, rm, purge-unattached}`,
  `lore scopes {ls, show, rename, reparent, rm}`. Scope rename/reparent
  propagates across both state files atomically.
- **Doctor extensions** — `lore doctor` validates attachments (path
  exists, wiki dir exists, scope in tree, fingerprint matches) and
  scope-tree integrity; surfaces `__orphan__` / `__unattached__` ledger
  buckets with actionable suggestions.
- **Migration tool** — `lore migrate attachments` (one-shot,
  idempotent, dry-run) converts legacy `## Lore` CLAUDE.md blocks into
  `.lore.yml` + registry rows and strips the section from CLAUDE.md
  (surrounding content preserved).
- **Reinstall shortcut** — `lore install reinstall` composes
  `uninstall` + `install` in one step.

### Changed

- `resolve_scope(cwd)` is registry-only — longest-prefix match on
  `attachments.json`. No filesystem walk-up. O(log n) lookup.
- Ledger's `pending()` / `pending_by_wiki()` default resolver is
  bound to the ledger's own `lore_root`, not `$LORE_ROOT` env —
  simplifies test fixtures.
- `_walk_up_lore_config` is now a registry-backed shim that returns
  a synthetic `claude_md_path` sentinel plus a block dict derived from
  the resolved scope (merged with any `.lore.yml` at the attachment
  path for non-routing fields).

### Removed

- Legacy `## Lore` CLAUDE.md walk-up resolver (`_legacy_walk_up_resolve`).
- Lazy-migration hook in the legacy resolver (transition-only,
  superseded by explicit `lore migrate attachments`).
- `TranscriptLedger._resolve_wiki_cached` + cache dict (redundant now
  that longest-prefix match is O(log n)).
- Legacy `lore attach read` / `lore attach write` commands (replaced
  by `lore attach accept|manual|offer` + `lore attachments show`).

### Migration

On machines with existing `## Lore` blocks in CLAUDE.md:

```
lore migrate attachments --dry-run   # preview
lore migrate attachments --yes       # apply
```

Idempotent. Re-runs are no-op. Preserves surrounding CLAUDE.md content.

## [0.2.4] — 2026-04-21

### Fixed

- **Capture hooks were never registered with Claude Code after v0.2.3**.
  `.claude-plugin/plugin.json` gained `SessionEnd` + `lore hook capture`
  wiring for `SessionStart`/`PreCompact` in commit `004d033`, but the
  package version wasn't bumped. `claude plugin update lore@lore` had
  nothing to re-fetch, so installed plugin caches stayed on the
  pre-capture manifest — the banner hook fired but the capture hook
  never did, and no transcripts were ever ledgered unless curator was
  run by hand. Bump forces a re-fetch.
- `lore_search` FTS5 index auto-migrates from the legacy contentless
  schema (which couldn't DELETE). `/lore:resume <keyword>` and
  `lore_search` MCP calls no longer raise
  `cannot DELETE from contentless fts5 table: notes_fts`.

### Added

- `lore hook capture` now emits a `hook-events.jsonl` record with
  `outcome="no-scope"` when the cwd isn't inside a configured wiki
  instead of silently returning. Makes "hook never fired" vs "hook
  fired but declined" distinguishable in `lore status` and
  `lore runs list --hooks`.
- `lore status` gains a `Hook` line between `Last run` and `Pending`
  (`· Hook  12m ago · session-start · spawned-curator`) plus a
  loud-on-earning alert when pending > 0 AND no hook events in 24h.
- `lore runs list --hooks` prints a diagnostic banner when runs
  exist but `hook-events.jsonl` is empty/missing.

## [0.2.3] — 2026-04-18

### Fixed

- **Slash commands lost their `lore:` namespace prefix in v0.2.0**.
  When skill directories were renamed from `skills/lore:<name>/`
  to `skills/<name>/` and the SKILL.md frontmatter `name:` was
  set to the bare value, Claude Code's picker started showing
  bare slash commands (`/init`, `/resume`, `/inbox`, …) — colliding
  with built-ins like Claude Code's own `/init`.
- **Restored explicit scoping in SKILL.md frontmatter**:
  `name: lore:<bare>` (literal colon). Other plugins like
  `frontend-design:frontend-design` use the same pattern; Claude
  Code uses the frontmatter `name` field verbatim as the slash
  command name. Directory names stay bare; only the in-frontmatter
  name carries the prefix.

  After `claude plugin update lore@lore`, slash commands appear in
  the picker as `/lore:resume`, `/lore:loaded`, `/lore:init`, etc.
  No collision with built-ins; explicit namespace always visible.

## [0.2.2] — 2026-04-18

### Fixed

- **`claude plugin update lore@lore` failed** with "destination is
  empty after copy" because the v0.2.0 marketplace.json used a
  `github` source object pointing back at `buchbend/lore` — the
  same repo as the marketplace itself. Claude Code's update path
  for github-source plugins (clone source repo → copy into
  versioned cache) appears to mishandle this self-reference and
  produces an empty cache.
- **Switched to `source: "./"`** (validated cleanly with
  `claude plugin validate`). The marketplace root IS the plugin
  root in our setup; Claude Code uses the marketplace clone
  directly, no separate github source clone, no copy-step bug.

## [0.2.1] — 2026-04-18

### Fixed

- **Top-level `lore --help` was still argparse-style** (bare list of
  subcommand names). The 0.2.0 typer migration covered the leaves
  but `__main__.py` still used the legacy SUBCOMMANDS lookup. Now a
  proper `typer.Typer()` root mounts every subcommand via
  `add_typer`, so `lore --help` shows the Rich-boxed command tree
  with descriptions.
- **`lore mcp --help` hung waiting for stdin** because the MCP
  server's `main()` was bare argparse-free and ignored `--help`.
  Wrapped in a typer app with a no-arg callback so help works
  without starting the STDIO loop.
- **`lore migrate`** was still on argparse; migrated to typer.
- **`lore uninstall`** alias preserved as a top-level typer command
  forwarding to `install_cmd._cmd_install` with `mode="uninstall"`
  (same flags as `lore install uninstall`).

## [0.2.0] — 2026-04-18

### Added

- **`lore install` / `lore uninstall`** — multi-host installer
  dispatcher (Claude Code + Cursor in v1) with print-and-confirm UX,
  per-host plans, schema versioning per host module, semantic-undo
  contract, `--force --yes` refusal, legacy-artifact detection.
  Replaces the 340-line `install.sh`.
- **`lore doctor`** — smoke-test subcommand: LORE_ROOT, wikis, cache,
  MCP server, FTS backend, SessionStart hook, attach block.
- **`lore_core.resume.gather()`** — unified resume entry point covering
  no-arg / wiki / keyword / scope modes. `/lore:resume` skill now does
  one MCP call instead of ~6 iterative Glob/Read/Grep.
- **`lore_core.session.scaffold()`** + `lore session new` /
  `lore session commit` — split `/lore:session` into MCP scaffold-read
  + visible CLI write/commit. Subagent goes from 6–8 tool calls to ~3.
- **`lore_core.briefing.gather()`** + `lore briefing
  {gather,publish,mark}` — split `/lore:briefing` into deterministic
  MCP gather + LLM prose composition + visible CLI publish.
- **`lore_core.inbox.classify()`** + `lore inbox
  {classify,archive}` — same shape for `/lore:inbox`.
- **`lore resume <topic> --launch <host>`** — standalone launcher
  pre-warms a fresh agent session with a gathered context block.
  TOML host registry at `lib/lore_cli/hosts.d/*.toml` for cross-host
  dispatch (Claude + Cursor in v1).
- **3 new MCP tools**: `lore_session_scaffold`, `lore_briefing_gather`,
  `lore_inbox_classify` (now 9 total).
- **`/lore:loaded`** — renamed from `/lore:why`; matches the
  SessionStart status line text. Cache stores full text (truncation
  only on inject).
- **Vault-first directive** in SessionStart `additionalContext` and
  re-asserted in PreCompact (`templates/host-rules/default.md` is
  the single source of truth, used by hooks + Cursor rules file).
- **`tools/undo_install_sh.py`** — stdlib-only Python helper to
  cleanly reverse the legacy `install.sh` mutations.
- **CONTRIBUTING.md** — dev-mode install recipe, "filing a host
  module" guide, version-bump convention.

### Changed

- **CLI migrated from argparse to typer + Rich** across all 12
  subcommands. Pretty `--help` boxes, type-coerced options, future
  shell-completion. `lib/lore_cli/_compat.py:argv_main()` keeps the
  legacy `main(argv) -> int` contract for tests + the SUBCOMMANDS
  dispatcher.
- **Skill directory names dropped the `lore:` prefix** — Claude Code's
  plugin namespace supplies the prefix. `skills/lore:loaded/` →
  `skills/loaded/`. Slash commands stay `/lore:loaded` etc.
- **`--json` envelopes** standardised across `lore.<verb>/N` schemas
  for attach, detach, search, lint, curator, resume, session,
  briefing, inbox, doctor, install.
- **`install.sh`** shrunk from 340 lines to a ~50-line deprecation
  shim pointing users at `lore install`.
- **`lore-thesis.md`** reframed from "plugin with CLI underneath" to
  "CLI-first that ships plugins per host." Token-economy principle is
  now the architectural backbone (gather → CLI/MCP, synthesis at
  write/maintenance time, no synthesis at retrieval).
- **`.claude-plugin/marketplace.json`** schema fixed
  (`source: {"source": "github", "repo": "buchbend/lore"}`,
  `metadata.description` instead of root-level `description`).
- **`.claude-plugin/plugin.json`** declares `hooks` + `mcpServers`
  inline (Claude Code's plugin system wires them; install no longer
  mutates `~/.claude/settings.json`).

### Fixed

- **PyPI name `lore` is squatted** by an unrelated package
  (`lore 0.8.6`, broken on Python 3.13). Install path uses
  `pipx install git+https://github.com/buchbend/lore.git` until a
  clean PyPI name is picked (issue #9).
- **Marketplace registration step missing** in `lore install --host
  claude` — added `claude plugin marketplace add buchbend/lore`
  with `on_failure=continue` so re-runs don't wedge.
- **Install error messages printed literal `[red]...[/red]`** markup
  tags — `markup=False` (added for ANSI safety) suppressed the
  wrapper colour. Now uses `rich.markup.escape()` on user-derived
  content with `markup=True` on the wrapper.
- **`lore install --json` mixed Rich legacy warning with JSON
  envelope** — warning now suppressed when `--json` is set;
  artifacts ride in `legacy_artifacts` field.

### Filed as known gaps

- **#6** — `LORE_ROOT` portable resolver (`~/.config/lore/config.toml`
  fallback for host-agnostic resolution).
- **#7** — Verify `lore_mcp.server` protocol compatibility with
  Cursor's MCP client.
- **#8** — Windows support for `lore install` and the Cursor host
  adapter.
- **#9** — Pick a clean PyPI name (`lore` is squatted).

## [0.1.0] — 2026-04-17 (initial alpha)

Initial public release. Linter, schema v2 with `## Issues touched` /
`## Loose ends` sections, MCP server, FTS5 search, curator (stale
detection + supersession + git-date backfill), session-writer
subagent, briefing sinks (Matrix, markdown), `lore attach` /
`lore detach`, scope-prefix `lore resume --scope`, identity +
team-mode machinery.
