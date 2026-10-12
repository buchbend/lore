# Logbook entry

Read this at a finish point, after the handover section and before the merge approval. The
[`handover`](../handover/SKILL.md) skill runs the same step at a stop that hands work over. ADR
0017 (the logbook) holds the decision.

1. **Write the entry** into a file. Keep to one paragraph of 80 words or fewer.
   - Link at least one PR, issue, PRD or ADR. Restate no fact that has a home there.
   - Name no merge state. The commit on the target branch and the PR link show it.
   - Start no line with `todo:` or `decided:`.
   - At a stop that hands work over, end with a `Waiting on:` line that names the blocker.
2. **Add the entry:** `lore log add --ref <ref> --text-file <file>`. The ref is the PR
   (`owner/repo#n`), the issue, the epic or `branch@sha`.
   - Exit code 2 names the broken rule. Fix the entry and run the command again.
   - Lore delivers the entry to every sink of the scope at once. A sink that fails stays
     undelivered on the `[logbook]` line of the local ledger. The failure does not block the
     merge. `lore log flush` retries it.
3. **Commit the change** under `docs/logbook/` on the build branch, before the version bump.
