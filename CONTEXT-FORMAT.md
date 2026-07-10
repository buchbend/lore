# Session note format v2 — title and body shape

Refines the note-essence voice from `CONTEXT.md`'s "The session note"
section: same vocabulary (chapter, topic block, lead, disclaimer), two
rendering changes that make a note's *display* title and its *skim
layer* easier to read at a glance. Grounded in
`lore_core/note_document.py` and `lore_curator/stub_note.py`.

## Title: `scope: name`

A note's frontmatter `title` is a placeholder at creation
(`stub_note._placeholder_title`, e.g. `proj:x session — 2026-07-10`) —
the first heartbeat fires before any chapter exists, so there's no
topic to name yet. Once the first chapter composes, the title is
replaced with `<scope>: <name>`:

- **scope first** — the linkage scope (repo/project slug, e.g.
  `proj:x` or `ccat:data-center`) so a list of notes sorts and scans
  by *where*, not by an arbitrary date stamp.
- **name second** — a compiled human-readable name taken from the
  first chapter's opening lead (`stub_note._topic_title`, reusing
  `_lead_for_rename`'s same source text as the filename slug — title
  and filename always name the same topic).

Example: `proj:x: Traced the flush race`.

The rename happens once, on chapter 1 only
(`note_document.append_chapter`'s `title` kwarg is a no-op past the
first chapter) — later chapters extend the note but never rename it,
matching the existing filename-rename behavior
(`chapter_flush._rename_to_topic_slug`).

## Body: inline lead

Each topic block's bold lead sentence opens the same paragraph as its
body prose — not a standalone bold line sitting above a blank-line
gap:

```
**Chose an append-only note model.** We decided the note file is
append-only until close.
```

not:

```
**Chose an append-only note model.**

We decided the note file is append-only until close.
```

A reader reads the bold sentence and bails, or keeps reading the same
paragraph for the rest of the topic — no forced stop between claim and
support. The skim layer (bold leads read top-to-bottom, CONTEXT.md)
is unchanged: leads are still bold, still self-sufficient, still first
in their paragraph. Quote and `@N` anchor still follow as their own
lines. See `note_document._render_block`.
