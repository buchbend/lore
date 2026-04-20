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

## paper
Citekey-named publication note.

```yaml
required: [type, citekey, title, authors, year, description, tags]
optional: [draft, status]
```

Extract when: a paper is discussed with concrete findings.

## result
Concrete outcome from analysis — numbers, plots, conclusions.

```yaml
required: [type, created, description, tags, source_session]
```

Extract when: a session produces a concrete numeric/plotted result.
