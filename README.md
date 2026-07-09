# Lore

**LLM-optimized knowledge graph for AI-coding teams.** Session notes
auto-extracted into durable knowledge, repo-scoped context injected at
session start, pluggable team briefings. No vector DB needed for small
vaults; a full hybrid search + MCP server for larger ones.

> ⚠️ **Pre-1.0.** APIs, hook contracts, skill surfaces, frontmatter
> schema, and CLI flags can still change between minor versions. Not
> recommended for wikis you can't re-migrate into shape.

## The pitch

When you work with an AI agent, the decisions, reasoning, and open
threads live in a chat window that disappears. PRs capture the diff;
nothing captures *why*. Lore closes the loop:

```
Session with AI  →  (auto at SessionEnd / PreCompact / cap-trip)
                 →  transcript captured into a per-session buffer
                 →  one chapter composed per flush, gated for PII/
                    secrets/directive phrasing before it's appended
                 →  one lab-notebook session note per session,
                    readable via MCP pull at next SessionStart
```

The flagship is the **session-note pipeline**: capture is automatic —
no explicit capture command needed. See the "Bootstrap" section below.
Everything else (search, MCP, hygiene curator) serves the same
pipeline. Ratified decisions live in the connected repo's ADRs/PRDs,
pulled on demand via MCP — Lore does not extract decisions from
transcripts.

## Two plugins: `lore` + `lore-workflow`

This repo ships **two marketplace entries**, versioned and installed
independently:

| Plugin | What it's for | Depends on |
|---|---|---|
| **`lore`** | The notes/vault system above: session capture, search, MCP, briefings. | nothing else |
| **`lore-workflow`** | An opinionated planning chain — epics, PRDs, TDD — that calls `lore`'s deterministic substrate (code map, model tiers). | `lore` |

`lore-workflow` is opt-in: install `lore` alone for the notes pipeline, or
add `lore-workflow` on top once you also want the planning chain. The
dependency only runs one way — nothing in `lore` core imports or requires
`lore-workflow`.

The chain it bundles:

```
seed-epic → orient → grill-with-docs → to-epic → orchestrate-epic → document-epic
```

with `implement-issue` as a lighter-weight track for one well-understood
issue, and `tdd` as the discipline every implementation teammate follows.
See [`docs/conventions.md`](docs/conventions.md) for the full chain, the
artifact-home contract (PRD/ADR/`AGENTS.md` placement), and the tier
vocabulary; [`docs/how-to/`](docs/how-to/) for task recipes
(run an epic, use the fast path, resume a broken epic, onboard a repo); and
[`lore-workflow/README.md`](lore-workflow/README.md) for the skill roster.

Install both from this one marketplace:

```text
/plugin marketplace add buchbend/lore
/plugin install lore@lore
/plugin install lore-workflow@lore
```

## Canonical shape

```
$LORE_ROOT/                 # default ~/lore (or set LORE_ROOT=...)
├── sessions/               # personal logs (optional)
├── inbox/                  # personal triage inbox (optional)
├── drafts/                 # WIP notes (optional)
├── templates/              # note templates (optional)
└── wiki/                   # always present — ≥1 mounted wiki
    └── <name>/             # symlink to a wiki git repo (or inline dir)
```

Each wiki is an independent git repo. Access control, shipping, history
stay at the repo boundary; Obsidian sees one unified graph via symlinks.

## Install

**One-liner (canonical path).** Works on Linux + macOS in v1 (Windows
tracked as a known gap):

```bash
curl -fsSL https://raw.githubusercontent.com/buchbend/lore/main/install.sh | sh
lore init                                               # scaffold a vault + set $LORE_ROOT
```

The bootstrap script picks `pipx` / `uv tool` / `pip --user` (in that
preference order), installs the `lore` CLI, then chains into
`lore install` to wire up Claude Code + Cursor integrations and refresh
Claude's plugin cache. Re-running it is the canonical upgrade path —
or use `lore install --upgrade` once the binary is on your PATH.

### Manual install

If you'd rather skip the bootstrap script and install by hand:

```bash
pipx install "git+https://github.com/buchbend/lore.git#egg=lore[capture]"  # CLI + passive-capture extras
lore install                                            # detect installed integrations, wire each
lore init                                               # scaffold a vault + set $LORE_ROOT
```

