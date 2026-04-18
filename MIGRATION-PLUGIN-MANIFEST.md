# Migration: install.sh → Claude Code plugin manifest

## Why

Today's `install.sh` (~340 lines of bash) does work the modern Claude
Code plugin system handles declaratively. The Python CLI install is
the only piece that legitimately needs out-of-band tooling
(`pipx install lore`); everything else — skills, subagents, hooks, MCP
server — can be auto-wired by the plugin manifest, and the rest
(pre-allows, LORE_ROOT) can be documented or handled by `lore init`.

Result: a standard install story (3 commands), an opaque-bash-script
dependency removed, and the plugin shape that other host adapters
(OpenCode, Cursor) will need to learn from anyway.

## Confirmed plugin-manifest capabilities

(See https://code.claude.com/docs/en/plugins-reference.md)

| Today (install.sh) | After migration |
|---|---|
| Symlink `skills/lore:*` → `~/.claude/skills/` | **Auto-discovered** from `skills/<name>/SKILL.md` |
| Symlink `agents/*.md` → `~/.claude/agents/` | **Auto-discovered** from `agents/<name>.md` |
| Merge `SessionStart` + `PreCompact` into `~/.claude/settings.json` | Declared in `.claude-plugin/plugin.json` `hooks` block; merged automatically |
| (not done today — manual) | MCP server declared in `plugin.json` `mcpServers` block; `${CLAUDE_PLUGIN_ROOT}` substitution available |
| Mutate `permissions.allow` for `Bash(lore *)` + `Read(<vault>/**)` | **Not declarable** — must document in README or accept early prompts |
| Resolve + persist `LORE_ROOT` in `settings.json` env block | Move to `lore init` (interactive) — writes `~/.config/lore/config.toml` or `.envrc` snippet |
| `pipx install --editable .` | Stays — `pipx install lore` from PyPI for users; editable for devs |

## Target install story

```
pipx install lore                               # 1. Python CLI from PyPI
claude plugin marketplace add buchbend/lore     # 2. Trust the marketplace
claude plugin install lore@lore                 # 3. Install the plugin
lore init                                       # 4. (one-time) scaffold vault + set LORE_ROOT
```

Optionally a fifth step (or noted in README) for users who hate
permission prompts:

```
# Add to ~/.claude/settings.json under "permissions.allow":
"Bash(lore *)", "Read(<your vault>/**)"
```

## Concrete file changes

### `.claude-plugin/plugin.json` — add hooks + mcpServers

Currently metadata-only. Becomes:

```json
{
  "name": "lore",
  "description": "...",
  "version": "0.1.0",
  "author": {"name": "Christof Buchbender"},
  "homepage": "https://github.com/buchbend/lore",
  "license": "MIT",
  "keywords": ["knowledge-graph", "obsidian", "mcp", "session-notes", "rag"],
  "hooks": {
    "SessionStart": [
      {"hooks": [{"type": "command", "command": "lore hook session-start"}]}
    ],
    "PreCompact": [
      {"hooks": [{"type": "command", "command": "lore hook pre-compact"}]}
    ]
  },
  "mcpServers": {
    "lore": {
      "command": "lore",
      "args": ["mcp"]
    }
  }
}
```

Skills (`skills/lore:*/SKILL.md`) and subagents (`agents/*.md`) are
auto-discovered — no entries needed.

### `.claude-plugin/marketplace.json` — already correct

Already declares `lore@lore` from `source: "."`. No change.

### `README.md` — rewrite the install section

Three-command install, documented pre-allows, link to dev-install
section for contributors.

### `lib/lore_cli/init_cmd.py` — add LORE_ROOT setup

Today `lore init` scaffolds a vault. Extend it to:
- Detect/prompt for vault location (default `~/lore`)
- Write `LORE_ROOT=<path>` to `~/.config/lore/config.toml` (read by
  `lore_core.config.get_lore_root()`)
- Optionally also append to the user's shell rc with permission

The hook commands (`lore hook session-start`, etc.) already call
`get_lore_root()`, so once `lore init` writes the config file the
hook resolves correctly without `~/.claude/settings.json` env entries.

### `lore_core/config.py` — add config-file fallback

```python
def get_lore_root() -> Path:
    env = os.environ.get("LORE_ROOT")
    if env:
        return Path(env).expanduser()
    config = Path.home() / ".config" / "lore" / "config.toml"
    if config.exists():
        data = tomllib.loads(config.read_text())
        if "lore_root" in data:
            return Path(data["lore_root"]).expanduser()
    return Path.home() / "lore"  # current default
```

This removes the need for `~/.claude/settings.json` env mutation. The
config file is portable across hosts (Claude Code, OpenCode, etc.)
which matches the host-agnostic thesis.

### `install.sh` — slim or delete

**Option A — delete it.** README documents the three commands.

**Option B — keep ~30 lines as a dev-mode helper:**

```bash
#!/usr/bin/env bash
# Dev install — editable CLI + local plugin marketplace
set -e
REPO="$(cd "$(dirname "$0")" && pwd)"
pipx install --force --editable "$REPO"
echo
echo "Now register the local plugin source in Claude Code:"
echo "  claude plugin marketplace add file://$REPO/.claude-plugin/marketplace.json"
echo "  claude plugin install lore@lore"
echo
echo "Then run: lore init"
```

Recommend Option B — keeps the contributor story one command without
forcing PyPI for dev iteration.

### Add `docs/install.md` (optional)

Long-form install guide covering: pipx vs uv vs pip, troubleshooting
"`lore` not on PATH" (`pipx ensurepath`), how to verify hooks fired
(`lore doctor`), how to opt out of pre-allows.

## Phases

**Phase A — Additive plugin manifest.** Add `hooks` + `mcpServers` to
`plugin.json`. Doesn't break existing install.sh users (their
settings.json hooks still work; the plugin's would be a no-op
duplicate, which Claude Code dedupes by command string IIRC — verify).

