"""Token totals per phase of one Claude Code session transcript.

Reads the transcript JSONL and splits token usage into phases. Never writes.

Rules:

- Each assistant message id counts once. Claude Code writes one line per
  content block and repeats the same ``usage`` on each line.
- A phase starts at an assistant ``tool_use`` block named ``Skill``
  (``skill:<input.skill>``) or ``Task`` / ``Agent``
  (``agent:<input.description or input.subagent_type>``). A user line that
  runs a namespaced slash command (``<command-name>/lore-workflow:build``)
  starts a skill phase too. A built-in command (``/model``) has no colon and
  starts none.
- A message counts in the phase that is active when its first line appears.
  The message that calls a Skill or Agent belongs to the phase before it.
  The phase change applies to the messages after it.
- Tokens before the first phase count as ``main``.
- A line with ``isSidechain: true`` (subagent work) counts in the most recent
  agent phase. It never starts or ends a phase. With no agent phase yet, it
  counts in the active phase.
- When ``<transcript stem>/subagents/agent-*.jsonl`` exists, each subagent file
  counts in the agent row whose tool_use ``id`` equals the ``toolUseId`` in its
  ``agent-*.meta.json``. The main thread then stays in the enclosing phase, and
  an agent row holds only the subagent's own usage. A file with no matching
  tool_use gets its own row, ``agent:<meta description>``. Agents spawned by a
  subagent get their own rows the same way.
- Each phase start makes a new row, even when a name repeats.
- Each row also splits its tokens by the model that served each message
  (``message.model``; ``?`` when the line carries none). An agent row keeps
  the ``model`` and ``effort`` its spawn asked for, so a reader can compare
  the request with the model that answered.
- Stage and target (see :mod:`lore_core.stages`). An agent row takes its
  stage from a tag at the start of its description or prompt. A session
  whose first user prompt opens with a tag (a teammate) gives that stage to
  every row except tagged or untagged agent rows of its own. The target is
  the epic or issue: a tag's reference, else the last reference in a skill's
  arguments, which stays in effect until the next one.
"""

from __future__ import annotations

import glob
import json
import re
from dataclasses import dataclass, field
from pathlib import Path

from lore_core.stages import parse_ref, parse_tag

_AGENT_TOOLS = {"Task", "Agent"}
# Claude Code writes this as the model of messages it makes up itself
# (an interrupted turn, an API error); they carry no billed tokens.
_SYNTHETIC = "<synthetic>"


@dataclass
class PhaseTokens:
    name: str
    messages: int = 0
    input: int = 0
    output: int = 0
    cache_read: int = 0
    cache_write: int = 0
    # The part of ``cache_write`` that went to the 1-hour cache, which bills
    # higher than the 5-minute one.
    cache_write_1h: int = 0
    models: dict[str, PhaseTokens] = field(default_factory=dict)
    requested_model: str | None = None
    requested_effort: str | None = None
    stage: str | None = None
    target: str | None = None

    @property
    def total(self) -> int:
        return self.input + self.output + self.cache_read + self.cache_write

    def counts(self) -> dict:
        return {
            "messages": self.messages,
            "input": self.input,
            "output": self.output,
            "cache_read": self.cache_read,
            "cache_write": self.cache_write,
            "cache_write_1h": self.cache_write_1h,
            "total": self.total,
        }

    def as_dict(self) -> dict:
        data = {"name": self.name, **self.counts()}
        data["models"] = {m: u.counts() for m, u in self.models.items()}
        if self.requested_model or self.requested_effort:
            data["requested"] = {"model": self.requested_model, "effort": self.requested_effort}
        for key in ("stage", "target"):
            if getattr(self, key):
                data[key] = getattr(self, key)
        return data

    @property
    def model_mismatch(self) -> bool:
        """True when the spawn asked for a model and none of its messages ran on it."""
        if not self.requested_model:
            return False
        served = [m for m in self.models if m != "?"]
        return bool(served) and not any(model_matches(self.requested_model, m) for m in served)


