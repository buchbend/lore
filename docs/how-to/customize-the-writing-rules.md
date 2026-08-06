# Customize the writing rules

**Goal:** replace the writing rules, the prose style agents write issue and PR
text against, with your team's own — and, if you lint, replace the Vale rules
that check them.

Lore ships a default document as package data. A wiki overrides it with one
file. Resolution is whole-file: your file wins entirely, or the packaged
default does. There is no merge and no per-repo layer.

## Before you start

- `lore` installed and the repo `lore attach`ed to a wiki.
- Vale on `PATH`, only if you want the lint step. `lore doctor` reports whether
  Vale is present. Its absence never blocks; the rules still apply as
  instructions.

## Steps

1. **Read the rules that apply today.**

   ```
   lore style show writing-rules
   ```

   Without an override the command prints the packaged default. The command
   always prints something, so an agent always has a style to follow.

2. **Copy it into your wiki and edit it.**

   ```
   lore style show writing-rules > "$LORE_ROOT/wiki/<name>/style/writing-rules.md"
   ```

   Create the `style/` directory first if it does not exist. Edit the copy.
   Re-run `lore style show writing-rules` from a repo attached to that wiki
   and confirm your text comes back.

3. **Override the Vale rules, if you lint.**

   The Vale config resolves the same way:

   ```
   lore style vale-config --packaged
   ```

   Copy the whole packaged `styles/vale/` directory, not the `vale.ini` alone:

   ```
   cp -r "$(dirname "$(lore style vale-config --packaged)")" "$LORE_ROOT/wiki/<name>/style/vale"
   ```

   The ini sets `StylesPath = .`, so Vale looks for the rule directory next to
   the ini it loaded. An ini copied without its `WritingRules/` directory
   fails with exit code 2 and `style 'WritingRules' does not exist on
   StylesPath`.

   `--packaged` is what makes the copy safe. Without it the command prints a
   generated copy carrying the current repository's glossary. Every repository
   attached to your wiki would then lint against that one glossary. Step 4
   drops the option, because linting is where you want the glossary.

4. **Check the rules fire.**

   ```
   vale --config "$(lore style vale-config)" <file>.md
   ```

   Exit code 1 means error-level findings. Exit code 0 with printed output
   means warning-level heuristics, which are advisory. Exit code 2 means the
   invocation itself is broken.

   Give the file a `.md` extension. The packaged config scopes its rules to
   `[*.md]`, so a `.txt` or extensionless file reports `0 files` and exits 0
   without checking anything.

5. **Expect the short-name check to fire, and read it as advice.**

   The packaged config carries `WritingRules.UnknownShortName = NO`, so the
   short-name check is off until a glossary switches it on. `lore style
   vale-config` looks for a `CONTEXT.md` in the current repo. Where the
   command finds one, it copies the whole config directory to the host cache
   under `$LORE_CACHE/vale/`, writes the glossary terms to `glossary.txt`
   beside the rules, and flips the switch line to `YES`. The printed path is
   the cached copy. Nothing lands in your repo.

   Rule 20 fires often on a first run. Lore's own `CONTEXT.md` yields 85
   terms, and linting issue 339's body against them reported 8 warnings and 0
   errors, exit code 0. Seven were rule 20: `ADR`, `PRD`, `AFK`, `Repo`,
   `Dev` and `checkability`. Several findings per issue body is the normal
   result, not a broken setup.

   Read the findings and fix the text, or leave the word and move on. The
   check never blocks: every finding is a warning, and `file-issue` reads
   Vale by exit code.

## Notes

- The banned-word list lives in the rules text and in the Vale style. A
  test asserts the two agree, so edit both together.
- `RetiredHeading.yml` names the headings Lore's templates dropped. A draft
  written from a stale template passes every prose rule, so the heading is the
  only evidence the template moved. Retire a heading, add it to that rule in
  the same change.
- Overriding the rules replaces the whole document, including its section
  skeleton and EARS patterns. Start from the packaged copy rather than a blank
  file unless you intend to drop those.
- Wikis are portable. The override travels with the wiki repo, so a team that
  takes its wiki elsewhere keeps its rules.
- The short-name check reads each repository's own `CONTEXT.md`. Your override
  sets the rules. The repository you lint from sets the terms. A repository
  without a `CONTEXT.md` runs no short-name check at all.
- `lore style show issue-register` still resolves the same document and names
  the retired term on stderr. A wiki that overrode `style/issue-register.md`
  renames that file to `style/writing-rules.md`.

## Related

- [Write a good fast-path issue](write-a-fast-path-issue.md)
- [Why the writing rules are a document, not config](../explanation/why-the-writing-rules.md)
