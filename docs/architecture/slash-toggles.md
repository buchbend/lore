# Slash Toggles — vocabulary and semantics

**Audience:** contributors touching `/lore:on`, `/lore:off`, the
inline-citation directive, or the hook short-circuit path.

This document settles **P3.3** from the 2026-04-27 multi-agent review.
Previous state: four slash commands (`/lore:on`, `/lore:off`,
`/lore:loud`, `/lore:quiet`) whose SKILL.md files described behavior
the codebase didn't actually implement.

## Vocabulary (settled)

Two scoped commands, default scope `all`:

| Command | Scope | Effect |
|---|---|---|
| `/lore:off` | `all` (default) | Mute every Lore touchpoint for this session. |
| `/lore:off citations` | `citations` | Suppress only inline `› consulted [[note]]` affordances. Hooks + MCP still fire. |
| `/lore:on` | `all` (default) | Re-enable everything that `/lore:off` muted. |
| `/lore:on citations` | `citations` | Re-enable inline citations only. |

**Retired:** `/lore:loud`, `/lore:quiet`. Kept as deprecated aliases
for one minor release with a banner pointing at the new spelling, then
removed.

**Why not `/lore:mute` / `/lore:hush`:** "mute" implies audio /
messages only; the master switch turns off retrieval, not just chatter.
"On" / "off" matches what the toggle actually controls.

## Semantics — what `off all` means

The user mandate is **security-first**: when a user runs `/lore:off`
they should be able to trust that nothing Lore-related touches the
context. So `off all` mutes:

- **Hooks** — `SessionStart`, `PreCompact`, `Stop`, `UserPromptSubmit`
  short-circuit and emit nothing.
- **MCP retrieval** — every `lore_*` tool call returns a sentinel
  refusal envelope rather than vault content.
- **Inline citations** — the citation directive is suppressed (no
  `› consulted` lines).

This is a stronger semantic than the previous SKILL.md doc, which
explicitly carved MCP out of the mute. The change is intentional: the
old carve-out left a path for vault content to reach the model after
the user said "off," which defeats the security posture.

`off citations` is the narrower scope — hooks and MCP keep working,
only the inline affordance is silenced.

## Implementation contract

### State

Per-session sentinel files under `$TMPDIR`:

- `$TMPDIR/lore-off-<sid>` — present iff `off all` is active for `<sid>`
- `$TMPDIR/lore-off-citations-<sid>` — present iff `off citations` is active for `<sid>`

`<sid>` is the canonical Claude Code session id, the same value
`_read_hook_payload` republishes as `CLAUDE_SESSION_ID` (see v0.13.1).
File presence is the only state — no JSON body, no timestamps.
Sentinels live in `$TMPDIR` so the OS reaps them at session boundary
without us having to track lifetimes.

### Helpers (`lib/lore_core/toggles.py`)

```python
def is_off(scope: str, sid: str) -> bool: ...
def set_off(scope: str, sid: str) -> None: ...
def clear_off(scope: str, sid: str) -> None: ...
```

`scope` is one of `"all"` or `"citations"`. Helpers are pure
side-effect functions, no I/O beyond the sentinel file.

### Check sites

| Site | Check | On true |
|---|---|---|
| Hook entry (`cmd_session_start`, `cmd_pre_compact`, `cmd_stop`, `cmd_user_prompt_submit`) | `is_off("all", sid)` after `_read_hook_payload` | Return without emitting. |
| Capture entry (`capture` — bound to SessionEnd + SessionStart/PreCompact capture) | `is_off("all", sid)` after `_read_hook_payload` | Return — no transcript ingestion, no curator spawn, no ledger write. |
| MCP `_dispatch` | `is_off("all", env CLAUDE_SESSION_ID)` | Return `_mcp_error("session_off", ...)`. |
| SessionStart additionalContext builder + UserPromptSubmit heartbeat envelope | `is_off("citations", sid)` | Inject "Citations are silenced — do not emit `› consulted [[X]]`" line. |

The capture entry guard is what makes `off all` *security-honest*: SessionEnd
is the only hook bound to the capture pipeline, so without this guard a muted
session could still spawn a curator that writes vault notes the next session
would surface. The UserPromptSubmit citation injection is what makes
`off citations` take effect *immediately* mid-session rather than at the next
SessionStart.

### CLI seam

Two new `lore` verbs, mounted via the typer-app pattern (see
`cli-contract.md`):

- `lore on [scope]` — `clear_off(scope, sid)`
- `lore off [scope]` — `set_off(scope, sid)`

`<scope>` defaults to `all`. `<sid>` resolves via `CLAUDE_SESSION_ID`
env var (the v0.13.1 source of truth). When the env var is missing
(running outside a Claude Code session) the commands print a clear
error and exit non-zero — there's no point muting nothing.

### Skill bodies

`/lore:off` and `/lore:on` SKILL.md files invoke the CLI verb:

```bash
lore off "$ARG"   # or "all" if no arg
```

The SKILL frontmatter declares `allowed-tools: Bash(lore *)` so the
agent can run it without a permission prompt.

## Known limitations

- **Windows.** Sentinel paths use `$TMPDIR` with a `/tmp` fallback,
  matching POSIX conventions. Issue #8 already tracks broader Windows
  support — this feature inherits that limitation. Tests for the
  toggle helpers are POSIX-only.
- **Long-lived MCP server reuse across sessions.** The MCP `_dispatch`
  reads `CLAUDE_SESSION_ID` from the process environment, set by the
  Claude Code parent at child-spawn time. If a host reuses one
  long-running MCP server across multiple Claude Code sessions, the
  sid is whichever session started the server — not the current
  caller. Stdio-mode MCP per Claude Code instance is the documented
  shape and stays correct; HTTP/shared deployments would need
  per-call sid plumbing through the MCP request envelope.

## Rollout

Two commits inside the work branch:

1. **Honest implementation under current four-skill names.** Wire
   sentinel write/check across hooks + MCP + citation directive.
   Leave `/lore:on`, `/lore:off`, `/lore:loud`, `/lore:quiet` SKILL.md
   files in place, pointing at the new CLI verbs. Closes the UX
   honesty bug independently.
2. **Vocabulary collapse.** Add the `[scope]` arg, retire `/lore:loud`
   and `/lore:quiet` (alias commits with deprecation banner for one
   release, then delete).

Reviewable separately. If commit 2 bikesheds we can still ship 1.