def model_matches(requested: str, served: str) -> bool:
    """Whether ``served`` is the model a spawn's ``requested`` value names.

    A spawn may name a full id (``claude-sonnet-5-5``) or an alias
    (``sonnet``); a served id may carry a suffix (``[1m]``, a date).
    ``inherit`` asks for the session's model, which a subagent file alone
    cannot confirm, so it always matches.
    """
    want, got = requested.strip().lower(), served.strip().lower()
    if want == "inherit":
        return True
    if want.startswith("claude-"):
        return is_variant_of(got, want)
    return want in got


def is_variant_of(model: str, family: str) -> bool:
    """``model`` is ``family`` itself, or ``family`` plus a date or a ``[...]`` suffix.

    ``claude-sonnet-5`` is not a variant of ``claude-sonnet-5-5``'s family,
    nor the other way round.
    """
    if not model.startswith(family):
        return False
    rest = model.split("[", 1)[0].removeprefix(family)
    return rest == "" or bool(re.fullmatch(r"-\d{8}", rest))


def _phase_name(block: dict) -> str | None:
    if block.get("type") != "tool_use":
        return None
    tool, inp = block.get("name"), block.get("input") or {}
    if tool == "Skill":
        return f"skill:{inp.get('skill', '?')}"
    if tool in _AGENT_TOOLS:
        return f"agent:{inp.get('description') or inp.get('subagent_type') or '?'}"
    return None


def _int(value: object) -> int:
    return value if isinstance(value, int) else 0


def _lines(path: Path):
    for raw in Path(path).read_text(encoding="utf-8").splitlines():
        try:
            entry = json.loads(raw)
        except json.JSONDecodeError:
            continue
        if isinstance(entry, dict) and isinstance(entry.get("message"), dict):
            yield entry, entry["message"]


def _entries(path: Path):
    for entry, message in _lines(path):
        if entry.get("type") == "assistant":
            yield entry, message


_COMMAND = re.compile(r"<command-name>/?([^<\s]+)</command-name>")
_COMMAND_ARGS = re.compile(r"<command-args>(.*?)</command-args>", re.S)


def _user_text(message: dict) -> str:
    content = message.get("content")
    if isinstance(content, str):
        return content
    texts = [b.get("text", "") for b in _blocks(message) if b.get("type") == "text"]
    return "\n".join(t for t in texts if isinstance(t, str))


def _command(text: str) -> tuple[str, str] | None:
    """``(skill, args)`` when ``text`` runs a namespaced slash command."""
    match = _COMMAND.search(text)
    if not match or ":" not in match.group(1):
        return None
    args = _COMMAND_ARGS.search(text)
    return match.group(1), args.group(1) if args else ""


def _blocks(message: dict):
    content = message.get("content")
    return [b for b in content if isinstance(b, dict)] if isinstance(content, list) else []


def _count(target: PhaseTokens, usage: dict) -> None:
    target.messages += 1
    target.input += _int(usage.get("input_tokens"))
    target.output += _int(usage.get("output_tokens"))
    target.cache_read += _int(usage.get("cache_read_input_tokens"))
    target.cache_write += _int(usage.get("cache_creation_input_tokens"))
    creation = usage.get("cache_creation")
    if isinstance(creation, dict):
        target.cache_write_1h += _int(creation.get("ephemeral_1h_input_tokens"))


def _add(target: PhaseTokens, message: dict) -> None:
    usage = message["usage"]
    _count(target, usage)
    model = message.get("model") if isinstance(message.get("model"), str) else "?"
    if model != _SYNTHETIC:
        _count(target.models.setdefault(model, PhaseTokens(model)), usage)


def _agent_row(name: str, block: dict, target: str | None) -> PhaseTokens:
    inp = block.get("input") or {}
    row = PhaseTokens(name)
    for attr, key in (("requested_model", "model"), ("requested_effort", "effort")):
        if isinstance(inp.get(key), str) and inp[key].strip():
            setattr(row, attr, inp[key].strip())
    stage, ref = parse_tag(inp.get("description"))
    if stage is None:
        stage, ref = parse_tag(inp.get("prompt"))
    row.stage, row.target = stage, ref or target
    return row


def _subagent_files(path: Path) -> list[Path]:
    return sorted((Path(path).parent / Path(path).stem / "subagents").glob("agent-*.jsonl"))