**Phase B — Config-file LORE_ROOT.** Extend `lore init` to write
`~/.config/lore/config.toml`; add the config-file fallback to
`lore_core.config.get_lore_root()`. New tests for the resolver order
(env → config-file → default).

**Phase C — README rewrite.** New install section, mark install.sh as
"dev install" with a sentence explaining when to use it.

**Phase D — End-to-end test.** On a clean container or fresh `$HOME`:
run the three-command install, start a Claude Code session, confirm
SessionStart fires, confirm `/lore:loaded`, `/lore:resume`, MCP tools
all work.

**Phase E — install.sh slimdown or removal.** Once Phase D passes,
choose Option A or B. If B, the script is the editable `pipx install`
+ a printed reminder; nothing else.

**Phase F — Doctor extension.** Add a check to `lore doctor` that
walks the plugin install (is `lore` on PATH? are the bundled hooks
present in `settings.json` from the plugin? does `claude plugin list`
show `lore@lore`?) so users can self-diagnose plugin-vs-PATH issues.

## Tradeoffs / risks

1. **Plugin auto-discovery untested on this version.** The docs say
   skills + agents auto-discover; I have not exercised it. Phase D
   verifies. Falsifies the whole migration if it doesn't work — fall
   back to declaring entries explicitly in plugin.json.
2. **`lore` on PATH is a hard dep for hooks.** If a user runs the
   `claude plugin install` step but skips `pipx install lore`, hooks
   fail silently. Mitigations: (a) document the order, (b) the
   plugin's first SessionStart could write a stderr line "lore CLI
   not found; run `pipx install lore`" — small Python wrapper around
   `lore hook session-start` that checks first.
3. **No declarative pre-allows.** Until Claude Code supports plugin
   permission declarations, every fresh install gets one prompt per
   `Bash(lore *)` until the user clicks "Don't ask again" or edits
   settings. Acceptable; document in README.
4. **Existing install.sh users.** Their settings.json has hook
   entries from the bash script. Plugin install adds duplicates. If
   commands are identical strings, Claude Code dedupes; verify and
   add a one-time migration note to the changelog.
5. **`lore init` ambiguity.** Today it scaffolds a vault. Adding
   "configure LORE_ROOT" overloads it. Either add `lore init
   --vault` and `lore init --config`, or have the single command
   handle both interactively. Recommend the latter — fewer verbs.

## What changes for the maintainer's day-to-day

