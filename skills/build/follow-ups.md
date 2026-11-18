# Follow-ups and questions

Read this when the work turns up a defect, an idea or a decision the user has to make. It
applies in every mode.

## Sort each follow-up

Most follow-ups land in the first group. An issue is the exception.

- **Fix it here.** A defect or an obvious improvement that needs no decision and stays small:
  about 50 lines each, at most three per PR. Fix it test first on this branch. List it under
  `## Found and fixed` in the PR body, one line each. In `epic` mode the teammate lists it in
  the sub-issue comment.
- **Ask.** It needs the user's decision. Follow § Questions.
- **File, seldom.** Too large for this PR, in another repo, or unrelated to the work. A defect
  or a concrete change becomes an issue with a `filed` ledger line. A feature idea outside the
  run's scope goes to the roadmap: `lore roadmap add` on the build branch. Set its `left` line
  `approved`.

## Questions

Never stop for a decision while other work can go on. Log each one as a `question` line
(`lore workflow ledger-add --kind question --text "…"`; in `epic` mode with `--path -`, on the
board).

- **The guess is cheap to undo:** build your best guess and keep going.
- **Otherwise:** park only the work that depends on the answer. Build the rest.
- **Ask in one numbered message** when new questions arise or the run runs out of work. Ask
  again only about new or unanswered questions.
- **Give each question its context**, so the user can answer cold:
  - what you were building;
  - what forces the choice;
  - the options and what each costs;
  - the guess you built, or the work you parked.
- **An answer** sets the line `approved`. Apply the answer before the finish point. A question
  that no longer matters is `dropped`.
- **Stop the run** only when every remaining piece of work waits on an answer.
