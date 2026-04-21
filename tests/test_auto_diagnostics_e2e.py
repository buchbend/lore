"""End-to-end: hook → curator → run-log → lore runs show."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest
from typer.testing import CliRunner

from lore_adapters import register
from lore_adapters.registry import _REGISTRY
from lore_core.ledger import TranscriptLedger, TranscriptLedgerEntry
from lore_core.types import TranscriptHandle, Turn


# ---------------------------------------------------------------------------
# Fake adapter (follows contract from test_hooks_capture.py + test_curator_a.py)
# ---------------------------------------------------------------------------

_NOW = datetime(2026, 4, 20, 10, 0, 0, tzinfo=UTC)

FAKE_TURNS = [
    Turn(index=0, timestamp=_NOW, role="user", text="hi, let's work on Zarr chunking"),
    Turn(index=1, timestamp=_NOW, role="assistant", text="substantive decision about Zarr chunking strategy"),
    Turn(index=2, timestamp=_NOW, role="user", text="looks good, let's commit"),
    Turn(index=3, timestamp=_NOW, role="assistant", text="committed with optimal chunk layout"),
]


class _FakeE2EAdapter:
    host = "fake"

    def __init__(self, handle: TranscriptHandle) -> None:
        self._handle = handle

    def list_transcripts(self, directory: Path) -> list[TranscriptHandle]:
        return [self._handle]

    def read_slice(self, handle: TranscriptHandle, from_index: int = 0):
        yield from (t for t in FAKE_TURNS if t.index >= from_index)

    def read_slice_after_hash(self, handle: TranscriptHandle, after_hash, index_hint=None):
        if after_hash is None:
            yield from FAKE_TURNS
            return
        for i, t in enumerate(FAKE_TURNS):
            if t.content_hash() == after_hash:
                yield from FAKE_TURNS[i + 1:]
                return
        yield from []

    def is_complete(self, handle: TranscriptHandle) -> bool:
        return True


# ---------------------------------------------------------------------------
# Fake Anthropic client (returns noteworthy=True with full classify payload)
# ---------------------------------------------------------------------------


class _FakeBlock:
    def __init__(self, type_: str, input_: dict | None = None, text: str | None = None) -> None:
        self.type = type_
        self.input = input_
        self.text = text


class _FakeResp:
    def __init__(self, content: list) -> None:
        self.content = content


class _FakeMessagesAPI:
    def create(self, **kwargs):
        tc = kwargs.get("tool_choice", {})
        name = tc.get("name") if isinstance(tc, dict) else None
        if name == "merge_judgment":
            data = {"new": True}
        else:
            data = {
                "noteworthy": True,
                "reason": "substantive Zarr chunking decision",
                "title": "Zarr Chunking Strategy",
                "bullets": ["decided optimal chunk layout", "committed changes"],
                "files_touched": [],
                "entities": [],
                "decisions": ["use chunk size 512 for optimal IO"],
            }
        return _FakeResp([_FakeBlock(type_="tool_use", input_=data)])


class FakeAnthropicNoteworthyTrue:
    def __init__(self) -> None:
        self.messages = _FakeMessagesAPI()


# ---------------------------------------------------------------------------
# Test
# ---------------------------------------------------------------------------


def test_e2e_capture_to_runs_show(tmp_path: Path, monkeypatch) -> None:
    """Fire a hook + run curator synchronously + assert `lore runs show latest` works."""

    # ------------------------------------------------------------------
    # Step 1 — set up attached wiki + project directory
    # ------------------------------------------------------------------
    wiki_name = "testwiki"
    lore_root = tmp_path
    wiki_dir = lore_root / "wiki" / wiki_name
    (wiki_dir / "sessions").mkdir(parents=True)
    # P2 per-wiki threshold — single-transcript e2e needs threshold=1.
    (wiki_dir / ".lore-wiki.yml").write_text("curator:\n  threshold_pending: 1\n")

    cwd = lore_root / "project"
    cwd.mkdir()
    (cwd / "CLAUDE.md").write_text(
        "# Project\n\n"
        f"## Lore\n\n"
        f"- wiki: {wiki_name}\n"
        f"- scope: proj:e2e\n"
        f"- backend: none\n"
    )

    monkeypatch.setenv("LORE_ROOT", str(lore_root))

    # ------------------------------------------------------------------
    # Step 2 — build fake adapter and register it
    # ------------------------------------------------------------------
    transcript_path = cwd / "t-e2e-001.jsonl"
    transcript_path.write_text("{}")

    handle = TranscriptHandle(
        host="fake",
        id="t-e2e-001",
        path=transcript_path,
        cwd=cwd,
        mtime=_NOW,
    )

    fake_adapter = _FakeE2EAdapter(handle)
    register(fake_adapter)

    try:
        # ------------------------------------------------------------------
        # Step 3 — suppress detached spawn and fire the hook
        # ------------------------------------------------------------------
        from lore_cli import hooks as hooks_mod

        monkeypatch.setattr(hooks_mod, "_spawn_detached_curator_a", lambda *a, **kw: None)

        # Pass transcript=None explicitly: capture() is a typer command so its
        # defaults are OptionInfo objects, not plain Python None. Omitting
        # `transcript` causes the filter `h.path == <OptionInfo>` to drop
        # every handle, resulting in an empty ledger write.
        hooks_mod.capture(event="session-end", cwd_override=cwd, host="fake", transcript=None)

        # ------------------------------------------------------------------
        # Step 4 — assert hook-events.jsonl contains the session-end record
        # ------------------------------------------------------------------
        events_path = lore_root / ".lore" / "hook-events.jsonl"
        assert events_path.exists(), "hook-events.jsonl should be created"
        event_records = [json.loads(line) for line in events_path.read_text().splitlines()]
        assert any(r["event"] == "session-end" for r in event_records), (
            f"No session-end record found; got: {event_records}"
        )

        # ------------------------------------------------------------------
        # Step 5 — synchronously run curator_a with fake LLM
        # ------------------------------------------------------------------
        from lore_curator.curator_a import run_curator_a

        def _adapter_lookup(host: str):
            if host == "fake":
                return fake_adapter
            raise KeyError(f"unknown host: {host!r}")

        run_curator_a(
            lore_root=lore_root,
            anthropic_client=FakeAnthropicNoteworthyTrue(),
            adapter_lookup=_adapter_lookup,
            trigger="manual",
            now=_NOW,
        )

        # ------------------------------------------------------------------
        # Step 6 — assert runs/<id>.jsonl exists with run-start / run-end
        # ------------------------------------------------------------------
        runs_dir = lore_root / ".lore" / "runs"
        run_files = [
            p for p in runs_dir.glob("*.jsonl")
            if not p.name.endswith(".trace.jsonl")
        ]
        assert len(run_files) == 1, f"Expected 1 run file, got {[p.name for p in run_files]}"

        records = [json.loads(line) for line in run_files[0].read_text().splitlines()]
        types = [r["type"] for r in records]
        assert types[0] == "run-start", f"First record type: {types[0]}"
        assert types[-1] == "run-end", f"Last record type: {types[-1]}"

        # Also assert that a session note was filed
        session_notes = list((wiki_dir / "sessions").glob("*.md"))
        assert len(session_notes) == 1, (
            f"Expected 1 session note, got {[p.name for p in session_notes]}"
        )

        # ------------------------------------------------------------------
        # Step 7 — invoke `lore runs show latest` via CliRunner
        # ------------------------------------------------------------------
        from lore_cli import runs_cmd

        monkeypatch.setattr(runs_cmd, "_get_lore_root", lambda: lore_root)

        runner = CliRunner()
        result = runner.invoke(runs_cmd.app, ["show", "latest"])
        assert result.exit_code == 0, f"lore runs show latest failed:\n{result.output}"
        # Output should mention the run; basic sanity check
        assert "trigger" in result.output.lower() or "start" in result.output.lower(), (
            f"Expected 'trigger' or 'start' in output:\n{result.output}"
        )

    finally:
        # Clean up registered fake adapter
        _REGISTRY.pop("fake", None)
