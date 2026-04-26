"""VSCode Copilot Chat JSONL adapter.

Reads GitHub Copilot Chat sessions stored per-workspace by the VSCode
extension ``github.copilot-chat``:

    Linux:   ~/.config/Code/User/workspaceStorage/<hash>/chatSessions/<session>.jsonl
    macOS:   ~/Library/Application Support/Code/User/workspaceStorage/<hash>/chatSessions/<session>.jsonl
    Windows: %APPDATA%\\Code\\User\\workspaceStorage\\<hash>\\chatSessions\\<session>.jsonl

Cursor is VSCode-family so the same extension in Cursor writes to the
mirror path under ``~/.config/Cursor/User/``. This adapter handles both
by probing all standard VSCode-family user-data dirs.

File format (reverse-engineered; Microsoft does not publicly document it,
but ``microsoft/vscode-copilot-chat`` is MIT-licensed — ChatSessionStore
implementation is the authoritative source):

- Line 1: ``{"kind": 0, "v": {...full snapshot...}}``
  where ``v`` contains ``version: 3, sessionId, requests: [...], ...``.
- Subsequent lines: ``{"kind": 1|2, "k": ["key","path"], "v": value}``
  — mutation patches. Walk ``k`` to set the value.

Each ``v.requests[i]`` carries ``message`` (the user prompt) and
``response`` (the assistant reply), plus metadata.

The adapter reconstructs the final state by applying patches in order,
then emits one Turn per request message and one per response.

Version-pin: ``v.version`` is currently ``3``. Format broke once in
Jan 2026 (JSON → JSONL + iterator protocol). Unknown versions are
rendered with best-effort + integration_extras.
"""

from __future__ import annotations

import json
import os
import sys
from collections.abc import Iterator
from copy import deepcopy
from datetime import UTC, datetime
from pathlib import Path

from lore_core.types import TranscriptHandle, Turn


_SUPPORTED_VERSIONS = (3,)


def _vscode_family_user_dirs() -> list[Path]:
    """Return candidate VSCode-family User directories to probe.

    Each entry points at ``<user-data>/User/`` — the parent of
    ``workspaceStorage`` and ``globalStorage``.
    """
    home = Path.home()
    if sys.platform == "darwin":
        base = home / "Library" / "Application Support"
    elif sys.platform.startswith("win"):
        appdata = os.environ.get("APPDATA")
        base = Path(appdata) if appdata else home / "AppData" / "Roaming"
    else:
        base = Path(os.environ.get("XDG_CONFIG_HOME") or home / ".config")

    candidates = [
        base / "Code" / "User",            # VSCode stable
        base / "Code - Insiders" / "User", # VSCode Insiders
        base / "Cursor" / "User",          # Cursor (VSCode fork)
        base / "VSCodium" / "User",        # VSCodium
    ]
    return [c for c in candidates if c.is_dir()]


def _workspace_hash_for_path(cwd: Path, user_dir: Path) -> str | None:
    """Reverse a workspace directory to its MD5 hash by reading each
    workspaceStorage/<hash>/workspace.json for a matching folder URI.
    """
    storage = user_dir / "workspaceStorage"
    if not storage.is_dir():
        return None
    target_uri = f"file://{cwd.resolve()}"
    for ws in storage.iterdir():
        meta = ws / "workspace.json"
        if not meta.is_file():
            continue
        try:
            data = json.loads(meta.read_text())
        except (OSError, json.JSONDecodeError):
            continue
        folder = data.get("folder")
        if folder == target_uri:
            return ws.name
    return None


def _apply_patch(state: dict, path: list, value) -> dict:
    """Apply a Copilot mutation patch. ``path`` is a list of keys; walk
    to the parent, then set the last key / index.
    """
    if not path:
        return state
    # Work on a deep copy so the caller can see the un-mutated snapshot
    # if desired; patches land on the copy.
    current = state
    for key in path[:-1]:
        if isinstance(current, list):
            try:
                current = current[int(key)]
            except (IndexError, ValueError, TypeError):
                return state
        elif isinstance(current, dict):
            if key not in current:
                # Create intermediate dict so later keys land somewhere.
                current[key] = {}
            current = current[key]
        else:
            return state
    last = path[-1]
    if isinstance(current, list):
        try:
            idx = int(last)
            while len(current) <= idx:
                current.append(None)
            current[idx] = value
        except (ValueError, TypeError):
            pass
    elif isinstance(current, dict):
        current[last] = value
    return state


def _replay_jsonl(path: Path) -> dict | None:
    """Parse a chatSessions JSONL file → reconstructed final state dict.

    Returns None if the file has no kind:0 base snapshot or is unreadable.
    """
    try:
        text = path.read_text(errors="replace")
    except OSError:
        return None

    state: dict | None = None
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        try:
            rec = json.loads(stripped)
        except json.JSONDecodeError:
            continue
        if not isinstance(rec, dict):
            continue
        kind = rec.get("kind")
        if kind == 0:
            v = rec.get("v")
            if isinstance(v, dict):
                state = deepcopy(v)
            continue
        if kind in (1, 2) and state is not None:
            k = rec.get("k")
            v = rec.get("v")
            if isinstance(k, list):
                _apply_patch(state, k, v)
    return state


