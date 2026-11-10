---
name: lore-workflow:experiment
description: Run one experiment and record it as one lab entry — write the question and the expected result before the run, run it, then write the entry with `lore lab add` and commit its file on the current branch. Use when the user wants to try, measure, calibrate, benchmark or test a hypothesis, says "run an experiment" or "lab entry", or a result needs a record that a later run can repeat or build on.
---

# Experiment

One experiment gives one **lab entry**: the dated record of the experiment,
with five labelled parts and a stamp that Lore writes (ADR 0019, the lab
notebook). The skill has three steps. Do them in order.

## 1. Before the run: write the question and the expected result

Write two lines in the conversation, before you start anything:

- **Question:** the one question the run answers.
- **Expected:** the result you expect, as a number or an outcome.

Write the expected result first, so the entry shows whether the result was a
surprise. Read the thread first when the experiment follows earlier entries:
`lore lab show --thread <slug>`.

## 2. Run the experiment

- Run the command. Keep the exact command line for `--cmd`.
- Keep the path of each data file and plot. An entry links to them by path;
  it holds no plot and no binary result.
- Report the numbers that the run printed. Never write a number the run did
  not produce.

## 3. Write one lab entry and commit its file

Write the entry in five labelled parts, at most 400 words:

```markdown
**Question:** the question from step 1.
**Setup:** what ran, on what, with which settings.
**Result:** what came out, with the numbers. Name the expected result from step 1.
**Interpretation:** what the result means. Do not restate the result.
**Next:** the next experiment, or "none".
```

Pass the entry on stdin, so no entry file lands in the repo:

```
lore lab add --text-file - --cmd '<command line>' --data <path> \
  --thread <slug> --status open|concluded|dead-end <<'EOF'
**Question:** ...
EOF
```

- Lore writes the stamp: date, author, repo, commit, dirty flag, branch and
  host. Lore adds a SHA-256 for each `--data` path that is a readable file.
  Do not write the stamp yourself. An uncommitted change sets the dirty flag;
  commit the code of the run first.
- `--status concluded` when the run answered the question.
- `--status dead-end` when the experiment failed to answer the question.
  A dead end is a lab entry, not a closed issue.
- `--status open` (the default) when the idea needs more runs.
- `--thread <slug>` groups the entries that follow one idea. Reuse the slug
  of the earlier entries.

The `file` sink writes the entry to `docs/lab/YYYY-MM/`. Commit that file on
the current branch, alone, with the message `lab: <question>`. Do not edit an
entry later; a later entry in the same thread corrects it.
