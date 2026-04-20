# Surfaces — <wiki>
schema_version: 2

## concept
Cross-cutting idea or pattern across sessions.

```yaml
required: [type, created, last_reviewed, description, tags]
optional: [aliases, superseded_by, draft]
```

## decision
A trade-off made — alternatives considered, path chosen.

```yaml
required: [type, created, last_reviewed, description, tags]
optional: [superseded_by, implements]
```

## session
Work session log filed by Curator A.

```yaml
required: [type, created, last_reviewed, description]
optional: [scope, tags, draft, source_transcripts]
```

## artefact
A concrete design output — mockup, prototype, spec doc.

```yaml
required: [type, created, description, tags]
optional: [link, status]
```

## critique
A critique or review of an artefact or decision.

```yaml
required: [type, created, description, tags, target]
optional: [resolved]
```
