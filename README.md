# Lore

**LLM-optimized knowledge graph for AI-coding teams.** Transcripts captured
and archived, team-relevant facts filed as repo artifacts — issues, comments
and pull requests — repo-scoped context injected at session start. No vector
DB needed for small vaults; a full hybrid search + MCP server for larger ones.

> ⚠️ **Pre-1.0.** APIs, hook contracts, skill surfaces, frontmatter
> schema, and CLI flags can still change between minor versions. Not
> recommended for wikis you can't re-migrate into shape.

## The pitch

When you work with an AI agent, the decisions, reasoning, and open
threads live in a chat window that disappears. PRs capture the diff;
nothing captures *why*. Lore closes the loop:

```
Session with AI  →  transcript captured and archived, ledger entry
                    stamped with repo/branch/PRs/issues/commits/files
                 →  an agent files each team-relevant fact as the repo
                    artifact that already has a reader: an issue, a
                    comment on an issue or PR, or a pull request
                 →  the developer triages it in the tracker
```

The write path is the **filing rule**: each kind of fact gets the artifact
that owns it. Capture itself is automatic and costs no model call — see the
"Bootstrap" section below. Lore writes nothing into a wiki on its own.
Ratified decisions live in the connected repo's ADRs/PRDs, pulled on
demand via MCP — Lore does not extract decisions from transcripts.

## Two plugins: `lore` + `lore-workflow`

This repo ships **two marketplace entries**, versioned and installed
independently:

| Plugin | What it's for | Depends on |
|---|---|---|
| **`lore`** | The notes/vault system above: session capture, search, MCP. | nothing else |
| **`lore-workflow`** | An opinionated planning chain — epics, PRDs, TDD — that calls `lore`'s deterministic substrate (code map, model tiers). | `lore` |

`lore-workflow` is opt-in: install `lore` alone for the notes pipeline, or
add `lore-workflow` on top once you also want the planning chain. The
dependency only runs one way — nothing in `lore` core imports or requires
`lore-workflow`.

The chain it bundles:

```
seed-epic → orient → grilling → to-epic → orchestrate-epic → document-epic
```

with `implement-issue` as a lighter-weight track for one well-understood
issue, and `tdd` as the discipline every implementation teammate follows.
See [`docs/conventions.md`](docs/conventions.md) for the full chain, the
artifact-home contract (PRD/ADR/`AGENTS.md` placement), and the tier
vocabulary; [`docs/how-to/`](docs/how-to/) for task recipes
(run an epic, use the fast path, resume a broken epic, onboard a repo);
[`docs/explanation/`](docs/explanation/) for the
reasoning behind the design; and
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
pipx install "git+https://github.com/buchbend/lore.git#egg=lore"  # the CLI
lore install                                            # detect installed integrations, wire each
lore init                                               # scaffold a vault + set $LORE_ROOT
```

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
`pipx install "git+https://github.com/buchbend/lore.git#egg=lore"`
separately, or use `lore install --integration claude` once `lore` is on
your PATH (it'll subprocess `claude plugin install lore@lore` for you).

### Dev install (editable, also the offline / air-gapped path)

See [CONTRIBUTING.md](./CONTRIBUTING.md) for the editable-from-checkout
recipe. Same recipe is the path for installs on machines without
network egress to PyPI / the marketplace.

## Bootstrap: passive capture

Capture is automatic and needs no explicit command. Every transcript
Claude Code produces is registered into the transcript ledger and
mirrored into the wiki's `.transcripts/`, stamped with a linkage block
(repo, branch, PRs, issues, commits, files) derived from git state — no
LLM call, no prose (see `CONTEXT.md` for the full model). Capture
writes nothing into a wiki itself. Everything in a wiki — concepts,
decisions, projects, reference notes — is written directly, by hand, via
`/lore:inbox`, or through a pull request an agent opens; there is no
automatic daily abstraction pass.

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
pip install -e .
lore install
```

