# Filing

Where each team-relevant fact goes. The SessionStart directive says when to
file; these lines say where.

- A defect, a concrete change, a gap between docs and code, a trap or a missing fact becomes an issue on the owning repo.
- A feature idea outside the task's scope becomes a roadmap item: run `lore roadmap add`. The next pull request carries it.
- A fact about an existing issue or PR becomes a comment on it.
- A dead end becomes an issue you opened, closed as not planned, with the reason in the close comment.
- A fact for a wiki topic note becomes a pull request on the wiki repo.
- An ADR, a PRD or a wiki topic-note edit outside a grilling session is a pull request. A human merges it; never merge it yourself.
- Close only issues you opened; never change the state of a human's issue or PR.
