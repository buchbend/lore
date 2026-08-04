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
   lore style vale-config
   ```

   Copy the whole packaged `styles/vale/` directory, not the `vale.ini` alone:

   ```
   cp -r "$(dirname "$(lore style vale-config)")" "$LORE_ROOT/wiki/<name>/style/vale"
   ```

   The ini sets `StylesPath = .`, so Vale looks for the rule directory next to
   the ini it loaded. An ini copied without its `WritingRules/` directory
   fails with exit code 2 and `style 'WritingRules' does not exist on
   StylesPath`.

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

## Notes

- The banned-word list lives in the rules text and in the Vale style. A
  test asserts the two agree, so edit both together.
- Overriding the rules replaces the whole document, including its section
  skeleton and EARS patterns. Start from the packaged copy rather than a blank
  file unless you intend to drop those.
- Wikis are portable. The override travels with the wiki repo, so a team that
  takes its wiki elsewhere keeps its rules.
- `lore style show issue-register` still resolves the same document and names
  the retired term on stderr. A wiki that overrode `style/issue-register.md`
  renames that file to `style/writing-rules.md`.

## Related

- [Write a good fast-path issue](write-a-fast-path-issue.md)
- [Why the writing rules are a document, not config](../explanation/why-the-writing-rules.md)