The `[capture]` extra adds the `claude-agent-sdk` + `anthropic` packages used
by the curator to summarise transcripts. Drop it (`#egg=lore`) to install
without LLM-driven capture; you'll still get retrieval, sessions, and
briefings, just not auto-extraction.

> **Note:** the bare `pipx install lore` form will *not* work — the
> name `lore` is squatted on PyPI by an unrelated package. Use the
> `git+https://...` form above. We'll switch to a clean PyPI name
> once one is picked (tracked in an issue).

`lore install` walks every detected integration (Claude Code, Cursor in v1)
and shows what it'll change before doing anything. One prompt per
integration; `--yes` for non-interactive use. The hooks, MCP server, skills,
and subagents come from `.claude-plugin/plugin.json` — Claude Code's
plugin system does the wiring; Lore stays out of `~/.claude/settings.json`.

For Cursor, `lore install` writes `mcpServers.lore` into the per-platform
mcp.json (`~/Library/Application Support/Cursor/User/` on macOS,
`${XDG_CONFIG_HOME:-~/.config}/Cursor/User/` or `~/.cursor/` on Linux)
and a `lore-managed` block to your Cursor rules dir.

### Uninstall

```bash
lore uninstall                  # symmetric remove
```

Removes the entries Lore added — including from shared JSON files like
`~/.cursor/mcp.json`. Other servers / your own edits outside Lore-managed
markers stay put.

### Migrating from the pre-v0.10 `install.sh`

> Only relevant if you ran a `lore` `install.sh` from before v0.10 — the
> one that wrote skill symlinks directly into `~/.claude/skills/`. The
> current `install.sh` is the thin bootstrap installer documented in
> § Install above; it never writes those symlinks.

If you ran the pre-v0.10 bash installer, `lore install` will refuse
with a clear warning until you reset:

```bash
git clone https://github.com/buchbend/lore.git    # if you don't have a checkout
cd lore
python3 tools/undo_install_sh.py --dry-run        # preview what would change
python3 tools/undo_install_sh.py                  # apply
curl -fsSL https://raw.githubusercontent.com/buchbend/lore/main/install.sh | sh
```

The undo helper is stdlib-only Python; runs even if `lore` isn't on
your PATH yet.

### As a Claude Code plugin (via marketplace)

The repo is a self-describing marketplace:

```
/plugin marketplace add buchbend/lore
/plugin install lore@lore
```

