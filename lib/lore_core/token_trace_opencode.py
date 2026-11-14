"""Token totals per phase of one OpenCode session file (PRD 0022).

The session file is the one the Lore OpenCode plugin writes (see
:mod:`lore_adapters.opencode`). Each assistant message carries its token
counts (``input``, ``output``, ``reasoning``, ``cache.read``, ``cache.write``)
and its cost, so nothing is estimated here.

Phases: ``main``, then one ``agent:<description>`` row per ``subagent`` tool
call. The row takes its stage and target from a tag at the start of the call's
``description`` or ``prompt``. The subagent's own tokens come from its session
file in the same folder (header ``parentID`` = this session), matched to the
call by the call's prompt, which the subagent's first user message contains.
A subagent file no call claims gets a row of its own.
"""

from __future__ import annotations

from pathlib import Path

from lore_core.stages import parse_tag
from lore_core.token_trace import PhaseTokens, _int


def is_opencode_session(path: Path) -> bool:
    try:
        with Path(path).open(encoding="utf-8", errors="replace") as fp:
            return '"host":"opencode"' in fp.readline().replace(" ", "")
    except OSError:
        return False


def _count(target: PhaseTokens, msg: dict) -> None:
    tokens = msg["tokens"]
    cache = tokens.get("cache") if isinstance(tokens.get("cache"), dict) else {}
    target.messages += 1
    target.input += _int(tokens.get("input"))
    target.output += _int(tokens.get("output"))
    target.reasoning += _int(tokens.get("reasoning"))
    target.cache_read += _int(cache.get("read"))
    target.cache_write += _int(cache.get("write"))
    cost = msg.get("cost")
    if isinstance(cost, (int, float)):
        target.cost = (target.cost or 0.0) + cost


def _add(target: PhaseTokens, msg: dict, seen: set[str]) -> None:
    mid = msg.get("id")
    if not isinstance(msg.get("tokens"), dict) or (mid is not None and mid in seen):
        return
    if mid is not None:
        seen.add(mid)
    _count(target, msg)
    ref = msg.get("model")
    model = ref.get("id") if isinstance(ref, dict) and isinstance(ref.get("id"), str) else "?"
    _count(target.models.setdefault(model, PhaseTokens(model)), msg)


def _first_user_text(messages: list[dict]) -> str:
    return next((m.get("text") or "" for m in messages if m.get("type") == "user"), "")


def trace_opencode(path: Path) -> list[PhaseTokens]:
    from lore_adapters.opencode import read_session

    header, messages = read_session(path)
    main_id = header.get("sessionID")
    # ponytail: direct children only; a subagent's own subagents are not traced.
    children = []
    for other in sorted(Path(path).parent.glob("*.jsonl")):
        child_header, child_messages = read_session(other)
        if main_id and child_header.get("parentID") == main_id:
            children.append(child_messages)

    phases = [PhaseTokens("main")]
    seen: set[str] = set()
    called: set[object] = set()  # a repeated message must not repeat its spawns
    rows: list[tuple[PhaseTokens, str]] = []
    for msg in messages:
        if msg.get("type") == "user" and phases[0].stage is None and not rows:
            phases[0].stage, phases[0].target = parse_tag(msg.get("text"))
        _add(phases[0], msg, seen)
        if msg.get("type") != "assistant":
            continue
        for part in msg.get("content") or []:
            if not (isinstance(part, dict) and part.get("type") == "tool"):
                continue
            if part.get("name") != "subagent" or part.get("id") in called:
                continue
            called.add(part.get("id"))
            state = part.get("state") if isinstance(part.get("state"), dict) else {}
            inp = state.get("input") if isinstance(state.get("input"), dict) else {}
            name = inp.get("description") or inp.get("agent") or "?"
            row = PhaseTokens(f"agent:{name}")
            stage, ref = parse_tag(inp.get("description"))
            if stage is None:
                stage, ref = parse_tag(inp.get("prompt"))
            row.stage, row.target = stage, ref or phases[0].target
            phases.append(row)
            rows.append((row, str(inp.get("prompt") or "").strip()))

    for child in children:
        text = _first_user_text(child)
        row = next((r for r, prompt in rows if prompt and prompt in text and r.messages == 0), None)
        if row is None:
            row = PhaseTokens("agent:subagent", target=phases[0].target)
            phases.append(row)
        for msg in child:
            _add(row, msg, seen)
    return phases
