---
title: "One plugin"
status: draft
epic: https://github.com/buchbend/lore/issues/588
repos:
  - buchbend/lore
---

# PRD 0021: One plugin

> Source of truth for this epic. Tracker: [epic issue](https://github.com/buchbend/lore/issues/588).
> The epic links here; this file is not embedded in the issue body.

Decision record: ADR [0021](../adr/0021-lore-ships-as-one-plugin.md) (Lore
ships as one plugin).

## Problem

PRD [0003](0003-lore-workflow-plugin-and-deterministic-substrate.md)
(lore-workflow plugin and deterministic substrate) split the repo into two
plugins, `lore` and `lore-workflow`. The split gave three benefits on paper.
A review on 2026-11-11 found that two no longer hold.

- **The repo tracks two versions for one release unit.** The workflow skills
  call `lore` commands from the same commit. `git log` on `main` holds 26
  bump commits for `lore-workflow` and 80 for `lore`.
- **The second version has no tool.** `tools/release.py` bumps only the
  `lore` manifest. A person bumps `lore-workflow/.claude-plugin/plugin.json`
  by hand.
- **A missed bump is silent.** `claude plugin update` fetches a plugin again
  only when its version changes. Without the bump, an installed cache keeps
  the old skills.
- **The dependency direction is no longer one-way.** PRD 0003 states that
  nothing in `lore` references the workflow skills. Four modules in `lib/`
  now name them: `lore_core/stages.py`, `lore_core/token_trace.py`,
  `lore_cli/setup_cmd.py` and `lore_workflow/ledger.py`.
- **Two skills share one name.** `/lore:handover` writes the note for the
  next session. `/lore-workflow:handover` writes the handover section and
  the seed issue. A person who says "handover" gets one of the two.
- **The opt-in install is the one benefit left.** A skill that nobody calls
  costs one description line in the skill listing.

## Solution

- **One plugin.** The marketplace lists `lore` only. The repo holds one
  `plugin.json` and one version.
- **One skill prefix.** Every skill has the name `lore:<name>`. The 11
  workflow skills move from `lore-workflow/skills/` to `skills/`.
- **One handover skill.** `lore:handover` holds both jobs in two parts. Part
  one writes the note for the next session. Part two writes the handover
  section and the seed issue.
- **Clean break.** The old names `/lore-workflow:<name>` stop to exist in the
  release that merges the plugins. The changelog names the change. Lore
  ships no alias skills.
- **Old installs are cleaned.** `lore setup` and `install.sh upgrade` remove
  the old plugin `lore-workflow@lore` where the plugin index holds it.

## Implementation decisions

- **Marketplace and manifest.** `.claude-plugin/marketplace.json` holds one
  entry. `lore-workflow/.claude-plugin/plugin.json` is deleted. The `lore`
  plugin description names the build skills.
- **Skill folders.** The folders `build`, `debug`, `document`, `experiment`,
  `file-issue`, `grilling`, `orient`, `tdd`, `to-epic` and `triage` move to
  `skills/`. Each `SKILL.md` front-matter `name:` takes the `lore:` prefix.
  Relative links between the skills keep working.
- **Handover.** The two `SKILL.md` files become one, `skills/handover/`. The
  description names both triggers. The glossary gains one term for the note
  that part one writes, in its own commit. `build` keeps its link to the
  shape of the handover section.
- **Support files.** `TIER-DELEGATION.md` and `THIRD-PARTY.md` move with the
  skills. The content of `lore-workflow/README.md` moves into the root
  `README.md`. The folder `lore-workflow/` is deleted.
- **Python names stay.** The package `lore_workflow` and the command group
  `lore workflow` keep their names. Both name a module, not a plugin.
- **Code that names a skill.** The stage tags, the token trace parser, the
  ledger resume line and the Cursor skill roots use the new names and the
  one skill root.
- **Living documents.** The root `README.md`, `CONTRIBUTING.md`,
  `CONTEXT.md`, `CODEMAP.md`, `docs/conventions.md`, `docs/how-to/` and
  `docs/explanation/` use the new names. The glossary entries `Skill` and
  `Workflow` drop the second plugin.
- **Historical documents stay.** The files in `docs/adr/` and `docs/prd/`,
  `CHANGELOG.md` and the sweep reports in `docs/` keep the old names. They
  record what was true at the time.
- **Old installs.** The plugin refresh of `lore setup` reads the plugin
  index. Where the index holds `lore-workflow@lore`, the refresh uninstalls
  that plugin and prints one line. `install.sh upgrade` does the same. The
  doctor check names an old plugin that is still installed.
- **Release.** The slice that moves the skills holds the version bump as its
  last commit. `tools/release.py` needs no change.

## Testing decisions

- A good test reads the files the plugin ships: the marketplace, the
  manifest, each `SKILL.md` front-matter. It does not check prose wording.
- `tests/test_version_sync.py` asserts one marketplace entry and one
  manifest. The test of the independent workflow version is deleted.
- `tests/test_workflow_plugin_structural.py` checks the skills under
  `skills/`. One new check asserts that no two skills share a `name:`.
- `tests/test_workflow_docs_drift.py` checks `lore:<name>` in the living
  documents. One new check fails when a living document or a skill still
  holds `lore-workflow:`. The check skips the historical documents.
- The setup tests in `tests/test_setup_cmd.py` are the prior art for the old
  install: a fake `claude` binary records each call.

## Out of scope

- A rename of the Python package `lore_workflow` or of `lore workflow`.
- Alias skills under the old names.
- Edits to historical PRDs, ADRs and `CHANGELOG.md` entries.
- A change to what a skill does, other than the merge of the two handover
  skills.
- Old installs on Cursor, beyond the one skill root.
