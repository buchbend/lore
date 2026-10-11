---
title: Route by folder scope, one settings file, lore setup
status: shipped
epic: https://github.com/buchbend/lore/issues/466
repos:
  - buchbend/lore
---

# PRD 0016: Route by folder scope, one settings file, lore setup

> Historical. Current decisions: ADR 0016.

> Source of truth for this epic. Tracker: [epic issue](https://github.com/buchbend/lore/issues/466).
> The epic links here; this file is not embedded in the issue body.
> Decisions: ADR [0016](../adr/0016-routing-by-folder-scope.md). Next epic:
> PRD [0017](0017-logbook-and-search-sources.md).

## Problem

- Setup takes four steps: `install.sh`, `lore init`, `lore attach` in each
  repo, and a restart. The user meets `install`, `init`, `attach`, `doctor`
  and `update` before the first search.
- `lore attach` guards a write that no longer happens. Its offer, consent,
  fingerprint and drift states existed to route session notes, which ADR 0007
  retired. Its prompts still promise session notes.
- The CLI shows 30 command groups and about 85 verbs. Commands that only
  hooks and skills call sit next to the ones a person uses.
- Settings live in six places. `lore config` reaches two of them. The
  `backend` key is asked for and stored, and no code reads it.
- A person who keeps every team repo under one folder, such as
  `~/git/ccatobs`, still attaches each repo on its own.

## Solution

- A person names a **scope** once: a folder, such as `~/git/ccatobs`. Every
  repo below the folder gets the scope's wiki, sinks, sources and tracker. A
  scope can also list git remotes, so a clone outside the folder routes too.
- A repo needs no step. An optional, committed `.lore.yml` holds exceptions,
  such as an opt-out from a sink.
- `lore setup` is the one command for first run, for adding a wiki or a
  scope, and for repair. A second run prints only check marks and writes
  nothing.
- `lore status` shows what applies in the current repo and where each value
  comes from.
- `lore --help` shows the commands a person uses. Hooks and skills keep
  their commands, hidden from the help.

## Implementation decisions

### Route

- A new `resolve_route(cwd)` replaces `resolve_scope(cwd)`. It returns the
  repo, the wiki, the scope name, the tracker name, the sink names, the
  source names and an origin for each value.
- Resolution order:
  1. A flag or an environment variable.
  2. The nearest `.lore.yml` between the working directory and the repo root.
  3. The scope with the longest matching folder.
  4. A scope whose `remote` list matches the repo's git remote.
  5. The person's defaults, then the code defaults.
- A path match wins over a remote match. Two scopes that claim one repo by
  remote give no scope, and `lore status` names the conflict.
- The route is cached per process, keyed on the repo root and the
  modification times of the config files. Every prompt hook resolves it.
- Every current reader of `resolve_scope` moves to the route: the hooks, the
  SessionStart banner, capture, the context pack, transcript sync, the MCP
  default wiki and style resolution.

### Settings

- One person-level file, `~/.config/lore/config.yml`. It absorbs the root
  config under `$LORE_ROOT/.lore/config.yml`, `attachments.json` and
  `scopes.json`.
- The wiki file `.lore-wiki.yml` stays. It holds settings a wiki's team
  shares.
- The secrets file `~/.config/lore/secrets.yml` holds secret values. Lore
  creates it with mode `0600`. Config files name a secret and hold no value.
- This epic defines the scope keys `logbook` and `sources` and the top-level
  sections `sinks` and `sources`. PRD 0017 gives them behaviour.

```yaml
# ~/.config/lore/config.yml
lore_root: ~/lore
defaults: {tracker: auto}
scopes:
  ccatobs:
    path: ~/git/ccatobs
    remote: [github.com/ccatobs/*]   # optional
    wiki: ccat
    logbook: [file, pm-ccat]          # names; PRD 0017
    sources: [ccatwiki]               # names; PRD 0017
sinks: {}                             # definitions; PRD 0017
sources: {}                           # definitions; PRD 0017
user: {display_name: Christof Buchbender}
```

```yaml
# <repo>/.lore.yml   (optional, committed, exceptions only)
logbook: [file]        # opt this repo out of the team sink
```

- The wiki registry stays the vault's `wiki/` directory. No new registry file.
- A wiki note's `scope:` frontmatter stays as a tag. Nothing routes on it.

### CLI

- Visible commands after this epic: `setup`, `status`, `config`, `search`,
  `read`, `wiki`, `update`, `uninstall`. PRD 0017 adds `log`. The folder
  tracker seed adds `issue`.
- `read` takes over `drill`. `wiki` takes over `wiki new`, `lint` and
  `curator`, the last as `wiki curate`.
- Hidden, same path as today: `hook`, `mcp`, `workflow`, `tier`, `codemap`,
  `trace`, `transcripts`, `style`, `ingest`, `inbox`, `handover`, `on`,
  `off`, `migrate`. Skills, hooks and `plugin.json` keep calling them
  unchanged.
- Deleted in one hard cut: `install`, `init`, `attach`, `scopes`, `doctor`,
  `session`, `project`, `journal`, `migrate flag-blocks`, and the two extra
  upgrade paths. Lore has one user at this time.
- `config` gains `secret set|unset|list`. `list` prints names only.

### Setup

- `lore setup` runs four steps: wiki, agents, scope, check.
  - **Wiki:** create, clone or link a wiki.
  - **Agents:** install or refresh the Claude Code plugins, and Cursor where
    it is present.
  - **Scope:** offer the working directory's parent folder as a scope, and
    ask for the wiki, an optional remote and the sinks.
  - **Check:** run the checks that `doctor` ran.
- `lore setup wiki|scope|sink|source` runs one step. PRD 0017 adds the
  `sink` and `source` kinds.
- Repair is the same command. It migrates `attachments.json`, `scopes.json`
  and the root config into the person's file, then deletes the old files.
- Each prompt has a flag, so `lore setup --yes` with flags runs without a
  terminal.
- `install.sh` runs `lore setup` after it installs the package.

## Testing decisions

- Test the route as a pure function over a config and a temporary folder tree.
  One case for each resolution step, the path-over-remote rule and the
  conflict case.
- Test the reporting boundary: a `.lore.yml` that names an undefined sink
  gives the `file` sink. ADR 0016 promotes this test to an invariant.
- Test the secrets rule: the file is `0600`, a secrets file inside a git
  worktree is refused, and a `token` or `password` key in a config file fails
  validation.
- Test that a second `lore setup` run writes no file.
- Test the migration on a fixture vault with `attachments.json`,
  `scopes.json` and a root config.
- Test that `lore --help` lists the visible commands and no hidden one.
- Test that no user-facing string says "session note", "composed note" or
  "## Lore block".
- Prior art: `tests/test_scope_resolver.py`, `tests/test_attachments_file.py`,
  `tests/test_init_cmd.py`, `tests/test_doctor_fix.py`,
  `tests/test_config_schema.py`, `tests/test_status_cmd.py`.

## Out of scope

- Logbook entries, sinks and search sources: PRD 0017.
- The folder tracker, `lore issue` and the hosting setting: the folder
  tracker seed issue.
- Moving hidden commands to new paths.
- Any sync of config between machines (ADR 0009).
