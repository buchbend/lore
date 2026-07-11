#!/usr/bin/env bash
# install.sh — Lore bootstrap installer.
#
# Usage:
#   First install:     curl -fsSL https://raw.githubusercontent.com/buchbend/lore/main/install.sh | sh
#   Upgrade:           re-run the same one-liner, OR `lore install --upgrade`
#   Editable/dev:      LORE_FROM=/path/to/repo ./install.sh
#   Skip integrations: ./install.sh --no-configure
#   Uninstall:         ./install.sh uninstall
#
# The script is intentionally thin and never imports the `lore` Python
# package. It picks an installer (pipx / uv / pip), installs or
# upgrades the binary, then chains into `lore install` to wire up
# integrations. Safe to re-run while a previous `lore` is loaded — we
# exec the new binary at the end, never modifying the running one.
#
# This file is the source of truth for installation; the `lore install
# --upgrade` flag fetches and runs it via curl.
#
# Note for legacy users: a previous `install.sh` (predating the Python
# CLI) wrote skill symlinks directly into ~/.claude. If you ran that
# old script, run `python3 tools/undo_install_sh.py` to clean up before
# proceeding. The chained `lore install` step refuses to run otherwise.

set -euo pipefail

LORE_FROM="${LORE_FROM:-git+https://github.com/buchbend/lore.git}"
CONFIGURE=1
MODE="install"

# ------------------------------------------------------------------------- args
while [ $# -gt 0 ]; do
    case "$1" in
        upgrade)         MODE="upgrade" ;;
        uninstall)       MODE="uninstall" ;;
        --no-configure)  CONFIGURE=0 ;;
        --from)          shift; LORE_FROM="${1:?--from requires a value}" ;;
        -h|--help)       sed -n '2,22p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
        *) echo "install.sh: unknown arg: $1" >&2; exit 2 ;;
    esac
    shift
done

say()  { printf '\033[36m▸\033[0m %s\n' "$*"; }
warn() { printf '\033[33m!\033[0m %s\n' "$*" >&2; }
die()  { printf '\033[31m✗\033[0m %s\n' "$*" >&2; exit 1; }

# --------------------------------------------------------------- installer pick
pick_installer() {
    # Order matters: pipx is the canonical install for app-style Python tools;
    # uv is fine and faster but less ubiquitous; pip --user is the fallback.
    if command -v pipx >/dev/null 2>&1; then echo pipx; return; fi
    if command -v uv   >/dev/null 2>&1; then echo uv;   return; fi
    if command -v pip  >/dev/null 2>&1; then echo pip;  return; fi
    echo none
}
INSTALLER="$(pick_installer)"

if [ "$INSTALLER" = "none" ]; then
    die "lore install requires one of pipx / uv / pip on PATH.
        Install pipx first: https://pipx.pypa.io/stable/installation/"
fi

# ---------------------------------------------------------------- package step
already_installed() {
    case "$INSTALLER" in
        pipx) pipx list --short 2>/dev/null | grep -q '^lore ' ;;
        uv)   uv tool list 2>/dev/null | grep -q '^lore' ;;
        pip)  pip show lore >/dev/null 2>&1 ;;
    esac
}

run_install() {
    case "$INSTALLER" in
        pipx)
            if already_installed; then
                say "Upgrading lore via pipx (source: $LORE_FROM)"
                pipx install --force "$LORE_FROM"
            else
                say "Installing lore via pipx (source: $LORE_FROM)"
                pipx install "$LORE_FROM"
            fi
            ;;
        uv)
            say "Installing/upgrading lore via uv tool (source: $LORE_FROM)"
            uv tool install --force "$LORE_FROM"
            ;;
        pip)
            say "Installing/upgrading lore via pip --user (source: $LORE_FROM)"
            pip install --user --upgrade "$LORE_FROM"
            ;;
    esac
}

run_uninstall() {
    case "$INSTALLER" in
        pipx) say "Removing lore via pipx";    pipx uninstall lore    || true ;;
        uv)   say "Removing lore via uv tool"; uv tool uninstall lore || true ;;
        pip)  say "Removing lore via pip";     pip uninstall -y lore  || true ;;
    esac
}

case "$MODE" in
    install|upgrade) run_install ;;
    uninstall)       run_uninstall ;;
esac

# ---------------------------------------------------------------------- handoff
if [ "$MODE" = "uninstall" ]; then
    say "Done. To remove Claude's plugin entry too: claude plugin uninstall lore@lore"
    exit 0
fi

if [ "$CONFIGURE" -eq 0 ]; then
    say "Skipping integration configuration (--no-configure)."
    exit 0
fi

if ! command -v lore >/dev/null 2>&1; then
    warn "lore is installed but not on PATH yet."
    warn "Add ~/.local/bin (pipx) or ~/.local/share/uv/tools/bin (uv) to PATH, then re-run this installer."
    die "install did not leave a runnable 'lore' on PATH."
fi

# Detect first-time vs re-run: if Claude already lists lore@lore, skip the
# full integration plan on upgrade — the user may have integrations on
# machines where they don't want them re-applied. Just refresh the manifest
# cache. Always configure on first install.
PLUGIN_INDEX="$HOME/.claude/plugins/installed_plugins.json"
HAS_PLUGIN=0
if [ "$MODE" = "upgrade" ] && [ -f "$PLUGIN_INDEX" ] && grep -q '"lore@lore"' "$PLUGIN_INDEX"; then
    HAS_PLUGIN=1
fi

if [ "$HAS_PLUGIN" -eq 1 ]; then
    say "Refreshing Claude plugin cache (integrations already configured)..."
    if command -v claude >/dev/null 2>&1; then
        claude plugin update lore@lore || warn "claude plugin update failed; run it manually"
    else
        warn "claude CLI not found; run: claude plugin update lore@lore"
    fi
    say "Done. Restart Claude Code to load the refreshed plugin."
else
    say "Starting the lore init wizard..."
    exec lore init
fi
