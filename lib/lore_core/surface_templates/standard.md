# Surfaces — <wiki>
schema_version: 2

## concept
Cross-cutting idea or pattern across sessions.

```yaml
required: [type, created, last_reviewed, description, tags]
optional: [aliases, superseded_by, draft]
plural: concepts
extract_prompt: "A cross-cutting idea. Extract when the same idea appears across 3+ session notes."
```

Extract when: pattern appears across 3+ session notes.

## decision
A trade-off made — alternatives considered, path chosen.

```yaml
required: [type, created, last_reviewed, description, tags]
optional: [superseded_by, implements]
plural: decisions
extract_prompt: "A trade-off with alternatives considered. Title reflects the choice."
```

Extract when: a session note records a trade-off decision.

## session
Work session log filed by Curator A.

```yaml
required: [type, created, last_reviewed, description]
optional: [scope, tags, draft, source_transcripts]
```
