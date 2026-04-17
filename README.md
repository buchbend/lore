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

One command, coexists with anything you already have:

```bash
git clone https://github.com/buchbend/lore.git
cd lore && ./install.sh --with-hooks
```

What the installer does:

1. Installs the `lore` CLI (via `pipx`, `uv tool`, or `pip --user`, in
   that order of preference)
2. Symlinks every skill into `~/.claude/skills/lore:*` — Claude Code
   picks them up automatically. Existing skills are left alone.
3. If you passed `--with-hooks`: merges SessionStart / PreCompact / Stop
   entries into `~/.claude/settings.json` (idempotent — re-running is
   a no-op).

Uninstall / disable hooks is just the inverse: delete the symlinks in
`~/.claude/skills/lore:*` and the `hooks` entries in `settings.json`.

### As a Claude Code plugin (via marketplace)

The repo is a self-describing marketplace. Once published, you can do:

```
/plugin marketplace add buchbend/lore
/plugin install lore@lore
```

This gives you the skills but not the Python CLI or hooks — the
`install.sh` path above remains the most complete install.

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
