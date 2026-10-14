# Logbook entry

Read this at a finish point, after the handover section and before the merge approval. The
[`handover`](../handover/SKILL.md) skill runs the same step at a stop that hands work over. ADR
0017 (the logbook) holds the decision.

The entry is for a human: a colleague who picks up the work next, or the author a month later.
It answers "Why was this work done, and what does it mean for the team?"

1. **Write the entry** into a file, in four labelled parts:

   | Label | Answers | Holds |
   |---|---|---|
   | `**Why:**` | Why did the work happen, and why now? | The problem or the need, and what made it matter. |
   | `**What we did:**` | How did the work solve it? | The approach, with at least one choice and its reason: "A, not B, because …". Not every change: the PR lists them. |
   | `**Consequences:**` | Who notices, and what is different for them? | One line per group: users, admins, the person who deploys, other repos. Describe the change and give no steps. Link the PR for the steps. "Nobody outside this repo notices" is an answer. |
   | `**Not done yet:**` | What did this work leave open? | Follow-up issues the work filed, and things nobody checked yet. Say which of the two each item is. Leave out older issues the work did not touch. |

   - Aim for about 200 words. `lore log add` rejects more than 300.
   - Write for a reader who was not in the session. Summarise facts from the PR, the ADRs and
     the issues in plain words, and link each one. The entry is dated and nobody edits it, so
     the summary records what the team knew that day.
   - Give every reference its title in a few words: `[#729](…) (chat agent checks and pin form)`.
   - Name no merge state. The commit on the target branch and the PR link show it.
   - Start no line with `todo:` or `decided:`.
   - At a stop that hands work over, end with a `Waiting on:` line that names the blocker.

   An example:

   ```markdown
   **Why:** Logbook entries read as lists of numbers. A colleague who picked up a feature could
   not follow the reasoning, and neither could the author a month later.

   **What we did:** An entry now has four labelled parts and up to 300 words. It summarises the
   PR and the ADRs in plain words, not only links them: the entry is dated and nobody edits it,
   so the summary cannot drift ([ADR 0017](…), the logbook).

   **Consequences:**
   - Daily reports show longer entries, split by label.
   - `lore log add` rejects an entry without the four labels.

   **Not done yet:**
   - Not yet checked: how project-manage renders the labels and lists.
   ```

2. **Add the entry:** `lore log add --ref <ref> --text-file <file>`. The ref is the PR
   (`owner/repo#n`), the issue, the epic or `branch@sha`.
   - Exit code 2 names the broken rule. Fix the entry and run the command again.
   - Lore delivers the entry to every sink of the scope at once. A sink that fails stays
     undelivered on the `[logbook]` line of the local ledger. The failure does not block the
     merge. `lore log flush` retries it.
3. **Commit the change** under `docs/logbook/` on the build branch, before the version bump.