Nothing, until Phase E lands. Lore on this machine is editable + the
old install.sh's settings entries continue to work. After Phase E,
the dev-loop is:

```
cd ~/git/lore && git pull          # Python is editable, picked up live
# If the plugin manifest changed:
claude plugin reinstall lore@lore  # or: claude plugin update
```

## Multi-host install architecture

Claude Code is the first integration, not the only one. Cursor, Codex,
Gemini, OpenCode, and successors all have different plugin shapes and
different feature surfaces. The installer story should reflect that
reality without forking into N separate scripts.

### What each host can express today

| Host | MCP servers | Hooks | Skills | Subagents | Rules / sys-prompt |
|---|---|---|---|---|---|
| Claude Code | ✅ plugin or `~/.claude/.mcp.json` | ✅ plugin | ✅ plugin auto-discover | ✅ plugin auto-discover | (CLAUDE.md) |
| Cursor | ✅ `~/.cursor/mcp.json` | ❌ | ❌ (commands only) | partial | ✅ `.cursorrules` / project rules |
| Codex CLI | ✅ via config | ❌ | ❌ | ❌ | ✅ system-prompt file |
| Gemini Code Assist | ✅ (emerging) | ❌ | ❌ | ❌ | ✅ instructions config |
| OpenCode | ✅ | ✅ | ✅ | ✅ | ✅ |

Lore's surface degrades gracefully across hosts. Claude Code gets the
full plugin (skills, subagents, hooks, MCP); the others get
MCP + a rules-file dropping the vault-first directive +
optional command aliases.

### The unified pattern: extend `hosts.d/*.toml`

We already have `lib/lore_cli/hosts.d/<host>.toml` for the launcher
(Phase 4.2). Generalise that descriptor so each host TOML declares
**both** how to launch *and* how to install the Lore integration into
that host. One file per host, drop-in extensible.

Sketch — `hosts.d/claude.toml`:

```toml
# Launcher (existing)
binary = "claude"
context_format = "flag"
context_flag = "--append-system-prompt"
extra_args = []

# Install — Claude Code uses its plugin system
[install]
strategy = "claude-plugin"           # the plugin manifest path
plugin_manifest = ".claude-plugin/plugin.json"
# All Lore surfaces flow through the manifest; nothing extra to drop.
```

Sketch — `hosts.d/cursor.toml`:

```toml
binary = "cursor"
context_format = "stdin"
context_flag = ""
extra_args = []

[install]
strategy = "drop-in"
mcp_config_path = "~/.cursor/mcp.json"
mcp_server_name = "lore"
mcp_command = "lore"
mcp_args = ["mcp"]
rules_file = "~/.cursor/rules/lore.md"   # written from templates/host-rules.md
```

Sketch — `hosts.d/codex.toml`:

```toml
binary = "codex"
context_format = "append"
extra_args = []

[install]
strategy = "drop-in"
mcp_config_path = "~/.config/codex/mcp.json"
mcp_server_name = "lore"
mcp_command = "lore"
mcp_args = ["mcp"]
system_prompt_addendum = "~/.config/codex/system-prompt.d/lore.md"
```

### `lore install [--host <name>|all]`

One CLI subcommand, multiple host adapters. Lives in
`lib/lore_cli/install_cmd.py` and reads the TOMLs:

```
lore install              # interactive — detect installed hosts, ask which
lore install --host all   # install for every host TOML where binary is on PATH
lore install --host cursor
lore install --check      # report what would change, do not write
lore install --uninstall --host cursor
```

Per host, the dispatcher knows three install strategies:

- **`claude-plugin`** — the manifest is already in the repo; the
  installer either points the user at `claude plugin install lore@lore`
  or does it for them via `claude plugin install` subprocess if
  available. (Lore-side it's just a printed instruction, no file
  write.)
- **`drop-in`** — write the right snippet into the right config
  file. Idempotent JSON/TOML merge per host (the same atomic-merge
  logic used for `~/.claude/settings.json` today, generalised).
- **`pypi-only`** — host has no integration; only the Python CLI is
  needed. (Useful for shell-only users.)

A new module `lib/lore_core/install/` would hold the per-strategy
implementations:

