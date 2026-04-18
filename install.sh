#!/usr/bin/env bash
# Lore — DEPRECATED installer.
#
# This bash script has been replaced by `lore install` (Python CLI
# subcommand). The new flow handles Claude Code + Cursor as v1 hosts,
# uses Claude Code's own plugin system to wire hooks/MCP/skills, and
# has a symmetric `lore uninstall` path.
#
# If you ran this script previously, clean up the legacy state first:
#
#   python3 tools/undo_install_sh.py --dry-run   # preview
#   python3 tools/undo_install_sh.py             # apply
#
# Then install with the new flow:
#
#   pipx install lore                # the Python CLI
#   lore install                     # detect & wire installed hosts
#   lore init                        # scaffold a vault + set $LORE_ROOT
#
# Dev / offline install: see CONTRIBUTING.md for the editable-from-
# checkout recipe (pipx install --editable + local plugin marketplace).
#
# This shim exists so users with bookmarks / shell history / docs
# pointing at `./install.sh` get a clear next step instead of a silent
# "no such command" failure.

set -e

cat <<'EOF'

==> install.sh is deprecated.

  Please use the new flow:

    pipx install lore
    lore install
    lore init

  If you previously ran this script, clean up the legacy ~/.claude
  state first:

    python3 tools/undo_install_sh.py --dry-run
    python3 tools/undo_install_sh.py

  Then re-install with `lore install`.

  Dev install (editable + local marketplace):
    See CONTRIBUTING.md.

  More: https://github.com/buchbend/lore#install

EOF

exit 1
