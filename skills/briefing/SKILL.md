---
name: lore:briefing
description: Publish a developer briefing for a wiki — gather new sessions
  since the last briefing, render them, publish via the wiki's configured
  sink (Matrix, markdown, ...), and update the ledger. Thin wrapper around
  `lore briefing --wiki <name>`. Run with "/lore:briefing <wiki>".
user_invocable: true
---

# Developer Briefing

Thin wrapper around the `lore briefing` CLI: gather + render + publish +
mark, all in one shot. The CLI reads `<wiki>/.lore-briefing.yml` for the
sink and uses the briefing ledger to determine what's new.

## Usage

```bash
lore briefing --wiki <wiki>
```

If the user invoked `/lore:briefing` without a wiki name, ask which
wiki. Otherwise pass the argument straight through.

By default the CLI uses the configured LLM backend (auto-detect:
subscription `claude` binary → `ANTHROPIC_API_KEY` SDK → openai-compatible
endpoint configured in `.lore/config.yml`) to compose the briefing in the
structured shape (`### What happened` / `### Key decisions` / `### Open
items` / `### Vault health`). When no backend is available or the call
fails, it falls back to a deterministic bullet-list render so briefings
always publish.

## Useful flags

- `--dry-run` — render and print to stdout, no publish, no ledger
  write. Use this to preview before sending.
- `--no-llm` — skip the LLM composer; publish the deterministic
  bullet-list digest directly. Useful when the LLM backend is down
  or the user explicitly wants the raw shape.
- `--since YYYY-MM-DD` — override the ledger floor (e.g. to re-emit
  the last week without resetting the ledger).
- `--sink <uri>` — override the configured sink (e.g.
  `markdown:/tmp/preview.md` for a one-off file dump).
- `--no-mark` — publish without recording in the ledger (useful for
  testing or republishing).

## Hard rules

- **One wiki per briefing.** Different wikis have different audiences.
- **Sinkless wikis fail loudly.** The CLI errors if no `sink:` is set
  in `.lore-briefing.yml` and no `--sink` was passed. Don't paper over
  that — surface the error so the user can configure the sink.
- **Credentials never enter the wiki repo.** Non-secret config (room
  IDs, homeserver URLs, output paths) lives in `.lore-briefing.yml`.
  Secrets stay external (e.g. matrix access tokens at
  `~/.local/share/lore/matrix-credentials.json`).

## When you need to compose prose yourself

The default flow already invokes an LLM inside the CLI. If the user
wants *you* (the in-conversation model) to compose the prose — e.g. to
hand-tune the wording before publishing — use the multi-step path:

```
lore briefing gather --wiki <name>     # JSON envelope (your input)
lore briefing publish --sink <uri> --wiki <name> --file <prose.md>
lore briefing mark --wiki <name> --session <path> [...]
```

Reserve this for explicit "let me hand-author the briefing" requests.
The default `/lore:briefing <wiki>` flow trusts the CLI's LLM.

## Related

- `/lore:context` — what SessionStart cached
- `/lore:resume` — fresh gather (different shape: per-topic / per-scope)
