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

Three layers:

1. **Hot path — capture** (hook-driven, silent, non-LLM).
2. **Curator A — session-filing** (async, frequent, incremental).
3. **Curator B — defragmentation / graph-abstraction** (async, daily, optional high tier).

```
transcript (host-specific format)
    │
    ▼  [Host adapter — Turn normalisation]
normalised Turn stream + TranscriptHandle
    │
    ▼  [Hot path — SessionEnd / PreCompact / SessionStart-sweep]
sidecar ledger: transcript pending
    │
    ▼  [Curator A — async, merge-or-create]
session note  (canonical vault artefact; draft:true until confirmed)
    │
    ▼  [Curator B — async daily, clock-rollover trigger]
concept / decision / result / paper / …  (per wiki's SURFACES.md)
    │
    ▼  [Briefing — downstream of Curator B]
published digest  (Matrix / Slack / markdown / GH Discussion)
```

Canonical chain: **transcript → Curator A → session note → Curator B → graph edges.** Session note is the first vault artefact. No intermediate fragments.

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

### 5. Curator B — defragmentation

Reads session notes, emits surfaces per the wiki's SURFACES.md.

**Trigger:** clock-rollover at SessionStart-sweep (`date.today() > last_curator_b.date()`) OR manual `lore curator run --abstract`. Not tied to any single session.

**Pipeline:**

1. **Cluster.** Middle-tier groups recent session notes (and existing surface notes) by scope + topic. Parallel-session-written notes cluster here.
2. **Abstract.** For clusters crossing the wiki's declared threshold (LLM judgment): extract a note per the wiki's SURFACES.md types. Sessions that contributed feed `synthesis_sources`. New note gets `draft: true` and `curator_pass`.
3. **Defragment** (if `high` tier enabled). Scan for cross-session drift — duplicate concepts, superseded decisions, orphan wikilinks. Merge duplicates; mark supersessions; propose backlinks.
4. **Maintain.** Frontmatter hygiene (backfill dates, age-out stale) — today's curator work.

**High-tier is config-optional.** `models.high: off` → step 3 falls back to middle tier with a coarser prompt. First-run warning: *"Running without high-tier — expect coarser abstractions, fewer cross-session connections."*

### 6. SURFACES.md

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

### 7. Per-wiki configuration

`$LORE_ROOT/wiki/<name>/.lore-wiki.yml`:

```yaml
git:
  auto_commit: true
  auto_push: false
  auto_pull: true
curator:
  threshold_pending: 3
  threshold_tokens: 50000
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
  quiet: false
```

### 8. Registry tooling

- `lore registry ls` — all attached `CLAUDE.md` → wiki → scope → git-config summary.
- `lore registry show <path>` — full config for one attach.
- `lore registry doctor` — validate attach blocks, check wikis exist, surfaces reachable.

Lightweight, visible on demand, out of the way.

### 9. Backfill

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

### 10. Onboarding (adjacent feature)

`lore onboard` — guided first-run. Walks user through:

- Detect recent projects (git repos, recent Claude Code transcript directories).
- Offer to create a new wiki, attach detected directories, set scope paths, pick SURFACES.md template.
- Kick off `lore backfill --dry-run` → show cost estimate → confirm.

Not the design centre. Wraps `lore new-wiki` + `lore attach` + `lore backfill` + SURFACES.md scaffolder — same primitives.

### 11. Breadcrumb UX

**A. SessionStart banner** — via `additionalContext`:

- Pending: `lore: 3 pending · last curator 2h ago · briefing yesterday`
- Up-to-date: `lore: up to date · 47 notes in private/lore`
- Curator running: `lore: curator A running in background`
- Error / schema drift: `lore: Cursor schema v2 unrecognised — run \`lore doctor\``

**B. Mid-stream confidence signals** — fire only on lore-actionable events:

- Post-commit (PostToolUse on `git commit`): `lore: commit linked to pending capture`
- Pre-compact (PreCompact): `lore: slice captured before compaction`
- Curator-complete async (drain file → next PostToolUse / UserPromptSubmit): `lore: session note filed: 'passive-capture design'`

All single-line, `lore: ` prefix, via `systemMessage` (banner, no multi-line markdown).

Target intensity: 1–2 mid-stream signals per day at typical usage. `breadcrumb.quiet: true` suppresses non-error signals.

### 12. Model tier abstraction

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

---

## Open sections (refine during review)

1. **SURFACES.md → schema.py integration detail.** Exact migration path from today's hardcoded `REQUIRED_FIELDS` dict. Additive; specifics need implementation-plan detail.
2. **Draft-lifecycle auto-promote rules.** Today v1 ships with *user must remove `draft: true` manually.* Options for future: time-based (30 d), pass-count-based (3 passes), user-gesture-based. Pick during review.
3. **Curator B abstraction heuristics.** *"Pattern appears across 3+ session notes"* is one shape; LLM judgment on a well-defined prompt is another. Probably a combo. Needs prompt engineering during implementation, with worked examples from real sessions.

---

## Success criteria

- **Friction removed.** User never types `/lore:session` unless they want to. Session notes appear without gesture.
- **Graph grows.** After a month of use, the wiki has concept / decision / result notes cross-linked from session notes — without the user writing any of them.
- **Cross-tool continuity.** User switches Claude Code → Cursor for a day; session notes from both appear in the same wiki, linked.
- **Non-tech onboarding.** A scientist / designer runs `lore onboard`, answers three questions, gets a working system with backfilled history.
- **No surprise writes.** Only attached-folder transcripts enter the vault. Opus burn is opt-out, not opt-in.

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
