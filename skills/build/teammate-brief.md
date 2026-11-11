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
> Scope: change only what this feature needs. A feature idea outside your scope goes to the
> roadmap: run `cd <path> && lore roadmap add "Title" ["paragraph"]` in one shell call, and
> commit the change on your branch. Siblings may edit the same files at the same time; keep your
> edits there small. A rebase conflict comes back to you.
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
> evidence, a one-paragraph summary, and the candidates above, in 15 lines or fewer. The
> sub-issue comment holds the detail.
