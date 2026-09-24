# Lore Configuration Map

**Audience:** contributors who need to know "where does setting X come
from, and what wins if it's set in two places?"

This document is the canonical map of every place Lore reads
configuration from. If you find behaviour that doesn't match what's
written here, the doc is wrong — fix it.

---

## TL;DR — precedence (highest wins)

For any single setting, the resolution order is:

1. **CLI flag** — explicit `--flag` on a `lore` invocation
2. **Environment variable** — `LORE_*` env (one process, one
   override)
3. **Per-wiki config** — `<wiki>/.lore-wiki.yml` (per-vault-mount
   policy)
4. **Root config** — `$LORE_ROOT/.lore/config.yml` (per-vault
   policy)
5. **Code default** — dataclass field default in `root_config.py` /
   `wiki_config.py`

Note settings (frontmatter inside individual `.md` files) are a
separate axis — they govern how a *single note* is interpreted, not
how Lore is configured globally.

---

## Reading & writing config — `lore config`

Config is both readable and writable from the CLI; every write is validated
against the typed schema (`root_config.py` / `wiki_config.py`) **before** it
touches disk. This surface adds *mutation* only — the precedence/resolution
above is unchanged.

- `lore config show` — print the fully resolved config with provenance (which
  layer each value came from).
- `lore config get <key>` — read one resolved value.
- `lore config set <key> <value>` — set a value in the target config file.
- `lore config unset <key>` — remove a key (reverts to the next-lower layer /
  code default).
- `lore config edit` — open the target file in `$EDITOR`; validated on close, an
  invalid result is refused (re-edit or abort), so a typo can't silently persist.
- `lore config schema` — list the settable keys and their expected types.

All accept `--wiki <name>` to target that wiki's `<wiki>/.lore-wiki.yml` instead
of the vault-root `$LORE_ROOT/.lore/config.yml`. On a rejected write the file on
disk is left unchanged: an unknown key is rejected naming the nearest valid keys,
and an invalid value is rejected naming the expected type/choices.

---

## Sources of truth

### 1. Versioning triple

| File | Role | Authority |
|------|------|-----------|
| `pyproject.toml:project.version` | Python package version (pip / pipx) | **canonical** |
| `.claude-plugin/plugin.json:version` | Claude Code plugin re-fetch token | must equal pyproject |
| `CHANGELOG.md` latest `## [X.Y.Z]` | Release log | must equal pyproject |

`tests/test_version_sync.py` enforces all three. See `CONTRIBUTING.md`
"Releasing a new version" for the bump procedure.

### 2. Environment variables

#### Vault location

| Var | Type | Read in | Resolved by |
|-----|------|---------|-------------|
| `LORE_ROOT` | path (default: `~/lore`) | many CLI commands + `lore_core/scope_resolver.py` | `lore_core.config.get_lore_root()` |

`get_lore_root()` returns env-or-default. CLI commands that *require*
the user to have explicitly set `LORE_ROOT` should call
`require_lore_root()` instead — that one errors when env is unset
rather than silently falling back to `~/lore`.

#### Curator

