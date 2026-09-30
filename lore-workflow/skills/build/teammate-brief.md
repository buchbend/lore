# Teammate brief

Fill the brackets and pass the text to each `epic` teammate.

> Implement **<feature title>** (sub-issue #<n>) in repo <repo>.
> Worktree <path>, branch `feat/<n>-<slug>` off `epic/<issue>`. The PR targets `epic/<issue>`.
> Acceptance criteria: <criteria>.
>
> Codemap excerpt, the same text for every teammate: <objective; expected output: one branch,
> one PR, red→green evidence; the ranked ~1k-token excerpt from `lore codemap`; the scope fence
> and the files sibling features edit>. Read it before you explore. Widen from it only as needed.
>
> Method: strict TDD with `/lore-workflow:tdd`. Put the failing-test output in the PR body.
> Before you push, run ruff (check and format) and the test files you touched, plus the tests of
> shared surfaces you reach: routes, permissions, schema. Do not run the full suite; epic-branch
> CI runs it after each merge. When stuck, use the `/lore-workflow:debug` circuit breaker, not a
> fourth blind fix.
>
> Scope: change only what this feature needs. Siblings may edit the same files at the same time;
> keep your edits there small. A rebase conflict comes back to you.
>
> Decisions: do not write an ADR or a `CONTEXT.md` term. Report each ADR candidate and each term
> candidate in your final report, one line each, with the reason.
>
> File writes: parallel teammates can share an editor isolation pointer, so an in-place
> Edit/Write can land in a sibling's worktree. Apply file changes through the shell at absolute
> paths.
>
> Deliver: push, write the PR body through `/lore-workflow:file-issue` in PR-body mode, open the
> PR linking #<n>. Report the PR number, the red→green evidence, a one-paragraph summary, and the
> candidates above.

Exception: in the human-present option of `epic` mode (`SKILL.md`), replace the Deliver
paragraph. The teammate commits on its branch, opens no PR, and reports the branch.
