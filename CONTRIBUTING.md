# Contributing to Lore

Two things you'll want when working on Lore: an editable Python install
(so source edits are picked up live) and a clean way to test the
installer flow itself.

## Dev install (editable + local plugin marketplace)

Three commands. Same recipe doubles as the **offline / air-gapped
install path** for machines without network egress to GitHub — point
a checkout at the local filesystem and you're set.

```bash
# 1. Editable Python — source edits are picked up live, no reinstall
pipx install --force --editable ~/git/lore
# (uv users: `uv tool install --force --editable ~/git/lore`)
# (pip users: `pip install --user --force-reinstall --editable ~/git/lore`)

# 2. Register the local plugin marketplace
claude plugin marketplace add file://$(realpath ~/git/lore)/.claude-plugin/marketplace.json

# 3. Wire each host (skills, hooks, MCP, Cursor rules — same as a
#    fresh install, just pointed at the local plugin source).
lore install --lore-repo ~/git/lore
```

**Why not `pipx install lore`?** The name `lore` is squatted on PyPI
by an unrelated package. Until we publish under a different PyPI name,
the canonical non-editable install is the git+ URL form
(`pipx install git+https://github.com/buchbend/lore.git`), which is
what `lore install`'s self-install bootstrap uses internally too.

Edit `~/git/lore/skills/<name>/SKILL.md` → Claude Code picks up the
change next session. (Skill directories are bare names like
`curator`, `inbox`, `verify`; the `/lore:` slash-command prefix
comes from the plugin namespace, not the directory name.) Edit `~/git/lore/lib/lore_core/...` → next CLI
invocation runs the new code. No reinstall step.

## Testing the installer on a clean state

The `lore install` flow refuses to run on top of legacy `install.sh`
artifacts. To test it from scratch on your own machine without nuking
your real install, use the undo helper:

```bash
python3 tools/undo_install_sh.py --dry-run    # preview
python3 tools/undo_install_sh.py              # apply
lore install check                            # plan-only, never writes
lore install                                  # for real
```

After testing, re-install your editable dev setup with the three-command
recipe above.

## Filing a new host module

Adding a new host (Codex, Gemini, OpenCode, …) is one Python file.

1. **Drop a module at `lib/lore_core/install/<host>.py`** with two
   functions:

   ```python
   from lore_core import managed_files
   from lore_core.install import _helpers
   from lore_core.install.base import Action, InstallContext, LegacyArtifact

   SCHEMA_VERSION = "1"

   def plan(ctx: InstallContext) -> list[Action]:
       """Return the actions to install/upgrade Lore for this host."""

   def uninstall_plan(ctx: InstallContext) -> list[Action]:
       """Symmetric semantic remove."""

   def detect_legacy(ctx: InstallContext) -> list[LegacyArtifact]:
       """install.sh-era artifacts this host left behind. Empty for new hosts."""
   ```

2. **Wire it into the registry** at `lib/lore_core/install/__init__.py`:

   ```python
   from lore_core.install import claude, cursor, my_new_host

   REGISTRY = {
       "claude": claude,
       "cursor": cursor,
       "my_new_host": my_new_host,
   }
   ```

3. **Add a `_binary_for()` mapping** in `lib/lore_cli/install_cmd.py`
   so `--host all` can detect the host's CLI on PATH.

4. **Use the existing primitives** rather than rolling your own.
   Writing into files the user owns lives in `lore_core.managed_files`:
   - `json_merge_atomic(path, mutator, validate=…)` for JSON config
     files (handles flock + symlink realpath + retry-after-validate)
   - `write_managed_markdown(path, body)` / `remove_managed_block(path)`
     for rules / system-prompt files (preserves user content outside
     the managed markers)
   - `copy_dir_atomic(src, dst)` / `remove_managed_dir(path)` for plugin
     trees — both refuse to touch a tree without Lore's sentinel

   Install-specific pieces live in `lore_core.install._helpers`:
   - `lore_mcp_entry(SCHEMA_VERSION)` for the canonical MCP server
     block — distinguishes Lore-managed from user-authored entries
     via `_lore_schema_version`
   - `check_lore_on_path()` if your host needs `lore` reachable

   Finding the on-disk Lore source tree (skills, plugin manifest) is
   `lore_core.source_root.resolve_lore_source_root()`.

5. **Tests** in `tests/test_install_<host>.py` — follow the
   `test_install_cursor.py` shape: monkeypatch `sys.platform` for
   per-platform branches; assert action shapes for fresh / present-
   same-schema / absent-schema (silent migrate) / present-old-schema
   (replace) cases; assert uninstall round-trip preserves user content.

The dispatcher (`lib/lore_cli/install_cmd.py`) handles all the
print-and-confirm UX, legacy detection, JSON envelope, and error
reporting — your module just emits Actions.

## Schema versioning

