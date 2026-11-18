# Teammate brief

Fill the brackets and pass the text to each `epic` teammate. Open the spawn's description with
the first line of the brief too. Lore reads the line to count the teammate's tokens for the
stage and the epic.

> [build:implement] <owner/repo#epic>
>
> Implement **<feature title>** (sub-issue #<n>) in repo <repo>.
> Worktree <path>, branch `feat/<n>-<slug>` off `epic/<issue>`. Do not push and open no PR: the
> lead merges your branch locally.
> Acceptance criteria: <criteria>.
>
> Codemap excerpt, the same text for every teammate: <objective; expected output: one branch,
> red→green evidence; the ranked ~1k-token excerpt from `lore codemap`; the scope fence
> and the files sibling features edit>. Read it before you explore. Widen from it only as needed.
>
> Method: strict TDD with `/lore:tdd`. Keep the failing-test output for the sub-issue
> comment. Before you report, run ruff (check and format) and the test files you touched, plus the tests of
> shared surfaces you reach: routes, permissions, schema. Do not run the full suite; the lead runs
> it locally before each merge. When stuck, use the `/lore:debug` circuit breaker, not a
> fourth blind fix.
>
> Substeps: when the sub-issue holds `## Substeps`, build them in order. Make one commit per
> substep, with the substep number in the commit subject (`feat(x): substep 2 — ...`). Leave
> one ticked box per landed commit in the sub-issue.
>
> Scope: change only what this feature needs, plus small defects you find on the way: fix
> those test first, about 50 lines each, at most three, and list each under "Found and fixed"
> in your sub-issue comment. Report anything larger in your final report. A feature idea outside your scope goes to the
> roadmap: run `cd <path> && lore roadmap add "Title" ["paragraph"]` in one shell call, and
> commit the change on your branch. Siblings may edit the same files at the same time; keep your
> edits there small. A rebase conflict comes back to you.
>
> Questions: a choice that needs the user does not stop you. Build your best guess when it is
> cheap to undo, otherwise skip only the part that depends on it. Report each question with its
> context, the options and what you built.
>
> Decisions: do not write an ADR or a `CONTEXT.md` term. Report each ADR candidate and each term
> candidate in your final report, one line each, with the reason.
>
> File writes: parallel teammates can share an editor isolation pointer, so an in-place
> Edit/Write can land in a sibling's worktree. Apply file changes through the shell at absolute
> paths.
>
> Deliver: commit on your branch. Write what a PR body would hold through
> `/lore:file-issue` in PR-body mode and post it as a comment on the sub-issue
> (`gh issue comment <n> --body-file …`). Report the branch, its head SHA, the red→green
> evidence, a one-paragraph summary, the found-and-fixed items, the questions, and the candidates above, in 15 lines or fewer. The
> sub-issue comment holds the detail.