| Var | Type | Default | Read in | Wins over |
|-----|------|---------|---------|-----------|
| `LORE_CURATOR_MODE` | `1` \| unset | unset | `lore_cli/hooks.py:_in_curator_mode` | (internal — set by the curator's own detached-subprocess spawns, not a user knob) |
| `LORE_SUPPRESS_CAPTURE` | `1` \| unset | unset | `lore_cli/hooks.py:_capture_suppressed`, checked first thing in `capture()` | (dispatch contract — set by an orchestrator when it launches a teammate session whose transcript should not become its own standalone note; unset leaves capture unchanged) |

#### Observability / runtime

| Var | Effect |
|-----|--------|
| `LORE_TRACE_LLM` | `1` enables verbose LLM I/O dump to `lore_core/run_log` |
| `LORE_LOG_NOW`, `LORE_STATUS_NOW` | Inject a fake "now" timestamp for log/status formatting tests |
| `LORE_ASCII` | `1` forces ASCII icon set in `run_render.py` (override TTY autodetect) |
| `NO_COLOR` | Standard convention; `run_render.should_use_color()` honours it |
| `LORE_CACHE` | Override the search-index cache dir (default: `~/.cache/lore/`) |

### 3. `$LORE_ROOT/.lore/config.yml` — root config

Vault-wide policy. Schema lives in
`lib/lore_core/root_config.py:RootConfig`. Subsections:

- `observability.hook_events.{max_size_mb, keep_rotations}`
- `observability.runs.{keep, max_total_mb, keep_trace}`
- `observability.proc.keep_generations`
- `observability.retention.{hot_days, cold_days, cold_max_mb, crash_log_days}` —
  the unified spine retention janitor: a hot tier keeps detailed events
  ~`hot_days`, a cold tier keeps compact summaries ~`cold_days` under a
  `cold_max_mb` size cap; `crash_log_days` bounds crash-log retention. See
  `docs/architecture/observability.md`.
  `dead_letter_hard_cap` was removed with the flush store. A `config.yml` that
  still sets it loads normally; the loader warns and names the key to delete.
- `journal.enabled`
- `tiers.overrides.<host>.<tier>` — override the shipped model-tier table
  (`lib/lore_core/tiers/table.py`) for one host/tier cell; see
  `docs/model-tiers.md`.

A `curator:` block is a retired key here too — the LLM-backend selector
left with the LLM client, and `load_root_config` warns by name
(`RETIRED_BLOCKS`).

Loader: `load_root_config(lore_root) -> RootConfig`. Missing file →
all defaults. Unknown keys → `warnings.warn` (not fatal). Malformed
YAML → defaults + warning.

### 4. `<wiki>/.lore-wiki.yml` — per-wiki config

Per-vault-mount policy. Schema lives in
`lib/lore_core/wiki_config.py:WikiConfig`. Subsections:

- `git.{auto_commit, auto_push, auto_pull}` — `auto_push` defaults to
  whether the wiki has a git remote; an explicit value in the file
  always wins over that default.
- `models.{simple, middle, high}` — Claude model IDs per tier
- `heartbeat.{enabled, cooldown_s, push_context}`
- `breadcrumb.{mode, scope_filter}`

A `curator:` or `briefing:` block is a retired key — `WikiConfig`
carries neither field, and `load_wiki_config` warns by name
(`RETIRED_BLOCKS`) rather than the generic unknown-key notice. The
per-session-turn-threshold knobs retired with the compose pipeline; the
briefing knobs retired with the briefing command.

Loader: `load_wiki_config(wiki_dir) -> WikiConfig`. Same fault-tolerant
behaviour as root config.

### 5. `<repo>/CLAUDE.md ## Lore` block — attachment metadata

Records the wiki/scope binding for a working directory and any GH
filter overrides. Read by hooks at SessionStart for status-line
context. Not a settings file in the configuration sense — more like
"this repo's identity card." Schema documented in
`docs/architecture/state.md`.

### 6. `.claude-plugin/plugin.json`

Claude Code plugin manifest. Hook command registration, MCP server
declaration, plugin version. Edited only as part of the release
process (see version triple above).

### 7. Note frontmatter

Per-note metadata (`type:`, `description:`, `status:`, `supersedes:`,
…). Documented in `lore_core/schema.py`. Not "config" in the global
sense.

---

## Why so many sources?

Each source has a justified role:

- **Env vars** — single-process overrides, the cheapest way to flip
  one knob without editing files.
- **Root config** (`config.yml`) — vault-wide policy that's per-user,
  not per-wiki: observability budgets, default backend.
- **Wiki config** (`.lore-wiki.yml`) — per-mount policy: this wiki
  uses these models, this curator schedule, this breadcrumb mode.
- **Plugin manifest** — Claude Code's contract; we don't own the
  schema.
- **Install templates** — integration-specific shapes; not a "setting" but
  an installer artifact.

The config layer that *should* be unified is "env override → file
override → default" — and that already is, for every env var listed
above. The unification is a *pattern* (in `_resolve_mode`); when a new
env-overridable setting is added, follow the same shape.

---

## Adding a new setting — checklist

1. Decide the layer: vault-wide (root_config) or per-wiki
   (wiki_config)?
2. Add a dataclass field in the appropriate `*_config.py` with a
   sensible default.
3. If env-overridable: add a `_resolve_<setting>` function next to
   the loader, following the env > config > default pattern.
4. Add a precedence test in `tests/test_root_config.py` or
   `tests/test_wiki_config.py`.
5. Document the new env var here and in
   `lore_core/wiki_config.py` / `root_config.py` docstrings.
