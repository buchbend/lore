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
Session with AI  →  /lore:session  →  extracted concept / decision note
                                   →  briefing published to team sink
                                   →  surfaces again next session, scoped
                                      to the repo you're in
```

The flagship is the **session-note pipeline**. Everything else (search,
MCP, curator) serves it.

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

Three commands. Works on Linux + macOS in v1 (Windows tracked as a
known gap).

```bash
pipx install git+https://github.com/buchbend/lore.git   # the Python CLI
lore install                                            # detect installed hosts, wire each
lore init                                               # scaffold a vault + set $LORE_ROOT
```

> **Note:** the bare `pipx install lore` form will *not* work — the
> name `lore` is squatted on PyPI by an unrelated package. Use the
> `git+https://...` form above. We'll switch to a clean PyPI name
> once one is picked (tracked in an issue).

`lore install` walks every detected host (Claude Code, Cursor in v1)
and shows what it'll change before doing anything. One prompt per
host; `--yes` for non-interactive use. The hooks, MCP server, skills,
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
pipx install git+https://github.com/buchbend/lore.git   # install the new CLI
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
`pipx install git+https://github.com/buchbend/lore.git` separately,
or use `lore install --host claude` once `lore` is on your PATH
(it'll subprocess `claude plugin install lore@lore` for you).

### Dev install (editable, also the offline / air-gapped path)

See [CONTRIBUTING.md](./CONTRIBUTING.md) for the editable-from-checkout
recipe. Same recipe is the path for installs on machines without
network egress to PyPI / the marketplace.

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
