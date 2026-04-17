#!/usr/bin/env bash
# Lore — one-shot installer.
#
# What this does:
#   1. Installs the `lore` CLI via pipx (preferred) or pip (fallback)
#   2. Symlinks every skill into ~/.claude/skills/lore:*
#   3. Offers to merge the SessionStart / PreCompact / Stop hooks into
#      ~/.claude/settings.json (opt-in, with a clear preview of the diff)
#   4. Prints next steps (set LORE_ROOT, run `lore init`, etc.)
#
# Safe to re-run (idempotent). Existing vault:* skills stay untouched —
# lore:* coexists with them.
#
# Usage:
#   cd ~/git/lore && ./install.sh [--with-hooks] [--skip-pipx]

set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SKILLS_DIR="${HOME}/.claude/skills"
SETTINGS_FILE="${HOME}/.claude/settings.json"

WITH_HOOKS=0
SKIP_PIPX=0
LORE_ROOT_ARG=""
for arg in "$@"; do
    case "$arg" in
        --with-hooks) WITH_HOOKS=1 ;;
        --skip-pipx)  SKIP_PIPX=1 ;;
        --lore-root=*) LORE_ROOT_ARG="${arg#--lore-root=}" ;;
        -h|--help)
            sed -n '2,20p' "$0"
            exit 0
            ;;
    esac
done

say() { printf '\033[1;36m==>\033[0m %s\n' "$*"; }
ok()  { printf '\033[1;32m ok\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m !!\033[0m %s\n' "$*"; }

# ----------------------------------------------------------------------
# 1. Install the Python CLI
# ----------------------------------------------------------------------

say "Installing the lore CLI"

if [[ "$SKIP_PIPX" -eq 0 ]] && command -v pipx >/dev/null 2>&1; then
    pipx install --force --editable "$REPO_DIR"
    ok "Installed via pipx (editable)"
elif command -v uv >/dev/null 2>&1; then
    # uv tool install works like pipx for Python CLIs
    uv tool install --force --editable "$REPO_DIR"
    ok "Installed via uv tool (editable)"
elif command -v pip >/dev/null 2>&1; then
    pip install --user --editable "$REPO_DIR"
    ok "Installed via pip --user (editable)"
else
    warn "No pipx / uv / pip found. Install one, then re-run."
    exit 1
fi

if ! command -v lore >/dev/null 2>&1; then
    warn "Installed, but 'lore' is not on PATH yet."
    warn "Try: pipx ensurepath; and reopen your shell. Then re-run."
    # Continue — skills and hooks still useful
fi

# ----------------------------------------------------------------------
# 2. Symlink skills
# ----------------------------------------------------------------------

say "Linking skills into $SKILLS_DIR"

mkdir -p "$SKILLS_DIR"
linked=0
for skill_dir in "$REPO_DIR"/skills/lore:*/; do
    name="$(basename "$skill_dir")"
    target="$SKILLS_DIR/$name"
    if [[ -L "$target" ]]; then
        current="$(readlink "$target")"
        if [[ "$current" == "$skill_dir" || "$current" == "${skill_dir%/}" ]]; then
            continue
        fi
        rm "$target"
    elif [[ -e "$target" ]]; then
        warn "$target exists and is not a symlink; skipping"
        continue
    fi
    ln -s "$skill_dir" "$target"
    linked=$((linked + 1))
done
ok "Linked $linked skill(s)"
echo "   (existing vault:* skills left untouched — lore:* coexists)"

# ----------------------------------------------------------------------
# 3a. Auto-allow `Bash(lore:*)` so Lore commands don't trigger permission prompts
# ----------------------------------------------------------------------

say "Permissions"

# The cache-read permission is a well-known path; vault-read is filled
# in after LORE_ROOT is resolved (step 3b below) since it depends on
# where the user's vault lives.
python3 - "$SETTINGS_FILE" "$HOME" <<'PYEOF'
import json, sys
from pathlib import Path
path = Path(sys.argv[1])
home = sys.argv[2]
path.parent.mkdir(parents=True, exist_ok=True)
cfg = json.loads(path.read_text()) if path.exists() else {}
permissions = cfg.setdefault("permissions", {})
allow = permissions.setdefault("allow", [])

rules = [
    "Bash(lore:*)",
    "Bash(lore *)",
    f"Read({home}/.cache/lore/**)",
]
added = [r for r in rules if r not in allow]
allow.extend(added)
if added:
    path.write_text(json.dumps(cfg, indent=2) + "\n")
    print(f"  allowed: {', '.join(added)}")
else:
    print("  lore permissions already in allowlist")
PYEOF

# ----------------------------------------------------------------------
# 3b. Resolve and persist LORE_ROOT in settings.json env block
# ----------------------------------------------------------------------

say "Setting LORE_ROOT"

resolved_root=""
if [[ -n "$LORE_ROOT_ARG" ]]; then
    resolved_root="$LORE_ROOT_ARG"
elif [[ -n "${LORE_ROOT:-}" ]]; then
    resolved_root="$LORE_ROOT"
elif [[ -d "$HOME/lore/wiki" ]]; then
    resolved_root="$HOME/lore"
elif [[ -d "$HOME/git/vault/wiki" ]]; then
    resolved_root="$HOME/git/vault"
fi

if [[ -z "$resolved_root" ]]; then
    warn "No existing vault detected. Either:"
    warn "  • run \`lore init\` to create one at ~/lore, then re-run this installer"
    warn "  • re-run with --lore-root=/path/to/your/vault"
