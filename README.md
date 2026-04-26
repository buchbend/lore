# Lore

**LLM-optimized knowledge graph for AI-coding teams.** Session notes
auto-extracted into durable knowledge, repo-scoped context injected at
session start, pluggable team briefings. No vector DB needed for small
vaults; a full hybrid search + MCP server for larger ones.

> ⚠️ **Work in progress — 0.1 alpha.** APIs, hook contracts, skill
> surfaces, frontmatter schema, and CLI flags are all still changing.
> Expect breakage. Not recommended for wikis you can't re-linter into
> shape. Core linter + schema + migration are in place; session
> pipeline, search, MCP, curator, and the scope/team/identity MVP
> (tracked in [#3](https://github.com/buchbend/lore/issues/3)) are
> under active implementation. No stability guarantee until 0.2.

## The pitch

When you work with an AI agent, the decisions, reasoning, and open
threads live in a chat window that disappears. PRs capture the diff;
nothing captures *why*. Lore closes the loop:

```
Session with AI  →  (auto at SessionEnd / PreCompact)
                 →  transcript captured, session note filed (draft:true)
                 →  daily: Curator B abstracts concepts / decisions / results
                 →  briefing published to configured sinks
                 →  graph surfaces at next SessionStart, scoped
                    to the repo you're in
```

The flagship is the **session-note pipeline**. As of the work on `main`
(Plans 1 + 2 of the passive-capture roadmap), capture is automatic —
no `/lore:session` gesture needed. See the "Bootstrap" section below.
Everything else (search, MCP, curator C) serves the same pipeline.

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

**This is the canonical install path. Everything below is for special cases
(uninstall, marketplace, dev checkout, migration).** Three commands. Works on
Linux + macOS in v1 (Windows tracked as a known gap).

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

### Migrating from `install.sh` (legacy)

If you ran the old bash installer, `lore install` will refuse with a
clear warning until you reset:

```bash
git clone https://github.com/buchbend/lore.git    # if you don't have a checkout
cd lore
python3 tools/undo_install_sh.py --dry-run        # preview what would change
python3 tools/undo_install_sh.py                  # apply
pipx install "git+https://github.com/buchbend/lore.git#egg=lore[capture]"   # install the new CLI
lore install                                      # then proceed cleanly
```

The undo helper is stdlib-only Python; runs even if `lore` isn't on
your PATH yet.

### As a Claude Code plugin (via marketplace)

The repo is a self-describing marketplace:

```
/plugin marketplace add buchbend/lore
/plugin install lore@lore
```

That alone gives you the plugin (hooks, skills, subagents, MCP) — it
does not install the `lore` CLI itself. Run
`pipx install "git+https://github.com/buchbend/lore.git#egg=lore[capture]"`
separately, or use `lore install --integration claude` once `lore` is on
your PATH (it'll subprocess `claude plugin install lore@lore` for you).

### Dev install (editable, also the offline / air-gapped path)

See [CONTRIBUTING.md](./CONTRIBUTING.md) for the editable-from-checkout
recipe. Same recipe is the path for installs on machines without
network egress to PyPI / the marketplace.

## Bootstrap: passive capture (new, on `main`)

As of the work on `main` (see
`docs/superpowers/specs/2026-04-19-passive-capture-v1-design.md`),
session notes auto-extract from Claude Code transcripts without
`/lore:session`, and surfaces (concepts, decisions, results, …) are
abstracted daily by Curator B.

### Update from an older install

```bash
pipx install --force "git+https://github.com/buchbend/lore.git#egg=lore[capture]"
lore install                                 # re-wire hooks + skills (picks up new SessionEnd wiring)
```

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

Then in a Claude Code session in that repo:

```
/lore:attach
```

Interactive — asks for wiki + scope, writes the managed block to
`CLAUDE.md`. Idempotent; safe to re-run.

### Bootstrap a wiki with surfaces

If you're creating a new wiki, declare which surfaces Curator B should
extract:

```bash
lore new-wiki team-wiki --surfaces standard  # concept + decision + session
# other templates:
lore new-wiki research --surfaces science    # + paper + result
lore new-wiki product --surfaces design      # + artefact + critique
lore new-wiki scratch --surfaces custom      # skeleton you fill yourself
```

`SURFACES.md` is human-editable markdown with embedded YAML. Three ways
to author and maintain it:

- **Design a wiki's vocabulary (interactive, LLM-guided):**
  `lore surface init --wiki <name>` drops into the `/lore:surface-init`
  skill in Claude Code. Guided holistic design — one open question,
  full synthesis, per-surface refinement, hybrid commit.
- **Add one new surface (interactive, LLM-guided):**
  `lore surface add --wiki <name>` drops into `/lore:surface-add`.
  Proposes a full draft from one open question with semantic-overlap
  detection against existing surfaces.
- **Scripted / automation / no-LLM:** write a `draft.json`
  (schema: `lore.surface.draft/1`) and run
  `lore surface commit <path>`. This is also how the skill paths write
  under the hood. `lore new-wiki <name> --surfaces <template>` (above)
  remains the fastest way to seed a wiki headlessly.

Both interactive flows require `claude` on PATH. The `commit` primitive
does not. Run `lore surface lint` anytime to validate the file.

See `docs/superpowers/specs/2026-04-20-surface-authoring-design.md` for
the full design.

### What runs automatically

Once attached with a wiki present:

- **Claude Code SessionEnd / PreCompact hooks** update the sidecar
  transcript ledger. No LLM in the hook itself; Curator A runs in a
  detached background subprocess when pending work crosses threshold.
- **First SessionStart of each calendar day** also spawns Curator B
  for the attached wiki (graph abstraction) and publishes a briefing
  if configured. All detached — SessionStart never blocks.
- **Banner at SessionStart** shows pending state:
  `lore: 3 pending · last curator 2h ago · briefing yesterday`.
  `lore!:` prefix flags actionable errors (broken SURFACES.md, etc.).

### Manual escape hatches

- `lore ingest --from <file.jsonl> --integration cursor --directory <cwd>` —
  ingest a transcript from any integration lore doesn't auto-capture.
- `lore curator run` — run Curator A now.
- `lore curator run --abstract [--wiki <name>]` — run A then B.
- `lore curator run --abstract --dry-run` — see what would happen.
- `lore registry ls` / `lore registry doctor` —
  list configured wikis and validate them. (For looking up the
  attachment covering a specific path, use `lore attachments show
  <path>`.)
- `/lore:session` — still available for explicit capture.

### Per-wiki configuration

Each wiki can set its own knobs in `<wiki>/.lore-wiki.yml`:

```yaml
git:
  auto_commit: true
  auto_push: false              # push manually by default
  auto_pull: true
curator:
  threshold_pending: 3          # spawn Curator A when ≥ N pending
  threshold_tokens: 50000       # OR ≥ M tokens accumulated
  a_noteworthy_tier: middle     # middle (default) | simple (cheap, higher false-neg)
  curator_c:
    enabled: false              # experimental weekly defrag — off for v1
    mode: local
models:
  simple: claude-haiku-4-5
  middle: claude-sonnet-4-6
  high:   claude-opus-4-7       # or 'off' — degrades Curator B/C abstract to middle
briefing:
  auto: true
  audience: personal
  sinks:
    - markdown:~/lore-briefing.md
breadcrumb:
  mode: normal                  # quiet | normal | verbose
  scope_filter: true
```

All fields default to sane values — start without a `.lore-wiki.yml`
and add knobs only as you need them.

### Roadmap & implementation notes

- [`docs/superpowers/specs/2026-04-19-passive-capture-v1-design.md`](docs/superpowers/specs/2026-04-19-passive-capture-v1-design.md) —
  architecture spec (all 5 plans).
- [`docs/superpowers/HANDOVER-2026-04-19.md`](docs/superpowers/HANDOVER-2026-04-19.md) —
  roadmap status, gotchas, how to resume Plans 3–5.
- [`docs/superpowers/plans/`](docs/superpowers/plans/) — executed plans
  (1 + 2) for reference when writing 3–5.

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

Full design: [`docs/superpowers/specs/2026-04-20-auto-session-diagnostics-design.md`](docs/superpowers/specs/2026-04-20-auto-session-diagnostics-design.md).

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

Then `/lore:init` to write the root CLAUDE.md and you're set.

### 2. Single-wiki — one team's knowledge only

You just want the team vault and its skills:

```
mkdir -p ~/lore/wiki
ln -s ~/git/myorg/team-knowledge ~/lore/wiki/team
```

All `/lore:*` commands work with a single mount; no routing prompts.

## Scheduling the curator — cost-free defaults

The curator (flags stale notes, detects superseded decisions, keeps
`_index.md` fresh) can run several ways. The README picks no default for
you; pick your trade-off:

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
