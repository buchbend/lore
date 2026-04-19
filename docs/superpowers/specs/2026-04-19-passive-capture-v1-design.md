# Passive Capture v1 — Architecture Design

- **Status:** draft
- **Date:** 2026-04-19
- **Project:** lore (CLI-first engineering memory)
- **Scope:** Sub-project B of the roadmap — passive capture + curator pipeline.
- **Spec version:** 1

---

## Context

### Why

The lore thesis (`decisions/lore/lore-thesis.md`, 2026-04-17) names its own falsifier:

> *"If engineers empirically refuse to write session notes even when a subagent does the heavy work, the behavioural premise fails. The loop depends on one easy gesture at the end of a session."*

The `/lore:session` gesture is too much friction — humans switch contexts fast, sessions span many topics, and "remember to run the command" is a reliability-zero signal. **This spec removes the gesture.** Session notes write themselves from transcripts, with the human *informed, not consulted* (calm-technology / peripheral-awareness pattern: Weiser & Seely Brown 1996; NN/g on *reversibility cheaper than confirmation*).

### Orthogonal constraints

- **Host-agnostic by design.** Works across Claude Code, Cursor, Codex, OpenCode, Copilot CLI, Gemini CLI. No Claude-Code-only paths in critical flow.
- **Dual-audience.** All artefacts are plain markdown; humans and LLMs read the same files.
- **Markdown-first, git-native.** No hidden caches are authoritative; git is the audit trail.
- **Token-economy discipline.** Cheap tier filters before mid tier; mid tier before high; high is optional.
- **Never blocks the user.** Hooks return in <100 ms. All LLM work runs in detached background processes.
- **Attached folders only.** Nothing captures from unattached cwds — no accidental private-conversation leaks to team wikis.
- **Install → immediate working system.** Non-tech adopters (scientists, designers) shouldn't need cron or systemd.

---

## Architecture overview

Four layers:

1. **Hot path — capture** (hook-driven, silent, non-LLM).
2. **Curator A — session-filing** (async, frequent, incremental).
3. **Curator B — graph abstraction** (async, daily, recent notes only).
4. **Curator C — wiki-wide defragmentation** (async, weekly, high-tier-gated, whole-graph).

```
transcript (host-specific format)
    │
    ▼  [Host adapter — Turn normalisation]
normalised Turn stream + TranscriptHandle (handle retained for later re-read)
    │
    ▼  [Hot path — SessionEnd / PreCompact / SessionStart-sweep]
sidecar ledger: transcript pending
    │
    ▼  [Curator A — async, merge-or-create]
session note  (canonical vault artefact; draft:true until confirmed)
    │
    ▼  [Curator B — async daily, clock-rollover trigger, recent notes]
concept / decision / result / paper / …  (per wiki's SURFACES.md)
    │
    ├─▶  [Briefing — downstream of Curator B]
    │           published digest  (Matrix / Slack / markdown / GH Discussion)
    │
    ▼  [Curator C — async weekly + per-user jitter, whole wiki]
coherent graph:  time-sorted concepts · superseded decisions marked ·
                 duplicates merged · orphan wikilinks repaired
```

Canonical chain: **transcript → Curator A → session note → Curator B → graph edges → Curator C keeps the graph coherent.** Session note is the first vault artefact. No intermediate fragments.

---

## Components

### 1. Host-adapter layer

Each supported host exposes a small adapter normalising its native transcript format to a common `Turn` shape. Downstream components speak only `Turn` and `TranscriptHandle`.

**Protocol:**

```python
@dataclass
class TranscriptHandle:
    host: str               # "claude-code", "cursor", "opencode", ...
    id: str                 # session uuid / rollout id
    path: Path              # on-disk location (debug + user visibility)
    cwd: Path               # session's cwd — drives attach lookup
    mtime: datetime

@dataclass
class Turn:
    index: int              # monotonic within transcript
    timestamp: datetime | None
    role: Literal["user", "assistant", "system", "tool_result"]
    text: str | None
    tool_call: ToolCall | None
    tool_result: ToolResult | None
    reasoning: str | None               # thinking / reasoning blocks
    host_extras: dict                   # format-specific; downstream may ignore

class Adapter(Protocol):
    host: str
    def list_transcripts(self, directory: Path) -> list[TranscriptHandle]: ...
    def read_slice(self, h: TranscriptHandle, from_index: int = 0) -> Iterator[Turn]: ...
    def is_complete(self, h: TranscriptHandle) -> bool: ...
```