else
    python3 - "$SETTINGS_FILE" "$resolved_root" <<'PYEOF'
import json, sys
from pathlib import Path
path, root = Path(sys.argv[1]), sys.argv[2]
path.parent.mkdir(parents=True, exist_ok=True)
cfg = json.loads(path.read_text()) if path.exists() else {}

env_changed = False
env = cfg.setdefault("env", {})
if env.get("LORE_ROOT") != root:
    env["LORE_ROOT"] = root
    env_changed = True

# Also allow Read on everything under the resolved vault root so
# skills that browse wiki notes don't prompt
perm_changed = False
allow = cfg.setdefault("permissions", {}).setdefault("allow", [])
vault_rule = f"Read({root}/**)"
if vault_rule not in allow:
    allow.append(vault_rule)
    perm_changed = True

if env_changed or perm_changed:
    path.write_text(json.dumps(cfg, indent=2) + "\n")
    msgs = []
    if env_changed: msgs.append(f"LORE_ROOT={root}")
    if perm_changed: msgs.append(f"allowed {vault_rule}")
    print(f"  {'; '.join(msgs)}")
else:
    print(f"  LORE_ROOT={root} and vault read permission already set")
PYEOF
    ok "LORE_ROOT + vault read permission persisted"
fi

# ----------------------------------------------------------------------
# 4. Offer to merge hooks
# ----------------------------------------------------------------------

HOOKS_BLOCK='{
  "SessionStart": [{"hooks": [{"type": "command", "command": "lore hook session-start"}]}],
  "PreCompact":   [{"hooks": [{"type": "command", "command": "lore hook pre-compact"}]}]
}'
# Note: Stop hook is intentionally omitted. Claude Code `Stop` fires
# on every agent turn (not at session end), so a "consider /lore:session"
# hint becomes noisy. Run /lore:session manually at the end of a
# working session instead.

say "Hooks configuration"

if [[ "$WITH_HOOKS" -eq 0 ]]; then
    cat <<EOF
Magic context injection (SessionStart / PreCompact / Stop) is opt-in.
To enable, re-run with --with-hooks, or manually merge the following
into ~/.claude/settings.json under "hooks":

$HOOKS_BLOCK

Doc: examples/settings.json
EOF
else
    if ! command -v python3 >/dev/null 2>&1; then
        warn "python3 not found — skip hook merge. Edit settings.json manually."
    else
        python3 - "$SETTINGS_FILE" <<'PYEOF'
import json, os, sys
from pathlib import Path

path = Path(sys.argv[1])
path.parent.mkdir(parents=True, exist_ok=True)
cfg = json.loads(path.read_text()) if path.exists() else {}

hooks = cfg.setdefault("hooks", {})

# Desired commands — intentionally no $CLAUDE_PROJECT_DIR expansion
# (Claude Code flags shell variable expansion as 'simple_expansion'
# and prompts for permission regardless of allowlist). The hook
# resolves CWD internally via os.getcwd(), which Claude Code sets to
# the project dir when spawning hooks.
desired = {
    "SessionStart": {"type": "command", "command": "lore hook session-start"},
    "PreCompact":   {"type": "command", "command": "lore hook pre-compact"},
}

# Old commands this version supersedes. Including the Stop hook —
# it fired every agent turn (not at session end) and was noise.
# Re-install migrates users automatically.
stale_commands = {
    'lore hook session-start --cwd "$CLAUDE_PROJECT_DIR"',
    'lore hook pre-compact --cwd "$CLAUDE_PROJECT_DIR"',
    "lore hook stop",
}

added, removed = [], []

# Strip stale commands across ALL events (including events like Stop
# that we no longer install). If an event ends up with no groups, drop
# the event entirely.
for event in list(hooks.keys()):
    group_list = hooks[event]
    new_groups = []
    for grp in group_list:
        kept = [
            h for h in grp.get("hooks", []) if h.get("command") not in stale_commands
        ]
        if len(kept) != len(grp.get("hooks", [])):
            removed.append(event)
        if kept:
            new_groups.append({**grp, "hooks": kept})
    if new_groups:
        hooks[event] = new_groups
    else:
        del hooks[event]

# Install desired commands if absent
for event, cmd in desired.items():
    group_list = hooks.setdefault(event, [])
    already = any(
        any(h.get("command") == cmd["command"] for h in grp.get("hooks", []))
        for grp in group_list
    )
    if already:
        continue
    group_list.append({"hooks": [cmd]})
    added.append(event)

path.write_text(json.dumps(cfg, indent=2) + "\n")
msg = []
if added:   msg.append(f"added: {', '.join(added)}")
if removed: msg.append(f"migrated stale: {', '.join(set(removed))}")
print("  " + ("; ".join(msg) if msg else "all hooks already configured"))
PYEOF
        ok "Hooks merged into $SETTINGS_FILE"
    fi
fi

# ----------------------------------------------------------------------
# 5. Next steps
# ----------------------------------------------------------------------

cat <<EOF

$(say "Done.")

Next steps:
  1. Open a new Claude Code session — you should see a one-liner from
     the SessionStart hook:
       lore: loaded <wiki> (N notes, M open items) · /lore:why

  2. If instead you see 'no vault at LORE_ROOT=...', re-run:
       ./install.sh --lore-root=/path/to/your/vault --with-hooks

  3. Starting from scratch? Run:
       lore init
       lore new-wiki <name>

  4. Seed catalogs + search index:
       lore lint
       lore search --reindex

  5. (Teams) Register the MCP server with any MCP client:
       server = "lore mcp" (STDIO)
EOF
