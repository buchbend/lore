# Surfaces — <wiki>
schema_version: 2

## concept
A named, reusable abstraction other notes can reference as `[[wikilink]]`.

```yaml
required: [type, created, last_reviewed, description, tags]
optional: [aliases, superseded_by, draft]
plural: concepts
extract_prompt: |-
  A concept is a NAMED, REUSABLE ABSTRACTION that other notes can reference by its title
  (e.g. `[[host-adapter-layer]]`, `[[curator-spawn-backpressure]]`). Extract ONLY when ALL hold:

  - Clean noun-phrase title that would age well as a wikilink target. Verb-phrase / shipping-arc
    titles ("phase B day-split shipped", "v0.7.0 release") are NOT concepts — they belong in
    session notes.
  - The body DEFINES the abstraction — what it is, why it exists, where it applies. It does NOT
    narrate sessions or summarise recent work.
  - A future reader could understand the concept WITHOUT reading the source sessions. The
    sessions are evidence the concept emerged; they are not the concept itself.

  Reject when the cluster is:
  - A status report, retrospective, or progress summary ("we shipped phases A through D").
  - Tightly bound to one feature's shipping arc (the feature note belongs in sessions/).
  - A single specific incident (use a decision or session note).
  - A description of code currently in the repo (read the code; don't restate it).

  Title test: does it work as a `[[wikilink]]` target a year from now? Body test: could the same
  body have been written *before* the cluster's sessions happened, given the right inputs?

  **Tags** — 2–5 short, lower-case, hyphenated tags in the `topic/<area>` namespace
  (e.g. `topic/curator`, `topic/cli`, `topic/knowledge-management`). Tags are for
  discovery and clustering — pick orthogonal axes that future search will land on,
  not synonyms of the title. Never emit `tags: []`; if you cannot name two tags the
  cluster does not yet warrant a concept. No project-name tags (those go in scope).
```

Extract when: a named abstraction has emerged that other notes will want to reference.

## decision
A choice between alternatives, with the trade-off and rationale captured.

```yaml
required: [type, created, last_reviewed, description, tags]
optional: [superseded_by, implements]
plural: decisions
extract_prompt: |-
  A decision records a CHOICE made between EXPLICIT ALTERNATIVES, with the trade-off and
  rationale captured. Extract ONLY when ALL hold:

  - The cluster contains an explicit choice with at least one stated alternative that was rejected.
  - The trade-off is articulated: what was gained, what was given up, why this beat the alternative.
  - The decision has lasting force — future work will be informed by it, not merely affected by it.

  Anchor on RATIONALE PRESENCE, not section headings. A bullet in a session note's
  ``## Decisions made`` section is a CANDIDATE — it satisfies the "decision was made"
  bar but you must still apply the rejection rules below. Do NOT treat presence in
  Decisions made as license to extract; the section often carries tactical bullets
  the author noted for completeness rather than for permanent record.

  Body should follow this structure (omit empty sections):
  - **Context** — the problem that prompted the choice
  - **Alternatives considered** — each rejected option with a one-line "why not"
  - **Chosen** — the path taken, with rationale
  - **Consequences** — what this commits the project to (optional)

  Reject when:
  - The cluster is purely descriptive (a feature was built, no rejected alternative).
  - The "decision" is a routine implementation step (use the right library, follow conventions).
  - The choice is reversible without lasting consequence.
  - The rationale would not still matter six months from now (six-month rule).
  - The bullet reads as activity narrative ("we decided to do X") rather than as
    a standing claim ("X is how we handle Y because Z"). Activity belongs in
    sessions, not decisions.

  Title names the CHOICE, not the topic. Good: "Threads.md as derived read-only surface".
  Bad: "Surface authoring" (that's a topic, not a decision).

  **Tags** — 2–5 short, lower-case, hyphenated tags in the `topic/<area>` namespace
  (e.g. `topic/process`, `topic/security`, `topic/storage`). Tags are for discovery
  and clustering across decisions — pick orthogonal axes future search will land on,
  not synonyms of the title. Never emit `tags: []`; if you cannot name two tags
  the choice is probably not yet a decision worth recording.
```

Extract when: a session note records a trade-off with explicit rejected alternatives.

## session
Work session log filed by Curator A.

```yaml
required: [type, created, last_reviewed, description]
optional: [scope, tags, draft, source_transcripts]
authored_by: curator_a
```
