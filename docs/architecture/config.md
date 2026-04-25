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

#### Curator backend selection

| Var | Type | Default | Read in | Wins over |
|-----|------|---------|---------|-----------|
| `LORE_LLM_BACKEND` | `auto` \| `subscription` \| `api` \| `openai` | `auto` | `lore_curator/llm_client.py:make_llm_client` | `.lore/config.yml:curator.backend` |
| `LORE_NOTEWORTHY_MODE` | `cascade` \| `llm_only` | `cascade` | `lore_curator/noteworthy.py:_resolve_mode` | `.lore/config.yml:curator.noteworthy_mode` |
| `LORE_CURATOR_MODE` | `local` \| `central` | `local` | `lore_curator/curator_c.py` | `.lore-wiki.yml:curator.curator_c.mode` |
| `LORE_CLAUDE_TIMEOUT_S` | float seconds | `300.0` | `llm_client.py:_resolve_claude_timeout` | constructor arg |

#### OpenAI-compatible backend (when `LORE_LLM_BACKEND=openai`)

Resolution: env > `.lore/config.yml:curator.openai.*` > error.

| Var | Maps to config key |
|-----|--------------------|
| `LORE_OPENAI_BASE_URL` | `base_url` |
| `LORE_OPENAI_API_KEY` | (api key — never in config files) |
| `LORE_OPENAI_MODEL_SIMPLE` | `model_simple` |
| `LORE_OPENAI_MODEL_MIDDLE` | `model_middle` |
| `LORE_OPENAI_MODEL_HIGH`   | `model_high` |

Implemented in `lore_curator/llm_client.py:_resolve_openai_settings`.

#### Anthropic SDK (when `LORE_LLM_BACKEND=api`)

| Var | Read in |
|-----|---------|
| `ANTHROPIC_API_KEY` | `lore_curator/llm_client.py:SDKClient.__init__` |

#### Observability / runtime

| Var | Effect |
|-----|--------|
| `LORE_TRACE_LLM` | `1` enables verbose LLM I/O dump to `lore_core/run_log` |
| `LORE_LOG_NOW`, `LORE_STATUS_NOW` | Inject a fake "now" timestamp for log/status formatting tests |
| `LORE_ASCII` | `1` forces ASCII icon set in `run_render.py` (override TTY autodetect) |
| `NO_COLOR` | Standard convention; `run_render.should_use_color()` honours it |
| `LORE_HOSTS_DIR` | Override the per-host install templates dir (default: `lib/lore_cli/hosts.d/`) |
| `LORE_CACHE` | Override the search-index cache dir (default: `~/.cache/lore/`) |

#### Sinks (briefing publishing)

| Var | Effect |
|-----|--------|
| `LORE_MATRIX_HOMESERVER`, `LORE_MATRIX_USER_ID`, `LORE_MATRIX_ROOM_ID` | Matrix sink connection params |

### 3. `$LORE_ROOT/.lore/config.yml` — root config

Vault-wide policy. Schema lives in
`lib/lore_core/root_config.py:RootConfig`. Subsections:

- `observability.hook_events.{max_size_mb, keep_rotations}`
- `observability.runs.{keep, max_total_mb, keep_trace}`
- `observability.proc.keep_generations`
- `curator.backend` — `auto` | `subscription` | `api` | `openai`
- `curator.noteworthy_mode` — `cascade` | `llm_only`
- `curator.openai.{base_url, api_key_env, model_simple, model_middle, model_high}`

Loader: `load_root_config(lore_root) -> RootConfig`. Missing file →
all defaults. Unknown keys → `warnings.warn` (not fatal). Malformed
YAML → defaults + warning.

### 4. `<wiki>/.lore-wiki.yml` — per-wiki config

Per-vault-mount policy. Schema lives in
`lib/lore_core/wiki_config.py:WikiConfig`. Subsections:

- `git.{auto_commit, auto_push, auto_pull}`
- `curator.{threshold_pending, threshold_tokens, a_noteworthy_tier,
  curator_a_cooldown_s, curator_b_cooldown_s}`
- `curator.curator_c.{enabled, mode, defrag_body_writes}`
- `models.{simple, middle, high}` — Claude model IDs per tier
- `briefing.{auto, audience, sinks}`
- `heartbeat.{enabled, cooldown_s, push_context}`
- `breadcrumb.{mode, scope_filter}`

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

### 7. `lib/lore_cli/hosts.d/*.toml` — install templates

Per-host install scaffolding (Claude Code, Cursor, etc.). Read by
`lore install` to know what files to write into each host's config
location. Override the dir via `LORE_HOSTS_DIR` for testing.

### 8. Note frontmatter

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
  uses these models, this curator schedule, this briefing audience.
- **Plugin manifest** — Claude Code's contract; we don't own the
  schema.
- **Install templates** — host-specific shapes; not a "setting" but
  an installer artifact.

The config layer that *should* be unified is "env override → file
override → default" — and that already is, for every env var listed
above. The unification is a *pattern* (in `_resolve_mode`,
`_resolve_openai_settings`, `_resolve_claude_timeout`); when a new
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