(Fresh installs follow [§ Install](#install) above.)

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

- **Claude Code hooks register every transcript into the ledger.**
  SessionStart, PreCompact, SessionEnd, and a mid-session
  UserPromptSubmit heartbeat all route through the same registration
  path (`lore_curator/capture_routing.py`): each upserts a ledger entry
  and stamps its linkage block, and a session boundary promotes that
  stamp to a full transcript read (edited files, commit SHAs). No LLM
  call; registration is the end of the capture path, not a step toward
  composing anything.
- **SessionStart also spawns a detached transcript sync**, mirroring
  every attached transcript into its wiki's `.transcripts/` and
  emitting the one live drain event, `transcript-synced`. An
  opportunistic, flock-guarded retention sweep runs in-process
  alongside it — short and lock-guarded, so it doesn't block
  SessionStart in practice, but only the transcript sync is actually
  detached.
- **Banner at SessionStart** is deliberately minimal: a status line, an
  optional Focus block, a last-active-day recap read off the transcript
  ledger (day, session count, repos, branches, refs — no LLM call),
  freshness lines only on positive evidence, and
  a fixed directive pointing at MCP pull for anything deeper. `lore!:`
  prefix flags actionable errors.

### Manual escape hatches

- `lore ingest --from <file.jsonl> --integration cursor --directory <cwd>` —
  ingest a transcript from any integration lore doesn't auto-capture.
- `lore curator [--wiki <name>] [--apply]` — the frontmatter-only
  hygiene pass (supersession, `implements:` back-links, git-date
  backfill, team-mode hint); dry-run by default.
- `lore scopes wikis` / `lore scopes doctor` —
  list configured wikis and validate them. (For looking up the
  attachment covering a specific path, use `lore attach attachments show
  <path>`.)
- `lore migrate flag-blocks [--apply]` — remove the blocks the retired
  flag crossing left in wiki notes. Dry-run by default.

### Per-wiki configuration

Each wiki can set its own knobs in `<wiki>/.lore-wiki.yml`:

```yaml
git:
  auto_push: true                # true by default when the wiki has a remote
  auto_pull: true
models:
  simple: claude-haiku-4-5
  middle: claude-sonnet-4-6
  high:   claude-opus-4-7
breadcrumb:
  mode: normal                  # quiet | normal | verbose
  scope_filter: true
```

All fields default to sane values — start without a `.lore-wiki.yml`
and add knobs only as you need them.

## Observability

Every background producer (hooks, the hygiene curator, transcript sync,
the retention janitor) writes one envelope onto one append-only
event log, the **spine**. Each envelope carries a `trace_id` field for
correlating several records into one story.
Three commands cover the common scenarios:

| Scenario | Command |
|---|---|
| **"Is Lore healthy right now?"** | **`lore status`** |
| "I had a session and nothing was captured" | `lore status` / `lore doctor` |
| "Hook plumbing feels off" | `lore doctor` (`--fix` repairs what it can) |

`lore status` is the first thing to run when you're wondering whether Lore is
alive: capture liveness, per-wiki connection health, retention
usage, and an alerts section where every warning names its own drill-down
command.

`lore trace <selector>` renders the chronological, correlated story of one
unit of work for a trace_id, a session_id, or a note path /
`[[wikilink]]`.

`lore log` / `lore news` / `lore runs` / `lore proc` have been removed —
their debugging role is fully absorbed by `lore trace` / `lore status`
above (see `CHANGELOG.md` for the removal).

Structured logs live under `$LORE_ROOT/.lore/spine.jsonl`, tiered
hot → cold → deleted; configure retention at `$LORE_ROOT/.lore/config.yml`:

~~~yaml
observability:
  retention:
    hot_days: 7
    cold_days: 30
~~~

Full envelope schema, producer list, trace_id lifecycle, and retention
tiers: [`docs/architecture/observability.md`](docs/architecture/observability.md).
Onboarding and troubleshooting walkthroughs:
[`docs/how-to/onboarding.md`](docs/how-to/onboarding.md),
[`docs/how-to/troubleshooting.md`](docs/how-to/troubleshooting.md).

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
wiki) and add `schema_version: 2` to existing notes:

```
LORE_ROOT=/path/to/your/vault lore migrate frontmatter --add-schema-version
# review the dry-run diff, then:
LORE_ROOT=/path/to/your/vault lore migrate frontmatter --add-schema-version --apply
```

No files move. If your vault does not yet match the canonical shape,
`lore init` scaffolds it without touching your notes.

## Design principles

- **Markdown + git stay authoritative.** No database the vault can't be
  rebuilt from.
- **Cheap context is automatic; expensive context is explicit.** Inject
  bounded, deterministic context at SessionStart and PreCompact (reading
  cached files the linter regenerates). Lore itself calls no model: the
  agent in the session does the judging.
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
