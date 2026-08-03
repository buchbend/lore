---
name: lore-workflow:file-issue
description: Writes issue text and files it — resolve the team's issue
  register, draft in its skeleton (single change, batch, a caller's template, or a PR
  body), lint with Vale when Vale is installed, then post with gh. Use whenever you are
  about to write or edit a GitHub issue, a sub-issue, or a PR description. Triggers on
  "file an issue", "file these as issues", or "/lore-workflow:file-issue".
---

# File Issue

Writes issue text and files it. Every workflow skill that writes an issue or a PR body
comes here to do it, and the user can invoke it directly.

**The caller decides *what* to capture. This skill owns *how* to write and file it.**
Never re-litigate the caller's judgement about whether something deserves an issue.

## Hard rule: run in-context

Do every step in the calling session. **Never spawn a subagent** — not to draft, not to
lint, not to post. One subagent per issue is the cost this skill exists to avoid.

## 1. Resolve the register

```bash
lore style show issue-register
```

Read the output. It carries the required section skeleton, the EARS patterns for
acceptance criteria, and the prose rules. **Never write from memory of the rules** — a
team overrides the register inside its wiki, so the resolved text is the only authority.
Add `--wiki <name>` when the target repo belongs to a wiki other than the cwd's.

## 2. Draft to a file whose name ends in `.md`

Write the body to this path, with `<slug>` replaced by a short kebab-case slug of the
title you are about to use:

```
${TMPDIR:-/tmp}/lore-file-issue-<slug>.md
```

**Repeat that path literally in steps 3 and 4. Never hold it in a shell variable** —
each step may run as its own shell, and a variable set in step 2 is gone by step 3.
Vale would then get an empty argument and lint nothing.

The slug keeps two sessions apart. A fixed filename means a second session filing at the
same moment under the same `TMPDIR` overwrites the first session's draft, and the first
session then lints and posts text it never wrote. You already know the title at this
point, so the slug costs nothing and stays the same in every step.

The `.md` extension is load-bearing. The Vale config scopes its rules to `[*.md]`, so a
draft saved as `.txt`, or with no extension, lints zero files and exits 0 — step 3 would
report a clean draft without having read one line of it.

Pick the mode from what the caller handed you:

| Mode | Use when | Shape |
|---|---|---|
| **Single change** | there is one change to capture | The register's full section skeleton. One statement under "Required behaviour", with its own EARS criteria. |
| **Batch** | several changes share one Context, go into one PR, and have no ordering between them | Keep the skeleton. Give each change its own numbered subheading under "Required behaviour", then repeat the same numbered subheadings under "Acceptance criteria". **Every numbered change carries its own criteria** — never one pooled list. |
| **Caller template** | the caller supplied a structure (a sub-issue header, an epic-seed shape) | Keep the caller's structure exactly. Add no section, drop no section, reorder nothing. Apply the prose rules and EARS *inside* the supplied structure. |
| **PR body** | writing the body for `gh pr create` | Prose rules only. No register skeleton. |

Before drafting a batch, check the register's "Batch issues" section for the split rule.
A change that needs its own Context, or that must merge before another change, is a
separate issue rather than a numbered block.

### When a fact is missing

Write the section heading and `TODO:` followed by the specific question. **Never fill
the gap with a plausible guess.** A confident sentence written over a missing fact is
the failure the register exists to stop, and it survives review far too easily.

## 3. Lint — only when Vale is installed

Check for Vale first, as its own command. Keep the two commands separate — chaining
them with `&&` makes a missing Vale exit non-zero, which is indistinguishable from the
"findings" exit below, and sends you into a fix loop over a lint that never ran.

```bash
command -v vale
```

Empty output means Vale is absent. **Post the draft unlinted** and say so in one line
when you report back. The linter never blocks filing. (`lore doctor` also reports
whether Vale is on PATH.)

With Vale present:

```bash
vale --config "$(lore style vale-config)" "${TMPDIR:-/tmp}/lore-file-issue-<slug>.md"
```

Read the result by exit code, not by whether anything printed:

- **Exit 1** — at least one `error`-level finding: banned vocabulary, or a sentence past
  the length ceiling. Rewrite those, then run again until the exit code is 0.
- **Exit 0 with findings printed** — `warning`-level heuristics only. Advisory.
- **Exit 2** — the invocation itself is broken, not the prose. Vale exits 2 on an empty
  path argument and on a `--config` path it cannot resolve. **Stop and report.** Editing
  prose here rewrites a draft that was never read.

Vale reports "0 files" as success, so a lint that read nothing still looks clean. Confirm
the output names your draft file before you trust a clean result.

The heuristics over-fire by design, and the register says as much. The participle rule
matches any capitalised `-ing` word opening a sentence, so a heading like
`## Testing decisions`, or a sentence opening with `Nothing`, trips it. **Do not mangle
correct prose to silence a warning.** Leave the sentence and move on.

Cap the fix loop at three passes. A finding that survives three rewrites is a rule
fighting a correct sentence. Post the draft anyway and name the finding when you report
back.

## 4. Post the exact file you linted

```bash
gh issue create --repo <owner>/<repo> --title "<imperative title, max 12 words>" \
  --body-file "${TMPDIR:-/tmp}/lore-file-issue-<slug>.md"
```

Use `--body-file`, never a retyped `--body` string — the bytes Vale checked must be the
bytes that get posted. For a PR body, swap in
`gh pr create --body-file "${TMPDIR:-/tmp}/lore-file-issue-<slug>.md"`.

In batch mode the caller chooses the granularity it asked for: one issue holding the
numbered blocks, or one issue per block. Do not silently split or merge what you were
given.

Report every URL you created back to the caller.

## Callers

[`to-epic`](../to-epic/SKILL.md), [`seed-epic`](../seed-epic/SKILL.md),
[`brief`](../brief/SKILL.md), [`implement-issue`](../implement-issue/SKILL.md), and
[`orchestrate-epic`](../orchestrate-epic/SKILL.md) — both its follow-ups and the PR
bodies its teammates open — file through this skill instead of writing issue or PR
bodies inline.

Machine-readable text stays exempt: roadmap tables, board comments, and reviewer
verdicts are parsed, not read, so the register does not apply to them.
