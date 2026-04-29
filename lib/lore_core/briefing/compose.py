"""LLM-composed briefing prose.

The LLM reads the gathered session summaries and produces a full
briefing in the structured shape the `/lore:briefing` skill used to
hand-author:

    ## Briefing: <today> (<wiki>)

    ### What happened
    - **<project>**: <summary>

    ### Key decisions
    - <decision and why>

    ### Open items
    - <item>

    ### Vault health
    - <N notes covered, M decisions, K open items>

The deterministic bullet-list render in :mod:`format` is the *fallback*:
when no LLM client is available, the model errors, or the user passes
``--no-llm``, the CLI publishes the deterministic version instead.
Briefings should always publish; the LLM is an enhancement, not a gate.
"""

from __future__ import annotations

from typing import Any, Callable

_PROMPT_HEADER = (
    "You are composing a developer briefing — a concise digest of recent "
    "work for a teammate catching up. Read the session summaries below "
    "and produce a structured briefing in markdown.\n"
    "\n"
    "Required shape (no preamble, no quotes, no code fences — just the "
    "markdown):\n"
    "\n"
    "    ## Briefing: <today> (<wiki>)\n"
    "\n"
    "    ### What happened\n"
    "    - **<project or theme>**: <one-line summary; multiple sessions "
    "may aggregate into one bullet>\n"
    "\n"
    "    ### Key decisions\n"
    "    - <decision and the *why*>\n"
    "\n"
    "    ### Open items\n"
    "    - <unresolved thread / loose end>\n"
    "\n"
    "    ### Vault health\n"
    "    - <N notes since last briefing, K decisions, M open items>\n"
    "\n"
    "Rules:\n"
    "- Group by project / theme, not chronologically.\n"
    "- Deduplicate overlapping work across sessions.\n"
    "- Keep the whole briefing ≤30 lines regardless of input size.\n"
    "- Omit a section entirely if its bullets would be empty (e.g. no "
    "decisions, no open items). Always include 'What happened' and "
    "'Vault health'.\n"
    "- Cite session slugs in parentheses when helpful "
    "(e.g. '(see fix-auth-redirect)').\n"
)

_MAX_SESSIONS_IN_PROMPT = 60
_SUMMARY_CAP = 600


def _shorten(text: str, cap: int) -> str:
    text = text.strip()
    if len(text) <= cap:
        return text
    return text[:cap].rstrip() + "…"


def _build_prompt(gather_result: dict[str, Any]) -> str:
    sessions = gather_result.get("new_sessions") or []
    wiki = gather_result.get("wiki", "")
    today = gather_result.get("today", "")
    last = (gather_result.get("ledger") or {}).get("last_briefing")
    since = last or "(start of vault)"

    lines = [
        _PROMPT_HEADER,
        "",
        f"Wiki: {wiki}",
        f"Today: {today}",
        f"Last briefing: {since}",
        f"New sessions: {len(sessions)}",
        "",
        "--- session summaries (most recent first) ---",
    ]
    capped = sorted(
        sessions, key=lambda s: s.get("date", ""), reverse=True
    )[:_MAX_SESSIONS_IN_PROMPT]
    for s in capped:
        d = s.get("date", "")
        slug = s.get("slug", "")
        fm = s.get("frontmatter") or {}
        summary = fm.get("summary") or fm.get("description") or ""
        sections = s.get("sections") or {}
        worked = sections.get("what we worked on", "")
        decisions = sections.get("decisions made", "")
        lines.append(f"- {d} · {slug}")
        if summary:
            lines.append(f"  summary: {_shorten(str(summary), _SUMMARY_CAP)}")
        if worked:
            lines.append(f"  what: {_shorten(worked, _SUMMARY_CAP)}")
        if decisions and decisions.strip().lower() not in {"_none_", "none", ""}:
            lines.append(f"  decisions: {_shorten(decisions, _SUMMARY_CAP)}")
    if len(sessions) > _MAX_SESSIONS_IN_PROMPT:
        lines.append(
            f"(+{len(sessions) - _MAX_SESSIONS_IN_PROMPT} older sessions omitted)"
        )
    return "\n".join(lines)


def _extract_text(resp: Any) -> str:
    """Pull plain text from an Anthropic-style messages.create response."""
    content = getattr(resp, "content", None)
    if content is None and isinstance(resp, dict):
        content = resp.get("content")
    if not content:
        return ""
    out: list[str] = []
    for block in content:
        text = getattr(block, "text", None)
        if text is None and isinstance(block, dict):
            text = block.get("text")
        if isinstance(text, str):
            out.append(text)
    return "".join(out).strip()


def compose_briefing_prose(
    *,
    gather_result: dict[str, Any],
    llm_client: Any,
    model_resolver: Callable[[str], str],
    tier: str = "middle",
    max_tokens: int = 2048,
) -> str:
    """Return a full structured briefing markdown, or "" on no input.

    Raises whatever the underlying LLM client raises — caller decides
    whether to swallow and fall back to the deterministic render.
    Empty ``new_sessions`` short-circuits to ``""`` without calling
    the model.
    """
    sessions = gather_result.get("new_sessions") or []
    if not sessions:
        return ""
    prompt = _build_prompt(gather_result)
    resp = llm_client.messages.create(
        model=model_resolver(tier),
        max_tokens=max_tokens,
        messages=[{"role": "user", "content": prompt}],
    )
    return _extract_text(resp)