```
lib/lore_core/install/
    __init__.py
    base.py            # InstallStrategy protocol
    claude_plugin.py   # subprocess `claude plugin ...` or print instructions
    drop_in.py         # JSON/TOML merge per host config
    rules_file.py      # write the vault-first directive as a host-rules file
```

The same `lore install --uninstall` reverses each strategy: removes
MCP config block, deletes the rules file, etc. Better than today's
install.sh which has no uninstall path.

### `templates/host-rules.md`

The vault-first directive (currently injected by the SessionStart
hook + documented in `templates/wiki-CLAUDE.md`) gets a
host-rules.md sibling that's the canonical "what should the agent do
on a fresh session" content for hosts without a hook surface. The
installer drops it into `~/.cursor/rules/lore.md`,
`~/.config/codex/system-prompt.d/lore.md`, etc.

This deduplicates the directive — one source of truth, projected per
host by the installer.

### Revised target install story

```
pipx install lore                    # 1. Python CLI from PyPI (universal)
lore install                         # 2. Detect hosts, install per host
lore init                            # 3. Scaffold vault + set LORE_ROOT
```

For Claude Code users, `lore install` either runs `claude plugin
install` for them or prints the one-liner. For Cursor/Codex/Gemini,
it writes the MCP config + rules file directly. Single entry point
across all hosts.

### Revised phases

- **Phase A — Plugin manifest for Claude Code** (as before): hooks +
  mcpServers in `.claude-plugin/plugin.json`.
- **Phase B — Config-file LORE_ROOT** (as before).
- **Phase C — Generalised host TOML schema**: extend `hosts.d/*.toml`
  with `[install]` blocks. `claude.toml` gets `strategy =
  "claude-plugin"`; ship `cursor.toml`, `codex.toml`, `gemini.toml`
  as `drop-in`.
- **Phase D — `lore install` subcommand**: dispatcher + the three
  strategy modules + `--check` / `--uninstall` flags.
- **Phase E — `templates/host-rules.md`**: one-source-of-truth for
  the vault-first directive across rule-only hosts.
- **Phase F — README rewrite**: three-command universal install
  story, per-host details in `docs/install.md`.
- **Phase G — install.sh slimdown**: shrink to dev-mode only
  (editable pipx + register the local plugin marketplace) or delete
  if `lore install --dev` covers the same ground.
- **Phase H — End-to-end test**: clean container with each host
  installed; run `lore install --host all`; confirm each host sees
  Lore appropriately.

### Tradeoffs of the multi-host approach

- **Surface area grows.** N host adapters, N config formats to track
  as the hosts evolve. Mitigation: strict per-host integration tests
  (`tests/test_install_<host>.py`); minimal feature set per host
  (MCP + rules) to keep the matrix small.
- **Each host's config file is the host vendor's contract — they may
  break it.** Drop-in installers will need maintenance when Cursor
  changes `mcp.json` shape, etc. Mitigation: TOML descriptors mean
  the fix is one file per host, not code refactor.
- **`claude-plugin` strategy still needs the manifest in the repo.**
  So Phase A is a hard prerequisite for `lore install --host claude`
  to do anything useful.
- **Claude Code is over-served, others under-served (intentionally).**
  Cursor/Codex don't get skills/subagents/hooks because their host
  doesn't support those concepts. Lore degrades to MCP + rules.
  That's the right shape — don't try to backport Claude-only features
  via brittle workarounds.

## Open questions for the user

- Pin `pipx install lore` to PyPI v0.1.0, or stay editable-via-pipx
  for now and revisit when there's a release?
- Move `LORE_ROOT` to `~/.config/lore/config.toml` (recommended) or
  keep it in `~/.claude/settings.json` env (works only for Claude
  Code; not host-agnostic)?
- `install.sh`: Option A (delete) or Option B (slim dev helper)?
- Worth the SessionStart "doctor" wrapper that detects missing
  `lore` binary, or trust users to run `pipx install` first?
- Multi-host scope for v1: ship `cursor.toml` + `codex.toml` +
  `gemini.toml` from day one (placeholder MCP + rules), or
  Claude-only with the architecture in place and other hosts added
  as users ask?
- Single `lore install` (interactive, detects hosts) or per-host
  subcommands (`lore install claude`, `lore install cursor`)?
  Recommend single + `--host` flag for scriptability.
