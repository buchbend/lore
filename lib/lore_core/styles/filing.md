# Filing

Where each team-relevant fact goes. The SessionStart directive says when to
file; these lines say where.

- File a fact the moment it appears, with the refs behind it. Do not interrupt the user to ask.
- A defect, a concrete change, a gap between docs and code, a trap or a missing fact becomes an issue on the owning repo.
- A feature idea outside the task's scope becomes a roadmap item: run `lore roadmap add`. The next pull request carries it.
- A fact about an existing issue or PR becomes a comment on it.
- An experiment result becomes a lab entry: run the `experiment` skill or `lore lab add`.
- A dead end in an experiment becomes a lab entry with status `dead-end`, not a closed issue.
- A dead end in software work becomes an issue you opened, closed as not planned, with the reason in the close comment.
- Research, a scoping of options, an explanation for a human reader or draft paper material becomes a wiki entry. Offer it once, at a stop of the work. Write it after the person says yes: run the `wiki` skill. The skill commits and pushes the entry at once.
- A fact for a wiki topic note without a yes in the session becomes a pull request on the wiki repo.
- An ADR, a PRD or a wiki topic-note edit outside a grilling session is a pull request. A human merges it.
- A rule that looks out of date becomes a proposed amendment of its ADR.
- Close only issues you opened. Do not change the state of a human's issue or PR.