When the shape of a Lore-managed config block changes (e.g. you add
a field to the Cursor `mcpServers.lore` entry), bump `SCHEMA_VERSION`
in the host module. The dispatcher detects:

- **Absent `_lore_schema_version`** → silent migrate-in-place
  (`kind=merge`). Don't scare the user about a "schema bump" they
  never opted into.
- **Present but older** → `kind=replace` with explicit per-action
  prompt. The presence of `_lore_schema_version` is the marker
  that says "Lore put this here; I can upgrade it."

This nuance lives in `cursor.py:_read_existing_lore_entry` and the
plan() dispatch — copy that pattern.

## Releasing a new version

`claude plugin update lore@lore` only re-fetches when the version in
`.claude-plugin/plugin.json` changes. Bump in lockstep:

1. **Decide the bump.** While we're 0.x: minor for any user-visible
   contract change (new subcommand, schema, install behaviour); patch
   for bug fixes and doc-only changes. 1.0 lands when the install
   contract stops moving.
2. **Run `python3 tools/release.py --part minor`.** The script branches
   off `origin/main`, bumps `pyproject.toml:version` and
   `.claude-plugin/plugin.json:version` in lockstep, writes the
   `## [X.Y.Z] - YYYY-MM-DD` section in `CHANGELOG.md`, runs the
   version-sync guard, commits `chore: release X.Y.Z`, pushes and opens
   the pull request.

   Write the section body yourself and pass it as `--notes notes.md`.
   Without it the section holds the commit subjects that landed since
   the last release, which is a record rather than a summary. Follow
   Keep a Changelog headings (Added / Changed / Fixed / Deprecated /
   Removed / Security). `--dry-run` prints the plan and writes nothing.
   `--no-pr` stops after the commit.
3. **Merge the pull request.** `main` is branch protected: the `test`
   check has to pass, and direct pushes are blocked for everyone,
   admins included. The script never merges.

`tests/test_version_sync.py` enforces all three files in pytest — if any
one is missing or disagrees, the test suite fails. The script runs that
test before it commits.

After the merge, users running `claude plugin update lore@lore` see the
new version and re-cache; users on `pipx install
git+https://github.com/buchbend/lore.git` re-install via
`pipx upgrade lore` (or re-run the install command).

Do not set `version` in `.claude-plugin/marketplace.json` — for
github-source plugins, the plugin manifest's version always wins
silently and a duplicate in the marketplace is ignored (per the
Claude Code plugin docs' explicit warning).

The repo also ships `lore-workflow`, a second plugin with its own manifest at `lore-workflow/.claude-plugin/plugin.json` and an independent version axis — bumping `lore` does not require bumping `lore-workflow`, and vice versa. `tests/test_version_sync.py` checks both manifests are present and marketplace-wired; it does not force them to agree. Dependency direction is one-way: `lore-workflow` may depend on `lore`'s CLI/MCP surface, `lore` never depends on `lore-workflow`.

The `_lore_schema_version` field inside `mcpServers.lore`
(installed into `~/.cursor/mcp.json` etc.) is a separate concern —
bump that only when the *managed-block shape* changes, not on every
release.

## Tests

```bash
python -m pytest          # full suite
python -m pytest tests/test_install_*.py -v   # installer-only
```

## CI

`.github/workflows/ci.yml` runs on every push and pull request. It
mirrors the local dev commands exactly — no separate CI-only config:

```bash
pip install -e .[dev]
pytest -q
ruff check .
ruff format --check .
```

`pytest` is a blocking gate. `ruff check` / `ruff format --check` run
as advisory (`continue-on-error: true`) until the repo-wide lint and
formatting cleanup tracked in #196 lands — new code you write should
still pass both locally before you push.

For end-to-end testing on a clean container, the canonical checklist
lives in the PR description that lands a release (or in
`docs/REVIEW-*.md` when one is written). If you're cutting a release,
spin up a fresh container, run the canonical install path from the
README, and verify hooks fire on a real Claude Code session.

## Things to avoid

- **Closure thunks in `Action.apply`.** Action's `payload: dict` is
  the contract; `kind`-dispatched executors live in `_helpers.py`.
  Tracebacks stay clean; payloads JSON-serialize for `lore install
  plan --json`.
- **Per-action prompt logic in host modules.** That belongs entirely
  in the dispatcher so the UX stays consistent across hosts. Host
  modules emit `kind=replace` Actions; the dispatcher decides when
  to prompt.
- **Importing `lore_cli` from `lore_core/install/`.** The dispatcher
  imports from us, not the other way. Keeps the host modules
  testable without dragging in argparse / Rich.
- **Adding strategy-pattern abstraction prematurely.** Two hosts is
  not enough to justify a strategy registry. Keep one Python file
  per host until a third host actually composes shared pieces in a
  way the existing helpers can't handle.
