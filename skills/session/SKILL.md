---
name: lore:session
description: Write a session note to the correct Lore wiki. Auto-capture
  (curator A) handles this automatically from the transcript; use
  `/lore:session` only for explicit, hand-composed capture or to add an
  extraction the auto path wouldn't catch. Run with "/lore:session".
user_invocable: true
---

# Session Note Writer

Writes one session note to the correct Lore wiki. Most of the time you
don't need this — auto-capture (curator A) writes session notes from
the transcript without an explicit gesture. Use `/lore:session` when
you want to **explicitly** compose and file a note for the current
session, or when you want to promote a clear concept/decision the auto
path wouldn't extract.

## Inputs

- **GIST** — ≤300-word summary you compose by scanning the conversation:
  what was worked on (3–5 terse bullets), decisions made (capture the
  *why*), open items (future-you needs these), repos touched (brief —
  re-derived from `git log`). If a clear new concept or decision warrants
  extraction, flag it; otherwise say so explicitly.
- **CWD** — the directory the session ran in (used for routing, scope,
  repo, identity).

Optional:
- **TARGET_WIKI** — explicit wiki name (else inferred from the
  attachment registry).
- **EXTRACT** — `auto` (default), `none`, or a list of proposed slugs.

## Workflow — three tool calls minimum

### 1. MCP scaffold-read (silent, fast)

Call `mcp__lore__lore_session_scaffold` with:

```
{
  "cwd": "<CWD>",
  "slug": "<short kebab-case topic from gist>",
  "description": "<one-sentence summary from gist>",
  "title": "<descriptive — defaults to slug>",
  "target_wiki": "<TARGET_WIKI or omit>",
  "tags": [<3–5 wiki-appropriate tags>],
  "implements": [<proposal slugs that landed, if any>],
  "loose_ends": [<short-form lines for frontmatter; long-form goes in body>],
  "project": "<primary project name or omit>"
}
```

The tool returns a dict with the resolved `wiki`, `note_path`,
`frontmatter`, `frontmatter_yaml`, `body_template`, `handle`, `scope`,
`team_mode`, `commit_log`, and `existing` (whether a note for this
date+slug already exists).

If the scaffold returns `error`, surface it to the user and stop.

### 2. Find wikilink candidates (optional MCP read)

If the gist references concept names or decisions you want to link,
call `mcp__lore__lore_search` once:

```
{"query": "<key topic from gist>", "wiki": "<scaffold.wiki>", "k": 8}
```

Use the top hits as `[[wikilink]]` candidates in the body. **Do not
Read the candidates** unless you need to update one.

**Wikilink discipline.** `[[ ]]` is reserved for slugs that actually
resolve to a vault note — i.e. results returned from `lore_search`,
or notes you are creating in this same session (the extraction in
step 5). Never wrap file paths (`lib/foo/bar.py`), directory paths,
external repo names (`org/repo`), PR/issue refs (`PR #77`, `#29`,
`org/repo#66`), code symbols, env vars, version strings, branch
names, or tool/product names. Use backticks for code-shaped tokens
and plain text for everything else. The lint report flags every
unresolved `[[X]]`; a broken wikilink is worse than none.

### 3. Compose the body and write — one Bash call

Use the scaffold's `body_template` as the skeleton. It's already
filled with the H1 and the `## Commits / PRs` section pre-populated
from the recent git log. Replace the `TODO` and `_None_` placeholders
with the gist's content. Keep it terse — bullets, no prose padding.

If `existing == true`, you're updating in place — read the file first
and merge sections rather than overwriting.

Then write via Bash. Re-pass the same scaffold args so the CLI's
internal scaffolder produces the same path + frontmatter; pipe the
composed body via stdin:

```bash
lore session new \
  --cwd <CWD> \
  --slug <SLUG> \
  --description "<DESC>" \
  --title "<TITLE>" \
  [--target-wiki <WIKI>] \
  [--tags "<a,b,c>"] \
  [--implements "<slug-a,slug-b>"] \
  [--loose-end "<line 1>" --loose-end "<line 2>"] \
  [--project <name>] \
  --body - <<'EOF'
<your composed body markdown>
EOF
```

stdout is the path of the written file (or a JSON envelope with
`--json`). Surface that to the user.

### 4. Commit — one Bash call

```bash
lore session commit <path-printed-by-step-3>
```

stdout is the commit short-sha (or empty if there was nothing new to
commit). **Do not push.**

### 5. Conditional extraction (LLM judgment, only if warranted)

Default `EXTRACT=auto`. Create a concept or decision note only if the
gist explicitly flags a new reusable pattern, architecture, or design
choice with trade-offs that no existing note covers. Verify against
the `lore_search` results from step 2.

For each extraction:
- Write directly to `<scaffold.wiki_path>/{concepts,decisions}/<slug>.md`
- Frontmatter: `schema_version: 2`, inherit `repos:` and `scope:` from
  the session, add bidirectional `[[wikilinks]]`.
- Run `lore session commit <path>` for each new note (the commit
  subcommand handles any path inside a wiki, not only sessions).

Skip extraction entirely if nothing qualifies. A session is one data
point; patterns need repetition before promotion.

## Final report — under 120 words

- Wiki: `<name>` · scope: `<scope>` (source: attach / wiki-default)
- Session note: `<path>` (created / updated)
- Handle: `<handle>` (team mode: yes/no)
- Commit: `<sha>` (or "staged only" / "nothing to commit")
- Extractions: `<list or "none">`
- `implements:`: `<slugs or "none">` — note that curator run needed to
  propagate status flips
- Any follow-ups (ambiguous routing, stale links, unverified
  `implements:` slugs, curator candidates)

## Hard rules

- **Three tool calls is the floor.** MCP scaffold + Bash write + Bash
  commit. Optionally one MCP search. Adding Glob/Read is a regression
  unless you're updating an existing note.
- **Never commit in the work-side repo (the CWD's repo).** Commit only
  in the wiki repo via `lore session commit`.
- **Never run `lore lint` or `lore curator`** from this skill. Mention
  curator candidates in the report.
- **Never prompt for scope.** The scaffolder resolves it.
- **Never use LLM reasoning for fields the scaffolder produces.** Path,
  handle, scope, repos, frontmatter all come from the scaffold result —
  not guessed.
- **Body is the only LLM output.** Plus optionally the prose for one
  extraction note when warranted.

## Skip trivial sessions

If the conversation was a one-shot question or a debug with no lasting
knowledge, tell the user "nothing worth recording" and stop. A session
note has value only when it helps a future session.