class VSCodeCopilotAdapter:
    """Adapter for GitHub Copilot Chat sessions (VSCode / Cursor / Insiders)."""

    integration = "copilot"

    def list_transcripts(self, directory: Path) -> list[TranscriptHandle]:
        cwd = Path(directory).resolve()
        out: list[TranscriptHandle] = []
        for user_dir in _vscode_family_user_dirs():
            ws_hash = _workspace_hash_for_path(cwd, user_dir)
            if ws_hash is None:
                continue
            sessions_dir = user_dir / "workspaceStorage" / ws_hash / "chatSessions"
            if not sessions_dir.is_dir():
                continue
            for jsonl in sorted(sessions_dir.glob("*.jsonl")):
                try:
                    mtime = datetime.fromtimestamp(jsonl.stat().st_mtime, tz=UTC)
                except OSError:
                    continue
                out.append(
                    TranscriptHandle(
                        integration=self.integration,
                        id=jsonl.stem,
                        path=jsonl,
                        cwd=cwd,
                        mtime=mtime,
                    )
                )
        return out

    def read_slice(
        self, handle: TranscriptHandle, from_index: int = 0
    ) -> Iterator[Turn]:
        for turn in self._iter_turns(handle):
            if turn.index >= from_index:
                yield turn

    def read_slice_after_hash(
        self,
        handle: TranscriptHandle,
        after_hash: str | None,
        index_hint: int | None = None,
    ) -> Iterator[Turn]:
        all_turns = list(self._iter_turns(handle))
        if after_hash is None:
            yield from all_turns
            return
        if index_hint is not None and 0 <= index_hint < len(all_turns):
            if all_turns[index_hint].content_hash() == after_hash:
                yield from all_turns[index_hint + 1:]
                return
        for i, t in enumerate(all_turns):
            if t.content_hash() == after_hash:
                yield from all_turns[i + 1:]
                return
        yield from all_turns

    def is_complete(self, handle: TranscriptHandle) -> bool:
        try:
            return any(True for _ in self._iter_turns(handle))
        except Exception:
            return False

    def _iter_turns(self, handle: TranscriptHandle) -> Iterator[Turn]:
        state = _replay_jsonl(handle.path)
        if not state:
            return
        version = state.get("version")
        extras_common: dict = {}
        if version not in _SUPPORTED_VERSIONS:
            extras_common["copilot.unsupported_version"] = version

        requests = state.get("requests") or []
        if not isinstance(requests, list):
            return

        index = 0
        for req in requests:
            if not isinstance(req, dict):
                continue
            ts = _parse_epoch_ms(req.get("timestamp"))
            message = req.get("message")
            response = req.get("response")

            user_text = _extract_text(message)
            if user_text is not None:
                yield Turn(
                    index=index, timestamp=ts, role="user",
                    text=user_text,
                    integration_extras={**extras_common,
                                 "copilot.request_id": req.get("requestId"),
                                 "copilot.agent": req.get("agent")},
                )
                index += 1

            asst_text = _extract_text(response)
            if asst_text is not None:
                yield Turn(
                    index=index, timestamp=ts, role="assistant",
                    text=asst_text,
                    integration_extras={**extras_common,
                                 "copilot.response_id": req.get("responseId"),
                                 "copilot.model_id": req.get("modelId")},
                )
                index += 1


def _parse_epoch_ms(raw) -> datetime | None:
    if raw is None:
        return None
    if isinstance(raw, (int, float)):
        if raw > 1e12:
            return datetime.fromtimestamp(raw / 1000, tz=UTC)
        return datetime.fromtimestamp(raw, tz=UTC)
    return None


def _extract_text(node) -> str | None:
    """Pull a human-readable text representation from a message/response node.

    Copilot's message format nests text under various shapes. Try a few,
    fall back to None if nothing extractable.
    """
    if node is None:
        return None
    if isinstance(node, str):
        return node or None
    if isinstance(node, dict):
        # Common shape: {"parts": [{"kind":"text","text":"..."}]}
        parts = node.get("parts")
        if isinstance(parts, list):
            texts = [
                p.get("text", "") for p in parts
                if isinstance(p, dict) and p.get("kind") in ("text", "markdownContent", "markdown")
            ]
            if texts:
                return "\n".join(t for t in texts if t)
        # Alternative: {"text": "..."}
        if "text" in node and isinstance(node["text"], str):
            return node["text"] or None
        # Alternative: {"content": "..."} or {"value": "..."}
        for key in ("content", "value", "markdown"):
            v = node.get(key)
            if isinstance(v, str) and v:
                return v
    if isinstance(node, list):
        parts = [_extract_text(n) for n in node]
        combined = "\n".join(p for p in parts if p)
        return combined or None
    return None