That alone gives you the `lore` plugin (hooks, skills, subagents, MCP); add
`/plugin install lore-workflow@lore` for the planning-chain skills too — see
[§ Two plugins](#two-plugins-lore--lore-workflow). Installing `lore` alone
does not install the `lore` CLI itself. Run
`pipx install "git+https://github.com/buchbend/lore.git#egg=lore[capture]"`
separately, or use `lore install --integration claude` once `lore` is on
your PATH (it'll subprocess `claude plugin install lore@lore` for you).

### Dev install (editable, also the offline / air-gapped path)

See [CONTRIBUTING.md](./CONTRIBUTING.md) for the editable-from-checkout
recipe. Same recipe is the path for installs on machines without
network egress to PyPI / the marketplace.

## Bootstrap: passive capture

Session notes auto-extract from Claude Code transcripts; explicit
capture commands are no longer needed. Each session's turns accumulate
in a buffer and are composed into chapters of a single, per-session
lab-notebook note (see `CONTEXT.md` for the full model). Anything else
in a wiki — concepts, decisions, projects, reference notes — is
written directly, by hand or via `/lore:inbox`; there is no automatic
daily abstraction pass.

### Update from an older install

One command — upgrades the binary, refreshes integrations, and pokes
Claude's plugin cache:

```bash
lore install --upgrade
```

`--upgrade` (or `-u`) delegates to `install.sh upgrade`, which picks
your installer (pipx / uv / pip), pulls the latest `lore`, then
re-enters `lore install` so hooks/skills/MCP stay in sync. Re-running
the curl one-liner from § Install does the same thing if you don't
have `lore` on PATH for any reason.

For an editable dev checkout:

```bash
cd /path/to/your/lore-checkout
git pull origin main
pip install -e ".[capture]"
lore install
```

(Fresh installs follow [§ Install](#install) above — the `[capture]` extras
are already part of the canonical command.)

### Attach a repo — one step per repo you work in

Each repo needs a `## Lore` block in its root `CLAUDE.md` so the
capture path knows which wiki / scope to file notes under:

```bash
cd /path/to/your/repo
```

Then in a Claude Code session in that repo, ask Claude to run
`lore attach` (or run it from a shell):

```
lore attach
```

Interactive — asks for wiki + scope, writes the managed block to
`CLAUDE.md`. Idempotent; safe to re-run.

### What runs automatically

Once attached with a wiki present:

- **Claude Code SessionEnd / PreCompact hooks**, plus ordinary tool
  activity, drive a per-session buffer-and-flush heartbeat — no cron.
  A flush composes one chapter (one LLM call) from the buffered slice
  and appends it to the session's note, behind the publish gate. No
  LLM runs inline in the hook itself; the flush is detached.
- **SessionStart** also sweeps any session whose owning process is
  provably dead (crash, closed laptop lid) — its note gets one compose
  attempt and closes, under a global singleton lock. All detached —
  SessionStart never blocks.
- **Banner at SessionStart** is deliberately minimal: a status line, an
  optional Focus block, at most two last-session hints, freshness
  lines only on positive evidence, and a fixed directive pointing at
  MCP pull for anything deeper. `lore!:` prefix flags actionable
  errors.

### Manual escape hatches

- `lore ingest --from <file.jsonl> --integration cursor --directory <cwd>` —
  ingest a transcript from any integration lore doesn't auto-capture.
- `lore curator run` — run the buffer/flush heartbeat now (files
  session notes from pending transcripts).
- `lore curator flush <buffer-sidecar>` — run the flush worker for one
  buffer directly.
- `lore curator sweep` — close every dead session's note now, under
  the singleton lock.
- `lore curator reap` — force-flush buffers whose owning session
  crashed.
- `lore curator [--wiki <name>] [--apply]` — the frontmatter-only
  hygiene pass (supersession, `implements:` back-links, git-date
  backfill, team-mode hint); dry-run by default.
- `lore registry ls` / `lore registry doctor` —
  list configured wikis and validate them. (For looking up the
  attachment covering a specific path, use `lore attachments show
  <path>`.)

### Per-wiki configuration

Each wiki can set its own knobs in `<wiki>/.lore-wiki.yml`:

```yaml
git:
  auto_commit: true
  auto_push: false              # push manually by default
  auto_pull: true
curator:
  threshold_pending_turns: 30   # spawn the heartbeat when ≥ N buffered turns
  max_pending_age_s: 600        # OR the oldest pending entry is this old
  a_noteworthy_tier: middle     # middle (default) | simple (cheap, higher false-neg)
  synthesis_buffer_cap_turns: 120   # flush a chapter at this many turns...
  synthesis_buffer_cap_chars: 240000 # ...or this many transcript chars
  synthesis_model_tier: middle  # which `models.*` tier composes chapters
models:
  simple: claude-haiku-4-5
  middle: claude-sonnet-4-6
  high:   claude-opus-4-7       # or 'off' — degrades synthesis_model_tier: high to middle
briefing:
  audience: personal
  sinks:
    - markdown:~/lore-briefing.md
breadcrumb:
  mode: normal                  # quiet | normal | verbose
  scope_filter: true
```

All fields default to sane values — start without a `.lore-wiki.yml`
and add knobs only as you need them. Briefings publish manually
(`lore briefing publish`) — there is no automatic cadence.

## Observability

The capture pipeline writes structured logs so you can inspect what it did — and
why. Four commands cover the common scenarios:

| Scenario | Command |
|---|---|
| **"Is Lore doing anything for me right now?"** | **`lore status`** |
| "I had a session and no note appeared" | `lore runs show latest` |
| "Hook plumbing feels off" | `lore doctor` |
| "I'm tuning noteworthy/merge config" | `lore curator run --dry-run --trace-llm` |

`lore status` is the first thing to run when you're wondering whether Lore is
alive. It prints a 7-line activity-first dashboard: pending transcripts, last
hook event time, last curator run, hook backlog age. Decay-ordered, loud-on-
earning — silent lines mean nothing wrong, prominent lines mean attention
warranted.

`lore runs list` prints a table of recent curator runs. `lore runs show <id>`
accepts the alias `latest`, carets `^1`..`^N`, the 6-char random suffix
(e.g. `a1b2c3`), or any unique prefix of the full ID.

Logs live under `$LORE_ROOT/.lore/`:

- `hook-events.jsonl` — one line per hook invocation
- `runs/<id>.jsonl` — one file per curator run (decision trace)
- `runs/<id>.trace.jsonl` — optional LLM prompt/response trace (enabled by
  `LORE_TRACE_LLM=1` or `--trace-llm` on `lore curator run`)

Retention is count + MB capped; configure at `$LORE_ROOT/.lore/config.yml`:

~~~yaml
observability:
  hook_events:
    max_size_mb: 10
    keep_rotations: 1
  runs:
    keep: 200
    max_total_mb: 100
    keep_trace: 30
~~~

## Two onboarding recipes

### 1. Polymath — many wikis, one brain

You have multiple knowledge domains (work, research, personal). Mount
them all under one root:

```
mkdir -p ~/lore/wiki
cd ~/lore/wiki
ln -s ~/git/myorg/team-knowledge team
ln -s ~/git/research/knowledge research
# personal wiki lives inline at ~/lore/wiki/personal/
```

Then run `lore init` to write the root CLAUDE.md and you're set.

### 2. Single-wiki — one team's knowledge only

You just want the team vault and its skills:

```
mkdir -p ~/lore/wiki
ln -s ~/git/myorg/team-knowledge ~/lore/wiki/team
```

All `/lore:*` commands work with a single mount; no routing prompts.

## Curator LLM backend — Claude subscription, Anthropic API, or OpenAI-compatible

The curator can talk to three different LLM backends. Pick one based on
where your API budget lives:

| Backend | Selector | Auth | Used when |
|---|---|---|---|
| **Subscription** (`claude` CLI on PATH) | `subscription` | your existing `claude` login | you have a Claude Pro / Team subscription and the `claude` binary on PATH — zero extra cost per curator call |
| **Anthropic API** (SDK) | `api` | `ANTHROPIC_API_KEY` | you want Claude models but pay per token |
| **OpenAI-compatible** | `openai` | `LORE_OPENAI_API_KEY` + `LORE_OPENAI_BASE_URL` | you have an institutional gateway, a local model server (vLLM, llama.cpp, Ollama with the openai shim, LiteLLM, OpenRouter, …) or want to point at the real OpenAI API |
| **auto** | `auto` | first one that works | the default — `claude` on PATH → API key → OpenAI gateway → no LLM (cascade rules only) |

Selection precedence, highest first: CLI flag (`--backend`) → env var
`LORE_LLM_BACKEND` → `curator.backend` in `$LORE_ROOT/.lore/config.yml`
→ `auto`.

### OpenAI-compatible backend setup

Two files under `$LORE_ROOT/.lore/`. Non-secrets go in YAML; the API key
goes in a separate, gitignored env file.

**`$LORE_ROOT/.lore/config.yml`** — diffable, shareable:

```yaml
curator:
  backend: openai
  openai:
    base_url: https://chat.kiconnect.nrw/api/v1   # your gateway root
    # api_key_env: LORE_OPENAI_API_KEY            # optional override
    model_simple: gpt-4o-mini                     # cheap tier
    model_middle: gpt-4o                          # default tier (chapter compose at synthesis_model_tier: middle)
    model_high:   gpt-4o                          # heaviest tier (synthesis_model_tier: high)
```

**`$LORE_ROOT/.lore/secrets.env`** — secrets only, mode `0600`:

```
# Auto-loaded by Lore at curator startup.
# Process env wins; this file fills in anything the shell didn't export.
LORE_OPENAI_API_KEY=sk-...
```

```bash
chmod 600 $LORE_ROOT/.lore/secrets.env
```

That's it. The whole `$LORE_ROOT/.lore/` directory is gitignored at the
vault level (see the default `.gitignore` written by `lore init`), so
the file never ends up in a commit. Lore warns at load time if the file
is readable by group or other.

**Resolution rules per field**, highest precedence first:

1. process env (e.g. `LORE_OPENAI_API_KEY` exported in your shell)
2. `$LORE_ROOT/.lore/secrets.env`
3. `$LORE_ROOT/.lore/config.yml` → `curator.openai.*`
4. unset → curator falls back to subscription/API/no-LLM per `auto`

The grammar of `secrets.env` is the dotenv subset every editor renders:
one `KEY=VALUE` per line, `#` for comments, blank lines ignored, single
or double quotes around the value optional and stripped on read. Any
line that isn't shaped like that emits a one-line warning and is
skipped — Lore will not silently fall over because of a stray paste.

Recognised env vars (any of these can live in `secrets.env` *or* the
shell — same precedence):

| Var | Purpose |
|---|---|
| `LORE_LLM_BACKEND` | overrides `curator.backend` (`subscription` \| `api` \| `openai` \| `auto`) |
| `LORE_OPENAI_BASE_URL` | OpenAI-compatible API root |
| `LORE_OPENAI_API_KEY` | API key for the OpenAI-compatible endpoint |
| `LORE_OPENAI_MODEL_SIMPLE` / `_MIDDLE` / `_HIGH` | per-tier model override |
| `ANTHROPIC_API_KEY` | for the `api` backend |

Verify the wiring with `lore doctor` (which checks selectability) or run
a one-shot: `lore curator run --backend openai --dry-run --trace-llm` —
the trace file under `$LORE_ROOT/.lore/runs/<id>.trace.jsonl` shows the
exact prompt/response pair for inspection.

### Recommended openai-backend setup (Curator A narration)

The narrator used to default to Mistral-119B, which has a known structural
failure on retraction-heavy transcripts — it asserts decisions and outcomes
the transcript later walks back (see experiments **005**, **006**, **007**
in the [`lore-experiments`](https://github.com/) repo). Swapping the high
tier to **GPT-OSS-120B with `reasoning_effort=high`** roughly halves
contradicted claims (4 → 2 on the 007 sample) and passes the pre-committed
gate.

```yaml
curator:
  openai:
    base_url: https://chat.kiconnect.nrw/api/v1
    model_high: "Openai GPT OSS 120B"
    reasoning_effort_high: high
# Per-wiki, in <wiki>/.lore-wiki.yml — route Curator A to the high tier:
# curator:
#   synthesis_model_tier: high
```

- **Latency**: GPT-OSS-120B at `reasoning_effort=high` takes 80–100s per
  Curator A call vs ~10s for Mistral. The session-note pipeline is async,
  so this is acceptable but worth knowing.
- **Cost**: both Mistral-119B and GPT-OSS-120B are free on the
  kiconnect.nrw endpoint Lore points at by default.
- **Backwards compatibility**: existing Mistral-only configs keep working
  unchanged — leave `reasoning_effort_high` unset and nothing flips.

## Scheduling the curator — cost-free defaults

The hygiene curator (propagates `supersedes:` / `implements:`
relations, backfills dates from git, hints at team-mode) can run
several ways. The README picks no default for you; pick your
trade-off:

| Pattern | Cost | Cadence | For |
|---------|------|---------|-----|
| `/schedule /lore:curator <wiki>` on laptop | **free** | any | individuals |
| `cron` + `claude -p "/lore:curator <wiki>"` | **free** | any | power users, no `/schedule` |
| GitHub Actions, **on push** to a wiki repo | **API $** | per-push, incremental | shared team wikis |
| GitHub Actions, cron | **API $** | nightly | always-on, no laptop |
| Home server + cron | **free** | any | users with always-on box |

Reference workflows in [`examples/`](./examples). Every LLM invocation
costs tokens; no default forces a cost on you.

## Using Lore with an existing markdown vault

Point `LORE_ROOT` at your vault (anything matching the canonical shape
— a directory with a `wiki/` subfolder containing at least one mounted
wiki) and add `schema_version: 1` to existing notes:

```
LORE_ROOT=/path/to/your/vault lore migrate --add-schema-version
# review the dry-run diff, then:
LORE_ROOT=/path/to/your/vault lore migrate --add-schema-version --apply
```

No files move. If your vault does not yet match the canonical shape,
`lore init` scaffolds it without touching your notes.

## Design principles

- **Markdown + git stay authoritative.** No database the vault can't be
  rebuilt from.
- **Cheap context is automatic; expensive context is explicit.** Inject
  bounded, deterministic context at SessionStart and PreCompact (reading
  cached files the linter regenerates). Invoke the LLM only at judgment
  points: session extraction, contradiction checks, import enrichment,
  curator review, briefing prose.
- **Compose, don't replace.** Skills orchestrate; MCP and CLI tools
  provide retrieval primitives; peer knowledge tools layer alongside.
- **No PreToolUse auto-enrichment.** Auto-injecting vault content on
  every tool call burns tokens and risks misleading the agent when the
  vault is stale. Lore is token-preserving by default: deterministic
  context is injected once at session start; the agent pulls more via
  MCP when it decides retrieval would help.

## Star history

[![Star History Chart](https://api.star-history.com/svg?repos=buchbend/lore&type=Date)](https://star-history.com/#buchbend/lore&Date)

## License

MIT. See [LICENSE](./LICENSE).
