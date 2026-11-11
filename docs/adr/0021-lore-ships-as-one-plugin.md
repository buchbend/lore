# ADR 0021: Lore ships as one plugin

- **Status:** Proposed
- **Date:** 2026-11-11
- **Deciders:** Christof Buchbender
- **Relates to:** PRD [0021](../prd/0021-one-plugin.md) (one plugin); PRD
  [0003](../prd/0003-lore-workflow-plugin-and-deterministic-substrate.md)
  (lore-workflow plugin and deterministic substrate), decision "Monorepo,
  two plugins"
- **See also:** ADR [0014](0014-one-home-per-fact.md) (one home per fact)

## Holds

- **Default:** the marketplace lists one plugin, `lore`. The repo holds one
  `plugin.json` and one version. `tests/test_version_sync.py` checks both.
- **Default:** a skill lives in `skills/` and has the name `lore:<name>`.
  No two skills share a name.
- **Default:** a renamed skill gets no alias. The changelog names the old
  and the new name.
- **Incidental:** the Python package `lore_workflow` and the command group
  `lore workflow` keep their names. Both name a module, not a plugin.
- **Incidental:** `lore setup` and `install.sh upgrade` uninstall the old
  plugin `lore-workflow@lore`. Remove that code when no install holds the
  old plugin.

## Revisit if

- A team wants the context of Lore without the build skills, and measures
  the skill listing as a cost.
- Workflow skills ship from another repo, against a `lore` command contract
  with its own version.

## Alternatives considered

- **Two plugins, one locked version.** `tools/release.py` writes the same
  version into both manifests. Rejected: the handover name clash stays, and
  the plugin boundary still claims a separation that the code does not have.
- **Two plugins, a release tool for the second version.** Rejected: the
  tool keeps two numbers correct for one release unit.
- **Alias skills under the old names for one release.** Rejected: the skill
  listing holds each workflow skill twice, and a second PR removes them.
- **Two handover skills with different names.** Rejected: a person says
  "handover" for both jobs, and the session then picks one by chance.

## Background

PRD 0003 chose two plugins for three reasons: an opt-in install, an
independent version, and a one-way dependency from the workflow to `lore`.
The workflow skills call `lore` commands from the same commit, so the two
versions describe one release unit. Four modules in `lib/` name workflow
skills, so the dependency runs both ways. PRD 0021 lists the observations.

PRD 0003 is shipped and stays as written. This ADR replaces its decision
"Monorepo, two plugins".

## Amendments

None.
