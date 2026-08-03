# Customize the issue register

**Goal:** replace the issue register, the prose style agents write issue and PR
text against, with your team's own — and, if you lint, replace the Vale rules
that check it.

Lore ships a default register as package data. A wiki overrides it with one
file. Resolution is whole-file: your file wins entirely, or the packaged
default does. There is no merge and no per-repo layer.

## Before you start

- `lore` installed and the repo `lore attach`ed to a wiki.
- Vale on `PATH`, only if you want the lint step. `lore doctor` reports whether
  Vale is present. Its absence never blocks; the register still applies as
  instructions.

## Steps

1. **Read the register that applies today.**

   ```
   lore style show issue-register
   ```

   Without an override the command prints the packaged default. The command
   always prints something, so an agent always has a style to follow.

2. **Copy it into your wiki and edit it.**

   ```
   lore style show issue-register > "$LORE_ROOT/wiki/<name>/style/issue-register.md"
   ```

   Create the `style/` directory first if it does not exist. Edit the copy.
   Re-run `lore style show issue-register` from a repo attached to that wiki
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
   the ini it loaded. An ini copied without its `IssueRegister/` directory
   fails with exit code 2 and `style 'IssueRegister' does not exist on
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

- The banned-word list lives in the register text and in the Vale style. A
  test asserts the two agree, so edit both together.
- Overriding the register replaces the whole document, including its section
  skeleton and EARS patterns. Start from the packaged copy rather than a blank
  file unless you intend to drop those.
- Wikis are portable. The override travels with the wiki repo, so a team that
  takes its wiki elsewhere keeps its register.

## Related

- [Write a good fast-path issue](write-a-fast-path-issue.md)
- [Why the issue register is a document, not config](../explanation/why-the-issue-register.md)