**Normalisation philosophy:** hybrid. Common fields normalised (role / text / tool / reasoning); `host_extras` dict carries format-specific extras. Specialist passes may peek; curator defaults ignore.

**Handles as retrieval pointers.** A `TranscriptHandle` (`host`, `id`, `path`) is lore's backbone for provenance. Session notes written by Curator A record handles plus a turn range (`from_index..to_index`). Surfaces extracted by Curator B record `synthesis_sources` pointing at session notes, which in turn point at handles. Curator C, when defragmenting weeks later, can walk from a concept back to its originating session notes and from there back to the raw transcript — via the adapter, cross-host. Summaries are lossy by design; the architecture is not, because the transcript stays reachable.

**V1 adapter set:**

| Adapter | Implementation | Status |
|---|---|---|
| `claude-code` | Claude Agent SDK (`list_sessions(directory=)`, `get_session_messages()`) | Day 1 |
| `cursor` | VS Code `state.vscdb` SQLite, version-pinned schema; falls back to `manual-send` on mismatch | Day 1 |
| `manual-send` | CLI: `lore ingest --from <path\|-> --host <name> --directory <path>` — covers any host without an adapter | Day 1 |
| `codex` | JSONL rollouts at `$CODEX_HOME/sessions/YYYY/MM/DD/rollout-*.jsonl` | Tier 2 |
| `copilot-cli` | JSONL + SQLite at `~/.copilot/session-state/` | Tier 2 |
| `opencode` | JSON-per-object at `~/.local/share/opencode/storage/` | Tier 2 |
| `gemini-cli` | JSON sessions + `tool_output/*.txt` stitcher | Tier 3 |
| `copilot-vscode` | Schema unstable (microsoft/vscode#285535); marginal | Deferred → manual-send |

**Code layout:** `lib/lore_adapters/<host>.py` in the lore monorepo day 1; entry-points discovery for third-party adapters is an additive migration later.

**Why SDK for Claude Code.** Sanctioned read path, survives schema changes, handles crashed-session edge cases (missing `ResultMessage`). Costs one Python dep on `claude-agent-sdk`. Raw-JSONL parse lives in the same adapter as a fallback.

### 2. Sidecar ledger + frontmatter markers (state tracking)

Two-level, hybrid — decision (c) from the brainstorm.

**Sidecar** at `$LORE_ROOT/.lore/transcript-ledger.json`. Per transcript:

```json
{
  "host": "claude-code",
  "transcript_id": "uuid...",
  "path": "/home/.../projects/enc/uuid.jsonl",
  "directory": "/home/buchbend/git/lore",
  "digested_through": 147,
  "synthesised_through": 147,
  "last_mtime": "2026-04-19T10:23:44Z",
  "curator_a_run": "2026-04-19T12:00:00Z",
  "noteworthy": true,
  "session_note": "[[2026-04-19-passive-capture-design]]"
}
```

Per-wiki:

```json
{
  "last_curator_b": "2026-04-19",
  "last_briefing": "2026-04-19",
  "pending_transcripts": 3,
  "pending_tokens_est": 45200
}
```

**Frontmatter markers** — each session note:

```yaml
curator_pass: 2026-04-19T12:00:00Z
synthesised_into: [[concept-passive-capture]]
source_transcripts:
  - "claude-code/uuid...#turn-102..147"
```

Each derived surface:

```yaml
synthesis_sources:
  - [[2026-04-19-passive-capture-design]]
draft: true
curator_pass: 2026-04-19T13:30:00Z
```

**Selection rule (idempotency):** a note is re-queued when `note.mtime > note.curator_pass` OR `note.curator_pass` is missing.

**Audit:** `$LORE_ROOT/.lore/curator.log` — per-run summary, new notes, merges, skips, reasons. `lore curator log` surfaces it.

**Concurrency:** `$LORE_ROOT/.lore/curator.lock` via atomic `mkdir`. Multiple invocations serialise.

### 3. Hot path — triggers

Three events. All return <100 ms. All spawn detached background processes (`subprocess.Popen(..., start_new_session=True)` on Unix; equivalent on Windows).

| Event | Hook | Action |
|---|---|---|
| `SessionEnd` | Claude Code `SessionEnd` | Resolve transcript cwd → walk for `CLAUDE.md` `## Lore`. If attached: mark pending in sidecar; spawn Curator A if pending ≥ threshold. |
| `PreCompact` | Claude Code `PreCompact` | Same. Extra value: captures slices before context compaction drops them. |
| `SessionStart-sweep` | Claude Code `SessionStart` | Read sidecar. Any `mtime > digested_through`? Spawn Curator A. New calendar day + pending work → spawn Curator B (+ briefing if configured). Render breadcrumb. |

**No cron, no systemd, no OS timers.** All triggers are user-caused. Install → immediate working system.

**Manual triggers:**

- `/lore:session [--force]` — force-capture current session; `--force` bypasses noteworthy filter.
- `lore curator run [--abstract] [--briefing]` — explicit curator invocation.
- `/lore:checkpoint` — deferred (PreCompact + SessionEnd cover the cases).

### 4. Curator A — session-filing

Processes pending transcripts. Kafka-style offset per transcript.

**Pipeline:**

1. **Load** pending slices from sidecar ledger (attached-only).
2. **Simple-tier filter.** For each slice: simple-tier produces a compact summary (title, 3–5 bullets, files touched, entities, decisions) + `noteworthy: bool`. If not noteworthy → mark digested, log reason, return. No mid/high-tier calls.
3. **Middle-tier assemble.** For noteworthy slices: stream full transcript (adapter streams `Turn`s; long tool results truncated to metadata). Check recent session notes in the same scope. If judged a continuation → **merge into existing note** (append body sections, bump mtime, re-extract atoms). Else → file new session note with `draft: true`.
4. **Advance ledger.** `digested_through = last_turn_index`; `curator_a_run = now`.

**Merge judgment:** middle-tier call with (a) new slice's summary and (b) recent session notes' frontmatter + summaries in scope. Prompt: *"Is this a continuation of any existing note?"* Returns `merge: <note-path>` or `new`.

**Output:** session notes in `<wiki>/sessions/YYYY-MM-DD-<slug>.md`, frontmatter per existing `session-note-schema-v2`, `draft: true`.

### 5. Curator B — graph abstraction (daily, recent work)

Reads *recent* session notes, emits surfaces per the wiki's SURFACES.md. Deliberately shallow — Curator B surfaces what's new; whole-wiki defragmentation is Curator C's job (§6).

**Trigger:** clock-rollover at SessionStart-sweep (`date.today() > last_curator_b.date()`) OR manual `lore curator run --abstract`. Not tied to any single session.

**Scope of a run:** session notes touched since `last_curator_b` (or last N days, default 3). Older notes are not re-read here.

**Pipeline:**

1. **Cluster.** Middle-tier groups recent session notes (plus their immediate wikilink neighbours) by scope + topic. Parallel-session-written notes cluster here.
2. **Abstract.** For clusters crossing the wiki's declared threshold (LLM judgment): extract a note per the wiki's SURFACES.md types. Sessions that contributed feed `synthesis_sources`. New note gets `draft: true` and `curator_pass`.
3. **Maintain.** Frontmatter hygiene on touched notes (backfill dates, age-out stale) — today's lore-curator work, scoped.

Curator B never rewrites the wiki. New surfaces only. Cross-cutting merges and supersessions are C's territory.

### 6. Curator C — weekly defragmentation (whole wiki, time-aware)

Reads the whole wiki, defragments the graph. Distinct from B: broader scope (all notes, not just recent), slower cadence (weekly), heavier model tier (high by default), time-aware (newer overrides older).

**What it does:**

1. **Adjacent-concept merge.** Scan for concepts that share substantial semantic overlap but exist as separate notes. Propose a merged concept with `synthesis_sources` listing both, and `superseded_by` backlinks on the originals.
2. **Supersession chains.** When a newer decision contradicts an older one (same topic, newer `created:` date, overlapping scope), mark the older `superseded_by: [[newer]]`. Time-awareness: *newer-wins* unless the older note carries explicit `canonical: true` (opt-out).
3. **Orphan wikilink repair.** Broken `[[wikilinks]]` — either the target was renamed (fuzzy-match + propose rewrite) or was deleted (flag for user review).
4. **Graph-wide frontmatter maintenance.** Everything today's `lore curator` does, but across the whole wiki: `last_reviewed` backfill from git-log, stale flagging, etc.
5. **Draft promotion proposals.** C may propose promotion of long-standing drafts (see Open Sections §3 for auto-promote rules).

Output: in-place edits to existing notes (frontmatter + occasional body merges), plus new merged notes and supersession backlinks. Every change carries `curator_c_pass: YYYY-MM-DD` for audit.

**Trigger:** clock-rollover at SessionStart-sweep, **weekly + per-user jitter**:

- Base: new ISO week detected (`iso_week(today) > last_curator_c_week`).
- Jitter: per-user seed from `hash(git.user.email) % 48h`. User A fires Monday morning, user B fires Tuesday afternoon. A team doesn't fire all at once.
- First-come wins: before running, re-read sidecar's `last_curator_c`. If another user already ran this cycle → skip locally, log "already ran by <user> at <ts>".

**Team coordination:** `.lore-wiki.yml` → `curator.curator_c: local | central | off`.

- `local` (default) — every user can fire locally; sidecar + jitter coordinate.
- `central` — skip locally; implies a GitHub Actions / cron job runs C elsewhere (stub for v1; implementation deferred).
- `off` — skip entirely.

**High-tier dependency.** `models.high: off` → Curator C skips steps 1–2 (adjacent-merge and supersession detection need high-tier judgment) and runs only steps 3–5 with middle-tier. First-run warning: *"Curator C running without high-tier — graph defragmentation disabled, only hygiene + orphan repair."*

**Manual trigger:** `lore curator run --defrag`.

### 7. SURFACES.md

Per-wiki at `$LORE_ROOT/wiki/<name>/SURFACES.md`. Embedded YAML per section — dual-audience.

````markdown
# Surfaces — private wiki
schema_version: 2

## concept
Cross-cutting idea or pattern across sessions.

```yaml
required: [type, created, last_reviewed, description, tags]
optional: [aliases, superseded_by, draft]
```

Extract when: pattern appears across 3+ session notes.

## decision
A trade-off made — alternatives considered, path chosen.

```yaml
required: [type, created, last_reviewed, description, tags]
optional: [superseded_by, implements]
```

## result
Concrete outcome — numbers, plots, conclusions.

```yaml
required: [type, created, description, tags, source_session]
```

## paper
Citekey-named publication note.

```yaml
required: [type, citekey, title, authors, year, description, tags]
```
````

**Schema coupling:** `lib/lore_core/schema.py` `REQUIRED_FIELDS` learns to load from SURFACES.md. Additive migration — existing hardcoded types keep working until SURFACES.md is present.

**Lifecycle:**

- `lore new-wiki <name> --surfaces <template>` — shipped templates: `standard` (concept+decision+session), `science` (+paper+result), `design` (+artefact+critique), `custom`.
- `lore surface add <name>` — scaffold new section.
- `lore surface lint` — parseable + schema-consistent + no duplicates. Curator B refuses to run on broken SURFACES.md.

**Versioning:** top-level `schema_version: N`. Field changes bump version. Migrations via existing `lib/lore_core/migrate.py`. Per-surface independent versioning deferred to v2.

### 8. Per-wiki configuration

`$LORE_ROOT/wiki/<name>/.lore-wiki.yml`:

```yaml
git:
  auto_commit: true
  auto_push: false
  auto_pull: true
curator:
  threshold_pending: 3
  threshold_tokens: 50000
  curator_c: local              # local | central | off — weekly defrag
models:
  simple: claude-haiku-4-5
  middle: claude-sonnet-4-6
  high:   claude-opus-4-7       # or 'off'
  defaults: anthropic
briefing:
  auto: true
  audience: personal            # personal | team
  sinks:
    - matrix:#dev-notes
    - markdown:~/lore-briefing.md
breadcrumb:
  session_start: true
  mid_stream:
    on_commit: true
    on_precompact: true
    on_curator_complete: true
    scope_filter: true          # only fire for current-session scope
  quiet: false
```

### 9. Registry tooling

- `lore registry ls` — all attached `CLAUDE.md` → wiki → scope → git-config summary.
- `lore registry show <path>` — full config for one attach.
- `lore registry doctor` — validate attach blocks, check wikis exist, surfaces reachable.

Lightweight, visible on demand, out of the way.

### 10. Backfill

```
lore backfill [--since DATE] [--until DATE] [--hosts h1,...]
              [--wiki W] [--scope S] [--dry-run] [--resume]
```

**Defaults:** `--since` 90 days ago; `--until` now; `--hosts` all enabled adapters.

**Mechanics:**

- Chronological order (oldest → newest). Early seeds first so merge-continuations work.
- Simple-tier noteworthy filter runs first on all transcripts — dominant cost bounded.
- Curator B runs once at the end.
- Attach-only filter; transcripts from unattached cwds skipped (with count). End-of-run prompt offers attach.
- Rate-limit aware (backoff + continue).
- Resumable via ledger.
- Rich progress:

```
Transcripts: 47/230 · simple: 47/47 (12 noteworthy) ·
middle: 8/12 · high: queued · 2.3M tokens used · est $4.20 remaining
```

**Privacy gate before any API call:**

```
About to process N transcripts (attached cwds only), X turns total.
Estimated cost: $Y.
Content may include private conversations, secrets, client data.
Proceed? [y/N]
```

### 11. Onboarding (adjacent feature)

`lore onboard` — guided first-run. Walks user through:

- Detect recent projects (git repos, recent Claude Code transcript directories).
- Offer to create a new wiki, attach detected directories, set scope paths, pick SURFACES.md template.
- Kick off `lore backfill --dry-run` → show cost estimate → confirm.

Not the design centre. Wraps `lore new-wiki` + `lore attach` + `lore backfill` + SURFACES.md scaffolder — same primitives.

### 12. Breadcrumb UX

**A. SessionStart banner** — via `additionalContext`:

- Pending: `lore: 3 pending · last curator 2h ago · briefing yesterday`
- Up-to-date: `lore: up to date · 47 notes in private/lore`
- Curator running: `lore: curator A running in background`
- Error / schema drift: `lore: Cursor schema v2 unrecognised — run \`lore doctor\``

**B. Mid-stream confidence signals** — fire whenever something worth noting happens in the vault.

All background-job output flows through a **drain file** (`$LORE_ROOT/.lore/breadcrumbs.drain.jsonl`, one JSONL entry per event). Hooks (PostToolUse / UserPromptSubmit) read the drain, emit one `systemMessage` line per undelivered entry, then mark delivered. Background jobs don't write to stdout — they append to the drain and let the next hook surface them.

**Events that write to the drain:**

- **Capture** (written by hot path)
  - Post-commit (via PostToolUse on `git commit`): `lore: commit linked to pending capture`
  - Pre-compact (via PreCompact): `lore: slice captured before compaction`
- **Curator A** (session-filing)
  - Session note added: `lore: session note filed: 'passive-capture design'`
  - Session note merged (continuation): `lore: work merged into 'passive-capture design'`
  - Non-noteworthy transcript skipped: (silent by default; `breadcrumb.verbose: true` to surface)
- **Curator B** (graph abstraction)
  - New concept surfaced: `lore: new concept 'peripheral-awareness-pattern'`
  - New decision surfaced: `lore: new decision 'hybrid-state-tracking'`
  - Result / paper / custom surface: `lore: new result '<title>'`
- **Curator C** (defragmentation)
  - Supersession marked: `lore: 'old-decision' superseded by 'new-decision'`
  - Concepts merged: `lore: merged 'concept-a' + 'concept-b' → 'concept-c'`
  - Orphan wikilinks repaired: `lore: 3 wikilinks repaired`
- **Briefing**
  - Briefing published: `lore: briefing published to matrix:#dev-notes`

**Drain entry shape:**

```json
{"ts": "2026-04-19T13:24:12Z", "source": "curator_b", "event": "concept_surfaced",
 "note": "concepts/passive-capture.md", "scope": "private/lore",
 "render": "lore: new concept 'passive-capture'"}
```

All single-line, `lore: ` prefix, via `systemMessage` (banner, no multi-line markdown).

**Scope filtering** (config: `breadcrumb.mid_stream.scope_filter: true`, default on). Mid-stream signals fire only when the triggering event is in the *current session's attached scope* (or a parent scope). A session note filed for `private/science` while you're working in `private/lore` is silent — the note exists in the vault but doesn't surface here. We know the scope because attach resolution at hot-path time already recorded it.

Effect: session-relevant breadcrumbs can fire as often as needed to convey genuine activity, because irrelevant-scope events don't surface at all. Target intensity: every relevant event surfaces; cross-scope noise stays silent. `breadcrumb.quiet: true` suppresses all non-error signals.

### 13. Model tier abstraction

Three semantic tiers: `simple`, `middle`, `high`. Every LLM call site references a tier, not a concrete model.

**Rules:**

- `high: off` → Curator B step 3 falls back to `middle`. First-run warning shown.
- `simple: simple; middle: simple; high: off` (the "cheap lore" config) → louder warning: *"All tiers on simple — outcomes may be crippled. Proceed?"*
- Non-Anthropic providers: tier names stay; adapter maps to provider tiers.
- Sane defaults shipped with each new wiki.

**Implementation:** small `ModelTier` enum in code; concrete-model resolution at call time from config. Benchmarking across providers becomes trivial.

---

## Privacy + safety

- **Attached-only capture** — no accidental private-conversation leaks.
- **Confirmation gate on backfill** before any API call.
- **Draft-by-default.** Curator-authored notes carry `draft: true`; user edits / deletes freely; git is the undo.
- **Atomic writes with mtime guard.** Curator re-reads before patching; aborts on mid-edit race (Obsidian-held-file case). Already in existing curator.
- **Lockfile** prevents concurrent curator runs from corrupting the ledger.
- **Obsidian-hold detection** retained from existing curator.
- **Logging.** `vault/.lore/curator.log` + `lore curator log`. Every curator action auditable.

---

## Non-goals / deferred

- Mobile / cloud-hosted session capture (v4 / v5).
- In-session direction-switch detection (manual `/lore:session` covers the case).
- Automatic issue-filing on loose-ends — planned as a Curator B extension but not v1.
- Cursor native extension / plugin.
- Per-surface independent versioning (wiki-wide `schema_version` for v1).
- Templates polluting Obsidian graph (separate issue).
- **Central Curator C mode** (GitHub Actions / scheduled CI). Config flag in place; implementation deferred to v2. Local-only for now.

---

## Open sections (refine during review)

1. **SURFACES.md → schema.py integration detail.** Exact migration path from today's hardcoded `REQUIRED_FIELDS` dict. Additive; specifics need implementation-plan detail.
2. **Draft-lifecycle auto-promote rules.** Today v1 ships with *user must remove `draft: true` manually.* Options for future: time-based (30 d), pass-count-based (3 passes across Curator B/C), user-gesture-based. Curator C could propose promotions. Pick during review.
3. **Curator B abstraction heuristics.** *"Pattern appears across 3+ session notes"* is one shape; LLM judgment on a well-defined prompt is another. Probably a combo. Needs prompt engineering during implementation with worked examples from real sessions.
4. **Curator C team coordination in central mode.** Local-mode jitter + sidecar first-come-wins is clean. Central mode (GitHub Actions) needs conflict resolution with in-flight local runs if any. V1 ships local-only; central is a `central | off` stub.
5. **Drain-file delivery semantics.** How does the hook mark entries delivered — rewrite the drain excluding delivered entries, or keep a `.delivered.jsonl` sidecar? Rewrite is simpler but writes under every hook; sidecar is append-only but needs periodic compaction. Pick during implementation.

---

## Success criteria

- **Friction removed.** User never types `/lore:session` unless they want to. Session notes appear without gesture.
- **Graph grows.** After a month of use, the wiki has concept / decision / result notes cross-linked from session notes — without the user writing any of them.
- **Graph stays coherent.** After a quarter of use, Curator C has merged duplicate concepts, marked superseded decisions, and repaired orphan wikilinks — the wiki doesn't accumulate drift.
- **Cross-tool continuity.** User switches Claude Code → Cursor for a day; session notes from both appear in the same wiki, linked.
- **Non-tech onboarding.** A scientist / designer runs `lore onboard`, answers three questions, gets a working system with backfilled history.
- **No surprise writes.** Only attached-folder transcripts enter the vault. Opus burn is opt-out, not opt-in. Cross-scope events don't leak into the current session's breadcrumbs.

---

## Related

- Decision: `lore-thesis` (2026-04-17) — the thesis passive capture is the falsifier relief for.
- Decision: `lore-orthogonality` — why lore doesn't duplicate task state, code, commit history.
- Decision: `lore-dual-audience` — why SURFACES.md is markdown not YAML-only.
- Decision: `git-aware-not-git-dependent` — attached-folders-not-repos.
- Decision: `status-vocabulary-minimalism` — why `draft: true` is the lifecycle signal.
- Concept: `session-note-schema-v2` — session-note structure reused here.
- Concept: `claude-code-hook-schema` — hook output envelope rules.
- Research (2026-04-19): transcript availability matrix across coding agents (in-session agent output).
- Issue: [buchbend/lore#13](https://github.com/buchbend/lore/issues/13) — `lore attach write` TypeError (unrelated; noted in session).