def _meta(agent_file: Path) -> dict:
    try:
        return json.loads(agent_file.with_suffix(".meta.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def trace_tokens(path: Path) -> list[PhaseTokens]:
    """Phases of the transcript at ``path``, in order of appearance."""
    sub_files = _subagent_files(path)
    phases = [PhaseTokens("main")]
    current = phases[0]
    last_agent: PhaseTokens | None = None
    seen: set[str] = set()
    by_tool_use: dict[str, PhaseTokens] = {}
    session_stage: str | None = None
    target: str | None = None
    first_prompt = True

    def skill_row(name: str, args: object) -> PhaseTokens:
        nonlocal target
        target = parse_ref(args) or target
        return PhaseTokens(name, stage=session_stage, target=target)

    for entry, message in _lines(path):
        sidechain = bool(entry.get("isSidechain"))
        if entry.get("type") == "user":
            if sidechain or entry.get("isMeta"):
                continue
            text = _user_text(message)
            if first_prompt and text.strip():
                first_prompt = False
                session_stage, ref = parse_tag(text)
                target = ref
                phases[0].stage, phases[0].target = session_stage, ref
            command = _command(text)
            if command:
                current = skill_row(f"skill:{command[0]}", command[1])
                phases.append(current)
            continue
        if entry.get("type") != "assistant":
            continue
        mid = message.get("id")
        usage = message.get("usage")
        if isinstance(usage, dict) and (mid is None or mid not in seen):
            if mid is not None:
                seen.add(mid)
            _add(last_agent if sidechain and last_agent else current, message)

        if sidechain:
            continue
        for block in _blocks(message):
            name = _phase_name(block)
            if not name:
                continue
            if name.startswith("agent:"):
                row = _agent_row(name, block, target)
            else:
                row = skill_row(name, (block.get("input") or {}).get("args"))
            phases.append(row)
            if name.startswith("agent:"):
                by_tool_use[block.get("id", "")] = row
                last_agent = row
                if sub_files:
                    # Subagent usage lives in its own file: the main thread
                    # stays in the enclosing phase.
                    continue
            current = row

    # Nested spawns: register their rows first, so file order does not matter.
    parsed = []
    nested: list[tuple[PhaseTokens, int]] = []  # (row, index of the file that spawned it)
    for f in sub_files:
        meta = _meta(f)
        entries = list(_entries(f))
        for _, message in entries:
            for block in _blocks(message):
                name = _phase_name(block)
                if name and name.startswith("agent:") and block.get("id") not in by_tool_use:
                    row = _agent_row(name, block, None)
                    phases.append(row)
                    by_tool_use[block["id"]] = row
                    nested.append((row, len(parsed)))
        row = by_tool_use.get(meta.get("toolUseId", ""))
        if row is None:
            row = PhaseTokens(f"agent:{meta.get('description') or f.stem}", target=target)
            phases.append(row)
        parsed.append((row, entries))
    # A nested spawn without its own reference works for its parent's target.
    for _ in range(len(nested)):
        for row, index in nested:
            row.target = row.target or parsed[index][0].target

    for row, entries in parsed:
        for _, message in entries:
            mid, usage = message.get("id"), message.get("usage")
            if isinstance(usage, dict) and (mid is None or mid not in seen):
                if mid is not None:
                    seen.add(mid)
                _add(row, message)
    return phases


def find_transcript(lore_root: Path | None, session: str) -> Path | None:
    """Resolve a direct .jsonl path or a session id to a transcript file.

    A session id is looked up in the transcript ledger first, then as
    ``~/.claude/projects/*/<session>.jsonl``.
    """
    direct = Path(session).expanduser()
    if session.endswith(".jsonl") and direct.is_file():
        return direct
    if lore_root is not None:
        from lore_core.ledger import TranscriptLedger

        for entry in TranscriptLedger(lore_root).all_entries():
            if entry.transcript_id == session and entry.path.is_file():
                return entry.path
    for hit in sorted(
        (Path.home() / ".claude" / "projects").glob(f"*/{glob.escape(session)}.jsonl")
    ):
        return hit
    return None
