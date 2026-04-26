"""Cluster step — group recent session notes by scope+topic via middle-tier LLM."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable


@dataclass(frozen=True)
class Cluster:
    topic: str                          # short label
    scope: str
    session_notes: list[str]            # wikilinks/paths to source notes
    suggested_surface: str | None       # one of the wiki's surface names, if obvious; else None


def cluster_session_notes(
    *,
    notes: list[dict],                  # each: {"path": str, "frontmatter": dict, "summary": str}
    surfaces: list[str],                # surface names from SURFACES.md (e.g., ["concept", "decision", "result"])
    llm_client: Any,
    model_resolver: Callable[[str], str],
) -> list[Cluster]:
    """Cluster recent session notes by scope+topic via middle-tier LLM.

    Empty `notes` short-circuits to `[]` — no LLM call.
    Caller's responsibility: pre-filter notes to "since last_curator_b".

    Each returned Cluster groups notes likely about the same topic; the
    `suggested_surface` field is the LLM's guess (must be one of
    `surfaces` or None) for the most natural surface to abstract this
    cluster into. Curator B's abstract step (T7) makes the final call.
    """
    if not notes:
        return []
    prompt_text = _build_prompt(notes, surfaces)
    tool = _cluster_tool_schema(surfaces)
    resp = llm_client.messages.create(
        model=model_resolver("middle"),
        max_tokens=2048,
        tools=[tool],
        tool_choice={"type": "tool", "name": "cluster"},
        messages=[{"role": "user", "content": prompt_text}],
    )
    data = _extract_tool_input(resp)
    return _parse_clusters(data, valid_surfaces=set(surfaces))


def _build_prompt(notes: list[dict], surfaces: list[str]) -> str:
    lines = [
        "You are clustering recent session notes for a knowledge-graph "
        "abstraction step. Group notes that are about the same topic "
        "into clusters. Each note may belong to at most one cluster. "
        "Notes about distinct topics get their own cluster.",
        "",
        f"This wiki's surface vocabulary: {surfaces}",
        "For each cluster, optionally suggest the surface name that best fits "
        "(must be one of the vocabulary above, or null if none fits cleanly). "
        "Curator B will make the final extraction decision; this is just a hint.",
        "",
        "Call the `cluster` tool with the list of clusters.",
        "",
        "--- session notes ---",
    ]
    for n in notes:
        fm = n.get("frontmatter", {})
        path = n.get("path", "<unknown>")
        scope = fm.get("scope", "")
        desc = fm.get("description", "")
        summary = n.get("summary", "") or ""
        lines.append(f"- path: {path}")
        if scope:
            lines.append(f"  scope: {scope}")
        if desc:
            lines.append(f"  description: {desc}")
        if summary:
            # Cap summary length so prompt stays bounded.
            short = summary[:300] + ("…" if len(summary) > 300 else "")
            lines.append(f"  summary: {short}")
    return "\n".join(lines)


def _cluster_tool_schema(surfaces: list[str]) -> dict:
    """Tool schema for clustering — JSON returned via tool_use."""
    return {
        "name": "cluster",
        "description": "Emit the clustering as JSON.",
        "input_schema": {
            "type": "object",
            "properties": {
                "clusters": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "topic": {"type": "string"},
                            "scope": {"type": "string"},
                            "session_notes": {
                                "type": "array",
                                "items": {"type": "string"},
                            },
                            "suggested_surface": {
                                "type": ["string", "null"],
                                "enum": [*surfaces, None],
                            },
                        },
                        "required": ["topic", "scope", "session_notes"],
                    },
                }
            },
            "required": ["clusters"],
        },
    }


def _extract_tool_input(resp) -> dict:
    """Pull the cluster tool's input dict from an Anthropic Messages response."""
    for block in getattr(resp, "content", []):
        btype = getattr(block, "type", None) or (block.get("type") if isinstance(block, dict) else None)
        if btype == "tool_use":
            inp = getattr(block, "input", None)
            if inp is None and isinstance(block, dict):
                inp = block.get("input")
            if isinstance(inp, dict):
                return inp
    # Defensive: malformed response → empty cluster list, not crash.
    return {"clusters": []}


def _parse_clusters(data: dict, *, valid_surfaces: set[str]) -> list[Cluster]:
    """Convert raw tool dict → list[Cluster], filtering invalid suggestions."""
    out: list[Cluster] = []
    for raw in data.get("clusters", []) or []:
        if not isinstance(raw, dict):
            continue
        topic = str(raw.get("topic", "")).strip()
        scope = str(raw.get("scope", "")).strip()
        notes = list(raw.get("session_notes") or [])
        if not topic or not notes:
            continue
        suggested = raw.get("suggested_surface")
        if suggested is not None and suggested not in valid_surfaces:
            suggested = None  # silently drop unknown surface suggestions
        out.append(Cluster(
            topic=topic,
            scope=scope,
            session_notes=notes,
            suggested_surface=suggested,
        ))
    return out
