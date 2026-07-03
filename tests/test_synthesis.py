"""Tests for lore_curator.synthesis — two-phase flush worker.

NOTE: this module targets the pre-P2 work/discussion-gated schema
(``BULLET_CAPS``, ``_phase2_prompt``, ``_phase2_tool_schema``,
``_coerce_title_for_shape``, ``NarrativeShape``). The Curator A
revamp on branch ``pr/p2-style`` ports synthesis to the two-call P2
shape (experiment 005 best GPT-OSS cell); none of the imported
symbols exist anymore. The replacement coverage lives in
``tests/test_synthesis_p2.py`` (P2 schemas + prompts + two-call
``compose_session_note``).

Skipped at the module level until the legacy assertions are excised
or rewritten — a follow-up PR can either delete this file outright
or rewrite the high-value scenarios against the P2 contract.
"""
from __future__ import annotations

import pytest

pytest.skip(
    "Pre-P2 synthesis tests — replaced by tests/test_synthesis_p2.py. "
    "See branch pr/p2-style.",
    allow_module_level=True,
)


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------


class _FakeContentBlock:
    def __init__(self, type_: str, input_: dict | None = None):
        self.type = type_
        self.input = input_ or {}


class _FakeResponse:
    def __init__(self, content: list):
        self.content = content


class _FakeMessagesAPI:
    def __init__(self, responder):
        self._responder = responder
        self.calls: list[dict] = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        return self._responder(kwargs)


class _FakeLlmClient:
    def __init__(self, responder):
        self.messages = _FakeMessagesAPI(responder)


def _ok_responder(composed: dict[str, Any]):
    def _r(_kwargs):
        return _FakeResponse([_FakeContentBlock("tool_use", composed)])
    return _r


def _err_responder():
    def _r(_kwargs):
        raise RuntimeError("LLM down")
    return _r


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _make_scope() -> Scope:
    return Scope(
        wiki="private",
        scope="proj:feature",
        backend="none",
        claude_md_path=Path("/tmp/CLAUDE.md"),
    )


def _make_handle() -> TranscriptHandle:
    return TranscriptHandle(
        integration="claude-code",
        id="transcript-X",
        path=Path("/tmp/t.jsonl"),
        cwd=Path("/tmp"),
        mtime=datetime.now(UTC),
    )


def _make_turns(n: int = 2) -> list[Turn]:
    return [
        Turn(index=i, timestamp=None, role="user" if i % 2 == 0 else "assistant", text=f"msg-{i}")
        for i in range(n)
    ]


@pytest.fixture
def lore_root(tmp_path: Path) -> Path:
    (tmp_path / ".lore" / "buffers").mkdir(parents=True)
    (tmp_path / "wiki" / "private").mkdir(parents=True)
    return tmp_path


@pytest.fixture
def patch_collectors(monkeypatch):
    # Seed *some* commit signal so the Phase 2 empty-signal guard
    # doesn't kick in for tests that exercise the LLM compose path.
    # The guard skips the LLM when turns_text is empty AND there are no
    # commits / plans / projects to anchor on — without this the unit
    # tests would (rightly) be considered too thin to flush.
    from lore_curator.session_activity import CommitRef

    monkeypatch.setattr(
        "lore_curator.session_activity.collect_commits_by_sha",
        lambda *a, **kw: [
            CommitRef(short_hash="abc1234", subject="fix: seeded commit", branch="main", repo="x"),
        ],
    )
    monkeypatch.setattr("lore_curator.session_activity.collect_issues_in_window", lambda *a, **kw: ([], []))
    monkeypatch.setattr("lore_curator.session_activity.collect_projects_for_session", lambda **kw: [])
    monkeypatch.setattr("lore_core.git.git_repo_root", lambda cwd: None)
    monkeypatch.setattr("lore_core.git.current_repo", lambda cwd: "")


def _seed_stub(lore_root: Path, monkeypatch, *, files=None, transcript_id: str = "abc") -> tuple:
    """Run one heartbeat + write_or_update; return (buffer, stub_path, sidecar_path)."""
    files = files if files is not None else ["/repo/auth.py"]
    monkeypatch.setattr(
        "lore_curator.buffer_append._files_touched_from_turns",
        lambda turns: list(files),
    )
    monkeypatch.setattr(
        "lore_curator.buffer_append._files_modified_from_turns",
        lambda turns: list(files),
    )
    # Synthesis recomputes files_modified at flush time from the
    # transcript turns the adapter returns. The test fixture's
    # ``_make_turns`` produces text-only turns (no tool_calls), so the
    # honest re-read yields ``[]`` — which would force discussion shape
    # for every Phase-2 test. Patch the flush-side helper to mirror
    # the buffer-side ``files`` so these tests keep exercising
    # work-shape behaviour. Tests that explicitly want discussion shape
    # override this monkeypatch.
    monkeypatch.setattr(
        "lore_curator.synthesis._files_modified_from_turns",
        lambda turns: list(files),
    )
    work_time = datetime(2026, 5, 1, 14, 32, tzinfo=UTC)
    outcome = append_chunk(
        lore_root=lore_root, chunk_turns=_make_turns(2), local_date="2026-05-01",
        transcript_id=transcript_id, integration="claude-code", wiki="private", scope="proj:feature",
        cwd=lore_root, wiki_root=lore_root / "wiki" / "private", cfg=WikiConfig(),
    )
    write_or_update(
        outcome=outcome, scope=_make_scope(), transcript=_make_handle(),
        wiki_root=lore_root / "wiki" / "private",
        work_time=work_time, now=work_time, integration="claude-code",
        chunk_from_hash="h0", chunk_to_hash="h1",
    )
    return outcome.buffer, Path(outcome.buffer.read_sidecar().stub_path), outcome.buffer.sidecar_path


# ---------------------------------------------------------------------------
# Phase 1
# ---------------------------------------------------------------------------


def test_phase1_drops_stub_marker_and_closes_buffer(lore_root, patch_collectors, monkeypatch):
    buffer, stub_path, sidecar_path = _seed_stub(lore_root, monkeypatch)

    outcome = synth_and_close(
        sidecar_path,
        lore_root=lore_root,
        wiki_root=lore_root / "wiki" / "private",
        llm_client=None,
        model=None,
    )
    assert outcome.phase1_completed is True
    assert outcome.phase2_completed is False
    # The on-disk file is the deterministic Phase 1 note.
    fm = parse_frontmatter(stub_path.read_text())
    assert "state" not in fm
    # The buffer's live sidecar is gone — moved to _done/.
    assert not sidecar_path.exists()
    moved = lore_root / ".lore" / "buffers" / "_done" / sidecar_path.name
    assert moved.exists()


def test_close_collision_emits_warning_and_preserves_archive(
    lore_root, patch_collectors, monkeypatch
):
    """When ``_done/<stem>.state.json`` already exists at archive time,
    ``synth_and_close`` must not crash, must leave the pre-existing archived
    sidecar byte-for-byte unchanged, and must emit a warning through the
    curator logger so the failure is visible in diagnostics. Issue #54.
    """
    buffer, _stub_path, sidecar_path = _seed_stub(lore_root, monkeypatch)

    # Pre-seed a _done/ archive sidecar with the same stem, simulating
    # an upstream part-resolution misfire that opened a duplicate Part-1
    # buffer for an already-archived (transcript_id, local_date) pair.
    done = lore_root / ".lore" / "buffers" / "_done"
    done.mkdir(parents=True, exist_ok=True)
    pre_existing = done / sidecar_path.name
    pre_existing.write_text('{"sentinel": "untouched"}')
    pre_existing_bytes = pre_existing.read_bytes()

    class _RecordingLogger:
        def __init__(self):
            self.records: list[tuple[str, dict]] = []

        def emit(self, record_type: str, **fields):
            self.records.append((record_type, fields))

    logger = _RecordingLogger()

    # Must not raise — the curator continues after a collision.
    outcome = synth_and_close(
        sidecar_path,
        lore_root=lore_root,
        wiki_root=lore_root / "wiki" / "private",
        llm_client=None,
        model=None,
        logger=logger,
    )

    # Pre-existing archived sidecar is byte-for-byte unchanged.
    assert pre_existing.read_bytes() == pre_existing_bytes

    # A warning record carrying the colliding stem made it into the trace.
    warnings = [
        (rt, fields) for (rt, fields) in logger.records if rt == "warning"
    ]
    assert warnings, f"expected a warning record, got: {logger.records}"
    assert any(
        fields.get("stem") == buffer.stem
        and fields.get("reason") == "done-archive-collision"
        for (_rt, fields) in warnings
    ), f"no done-archive-collision warning for stem {buffer.stem!r}: {warnings}"

    # Phase 1 still ran (note on disk); the buffer just couldn't archive.
    assert outcome.phase1_completed is True


def test_phase1_idempotent_on_already_closed(lore_root, patch_collectors, monkeypatch):
    _, _, sidecar_path = _seed_stub(lore_root, monkeypatch)

    synth_and_close(
        sidecar_path,
        lore_root=lore_root,
        wiki_root=lore_root / "wiki" / "private",
    )
    # Second invocation -- sidecar moved to _done already; passing the
    # original path should short-circuit cleanly.
    outcome = synth_and_close(
        sidecar_path,
        lore_root=lore_root,
        wiki_root=lore_root / "wiki" / "private",
    )
    assert outcome.skipped_reason in ("no-sidecar", "already-closed")


def test_phase1_handles_missing_stub_gracefully(lore_root, patch_collectors, monkeypatch):
    buffer, stub_path, sidecar_path = _seed_stub(lore_root, monkeypatch)
    stub_path.unlink()

    outcome = synth_and_close(
        sidecar_path,
        lore_root=lore_root,
        wiki_root=lore_root / "wiki" / "private",
    )
    assert outcome.phase1_completed is False
    # Buffer still gets closed -- handover gates on state, not stub presence.
    moved = lore_root / ".lore" / "buffers" / "_done" / sidecar_path.name
    assert moved.exists()


# ---------------------------------------------------------------------------
# Phase 2
# ---------------------------------------------------------------------------


def test_phase2_rewrites_title_and_summary(lore_root, patch_collectors, monkeypatch):
    _, stub_path, sidecar_path = _seed_stub(lore_root, monkeypatch)

    composed = {
        "title": "auth handler refactor",
        "description": "Rebuilt auth.py to align with the new policy decorator.",
        "summary_lede": "We pulled the legacy callbacks out and slotted a tidy decorator chain in their place.",
        "adr_candidates": [{
            "choice": "decorator chain over callbacks",
            "rationale": "decorator chain is explicit and testable",
            "evidence": "user confirmed at turn 4",
            "alternative_rejected": "legacy callback pattern",
        }],
        "worked_on": ["**auth.py** — pulled callbacks", "**tests** — green"],
        "loose_ends": ["**docs** — the migration note remained unwritten"],
    }
    llm = _FakeLlmClient(_ok_responder(composed))

    outcome = synth_and_close(
        sidecar_path,
        lore_root=lore_root,
        wiki_root=lore_root / "wiki" / "private",
        llm_client=llm,
        model="m",
    )
    assert outcome.phase2_completed is True
    assert outcome.phase2_attempts == 1
    # Phase 2 may rename the stub from the deterministic basename slug
    # ("auth") to one derived from the synthesised title.
    final_path = outcome.stub_path
    assert final_path is not None
    assert final_path.exists()
    fm = parse_frontmatter(final_path.read_text())
    assert fm["title"] == "auth handler refactor"
    text = final_path.read_text()
    assert "## Summary" in text
    assert "## ADR candidates" in text
    assert "## What we worked on" in text
    assert "## Loose ends" in text


def test_phase2_retry_then_succeed(lore_root, patch_collectors, monkeypatch):
    _, stub_path, sidecar_path = _seed_stub(lore_root, monkeypatch)

    composed_ok = {
        "title": "title", "description": "desc", "summary_lede": "sum",
    }

    state = {"calls": 0}

    def _flaky(_kw):
        state["calls"] += 1
        if state["calls"] < 3:
            raise RuntimeError("transient")
        return _FakeResponse([_FakeContentBlock("tool_use", composed_ok)])

    llm = _FakeLlmClient(_flaky)
    outcome = synth_and_close(
        sidecar_path,
        lore_root=lore_root,
        wiki_root=lore_root / "wiki" / "private",
        llm_client=llm,
        model="m",
    )
    assert outcome.phase2_completed is True
    assert outcome.phase2_attempts == 3


def test_phase2_exhausts_and_degrades(lore_root, patch_collectors, monkeypatch):
    _, stub_path, sidecar_path = _seed_stub(lore_root, monkeypatch)

    llm = _FakeLlmClient(_err_responder())
    outcome = synth_and_close(
        sidecar_path,
        lore_root=lore_root,
        wiki_root=lore_root / "wiki" / "private",
        llm_client=llm,
        model="m",
    )
    assert outcome.degraded is True
    assert outcome.phase2_completed is False
    assert outcome.phase2_attempts == 3
    # Stub is still the deterministic Activity-only note.
    fm = parse_frontmatter(stub_path.read_text())
    assert "state" not in fm  # state:stub dropped by Phase 1


def test_phase2_truncates_overlong_bullets(lore_root, patch_collectors, monkeypatch):
    _, stub_path, sidecar_path = _seed_stub(lore_root, monkeypatch)

    def _adr(i: int) -> dict:
        return {
            "choice": f"choice-{i}",
            "rationale": "some reason",
            "evidence": "user confirmed",
            "alternative_rejected": "the other way",
        }

    composed = {
        "title": "t", "description": "d", "summary_lede": "s",
        # More candidates than cap — only BULLET_CAPS["adr_candidates"] should survive.
        "adr_candidates": [_adr(i) for i in range(BULLET_CAPS["adr_candidates"] + 5)],
        "worked_on": ["X" * (BULLET_LINE_MAX + 50)],
        "loose_ends": [],
    }
    llm = _FakeLlmClient(_ok_responder(composed))
    outcome = synth_and_close(
        sidecar_path,
        lore_root=lore_root,
        wiki_root=lore_root / "wiki" / "private",
        llm_client=llm,
        model="m",
    )
    text = outcome.stub_path.read_text()
    # Only the cap'd count of ADR candidate top-level bullets.
    candidate_count = text.count("- **choice-")
    assert candidate_count == BULLET_CAPS["adr_candidates"]
    # Worked-on line truncated.
    for line in text.splitlines():
        if line.startswith("- ") and not line.startswith("- **"):
            assert len(line) <= BULLET_LINE_MAX + 2  # "- " prefix + capped content


def test_phase2_skipped_without_llm_client(lore_root, patch_collectors, monkeypatch):
    _, stub_path, sidecar_path = _seed_stub(lore_root, monkeypatch)

    outcome = synth_and_close(
        sidecar_path,
        lore_root=lore_root,
        wiki_root=lore_root / "wiki" / "private",
        llm_client=None,
        model=None,
    )
    assert outcome.phase1_completed is True
    assert outcome.phase2_completed is False
    assert outcome.degraded is False


# ---------------------------------------------------------------------------
# Phase 2 rename — slug-from-title takes over from the deterministic stub
# ---------------------------------------------------------------------------


def test_phase2_renames_stub_to_slug_from_title(lore_root, patch_collectors, monkeypatch):
    _, stub_path, sidecar_path = _seed_stub(lore_root, monkeypatch)
    composed = {
        "title": "auth handler refactor",
        "description": "d", "summary_lede": "s",
    }
    llm = _FakeLlmClient(_ok_responder(composed))

    outcome = synth_and_close(
        sidecar_path,
        lore_root=lore_root,
        wiki_root=lore_root / "wiki" / "private",
        llm_client=llm,
        model="m",
    )
    assert outcome.phase2_completed is True
    assert outcome.stub_path is not None
    assert outcome.stub_path.name == "01-1432-auth-handler-refactor.md"
    assert outcome.wikilink == "[[01-1432-auth-handler-refactor]]"
    assert not stub_path.exists()  # old slug gone
    fm = parse_frontmatter(outcome.stub_path.read_text())
    # The aliased old stem must be the full filename stem (with date /
    # time prefix), so existing [[<old-stem>]] wikilinks still resolve.
    assert stub_path.stem in (fm.get("aliases") or [])


def test_phase2_skips_rename_when_slug_equals_existing(lore_root, patch_collectors, monkeypatch):
    # Seed a stub whose existing slug already matches the title-derived slug.
    # ``patch_collectors`` seeds a commit "fix: seeded commit" → slug
    # "fix-seeded-commit". A title that hashes to the same slug should
    # leave the path alone.
    _, stub_path, sidecar_path = _seed_stub(lore_root, monkeypatch)
    composed = {"title": "fix seeded commit", "description": "d", "summary_lede": "s"}
    llm = _FakeLlmClient(_ok_responder(composed))
    outcome = synth_and_close(
        sidecar_path,
        lore_root=lore_root,
        wiki_root=lore_root / "wiki" / "private",
        llm_client=llm,
        model="m",
    )
    assert outcome.phase2_completed is True
    assert outcome.stub_path == stub_path  # same slug, no rename


def test_phase2_skipped_when_signal_is_empty(lore_root, monkeypatch):
    """No turns_text and no commits / plans / projects → skip the LLM.

    Why: mid-tier models confabulate confidently when the prompt is just
    boilerplate + a comma-joined files-touched list. We'd rather keep
    the deterministic Phase 1 stub than fabricate a plausible-looking
    fictional narrative.
    """
    # Replicate ``patch_collectors`` minus the commit seed — leaves
    # commits / plans / projects empty.
    monkeypatch.setattr("lore_curator.session_activity.collect_commits_by_sha", lambda *a, **kw: [])
    monkeypatch.setattr("lore_curator.session_activity.collect_issues_in_window", lambda *a, **kw: ([], []))
    monkeypatch.setattr("lore_curator.session_activity.collect_projects_for_session", lambda **kw: [])
    monkeypatch.setattr("lore_core.git.git_repo_root", lambda cwd: None)
    monkeypatch.setattr("lore_core.git.current_repo", lambda cwd: "")

    _, stub_path, sidecar_path = _seed_stub(lore_root, monkeypatch)
    composed = {"title": "fabricated", "description": "d", "summary_lede": "s"}
    fake = _FakeLlmClient(_ok_responder(composed))

    outcome = synth_and_close(
        sidecar_path,
        lore_root=lore_root,
        wiki_root=lore_root / "wiki" / "private",
        llm_client=fake,
        model="m",
    )
    assert outcome.phase1_completed is True
    assert outcome.phase2_completed is False
    assert outcome.degraded is True
    # The LLM was NEVER called — confirms the guard fired.
    assert fake.messages.calls == []


def test_phase2_rename_avoids_collision(lore_root, patch_collectors, monkeypatch):
    _, stub_path, sidecar_path = _seed_stub(lore_root, monkeypatch)
    # Place a pre-existing note at the target name so the rename has to
    # bump the collision counter.
    target_dir = stub_path.parent
    pre_existing = target_dir / "01-1432-auth-handler-refactor.md"
    pre_existing.write_text("---\ntype: session\n---\nsomeone else\n")

    composed = {"title": "auth handler refactor", "description": "d", "summary_lede": "s"}
    llm = _FakeLlmClient(_ok_responder(composed))
    outcome = synth_and_close(
        sidecar_path,
        lore_root=lore_root,
        wiki_root=lore_root / "wiki" / "private",
        llm_client=llm,
        model="m",
    )
    assert outcome.phase2_completed is True
    assert outcome.stub_path == target_dir / "01-1432-auth-handler-refactor-2.md"
    # Pre-existing collision sibling untouched.
    assert pre_existing.exists()
    assert "someone else" in pre_existing.read_text()


# ---------------------------------------------------------------------------
# Phase 2 — narrative-shape gating (step-4 of yes-do-that-keen-yeti).
# Schema becomes a function of NarrativeShape; the work shape preserves
# the existing fields, the discussion shape strips ``decisions[]`` and
# ``worked_on[]`` entirely and adds ``discussion[]``. additionalProperties
# is locked to false on every variant.
# ---------------------------------------------------------------------------


def _make_shape(*, has_edits: bool, decisions_allowed: bool | None = None,
                no_edit_intent: bool = False, adr_flagged: bool = False) -> NarrativeShape:
    """NarrativeShape constructor with sane defaults for Phase-2 tests."""
    if decisions_allowed is None:
        decisions_allowed = has_edits
    return NarrativeShape(
        has_edits=has_edits,
        decisions_allowed=decisions_allowed,
        no_edit_intent=no_edit_intent,
        adr_flagged=adr_flagged,
    )


def test_phase2_schema_work_shape_has_adr_candidates_and_worked_on():
    schema = _phase2_tool_schema(_make_shape(has_edits=True))
    props = schema["input_schema"]["properties"]
    assert "adr_candidates" in props
    assert "worked_on" in props
    assert "loose_ends" in props
    # discussion is the discussion-shape companion — absent in work shape.
    assert "discussion" not in props
    # decisions (old field) is gone — replaced by the four-field adr_candidates.
    assert "decisions" not in props
    assert schema["input_schema"]["additionalProperties"] is False


def test_phase2_schema_discussion_shape_has_adr_candidates_no_worked_on():
    schema = _phase2_tool_schema(_make_shape(has_edits=False))
    props = schema["input_schema"]["properties"]
    assert "discussion" in props
    assert "loose_ends" in props
    # adr_candidates IS in discussion shape — real forks happen in talk sessions.
    assert "adr_candidates" in props
    # Structural gate — these fields are NOT advertised, so the LLM has
    # no slot to emit them. ``additionalProperties: false`` is the
    # belt-and-braces.
    assert "decisions" not in props
    assert "worked_on" not in props
    assert schema["input_schema"]["additionalProperties"] is False


def test_phase2_schema_adr_candidates_in_both_shapes():
    """``adr_candidates`` is present in BOTH work and discussion schemas —
    real architectural forks happen in pure-talk sessions too."""
    work = _phase2_tool_schema(_make_shape(has_edits=True))
    disc = _phase2_tool_schema(_make_shape(has_edits=False))
    assert "adr_candidates" in work["input_schema"]["properties"]
    assert "adr_candidates" in disc["input_schema"]["properties"]


def test_phase2_schema_default_shape_preserves_work_shape_behavior():
    """``shape=None`` (no migration yet) yields the work-shape schema —
    so test fixtures and any caller that hasn't migrated keep working."""
    schema = _phase2_tool_schema(None)
    props = schema["input_schema"]["properties"]
    assert "adr_candidates" in props
    assert "worked_on" in props
    assert "discussion" not in props
    assert "decisions" not in props


# ---------------------------------------------------------------------------
# Phase 2 — Summary lede + outcomes/takeaways shape (PRD #61, slice #62).
# Replaces the old single-string ``summary`` field with a structured
# lede + bullet-array shape; bullet field name is shape-conditional.
# ---------------------------------------------------------------------------


def test_phase2_schema_work_shape_emits_summary_lede_and_outcomes():
    schema = _phase2_tool_schema(_make_shape(has_edits=True))
    props = schema["input_schema"]["properties"]
    assert "summary_lede" in props
    assert "summary_outcomes" in props
    # Old single-string ``summary`` field is gone — body Summary is
    # composed from the structured fields at apply time.
    assert "summary" not in props
    # Discussion-shape companion absent in work shape.
    assert "summary_takeaways" not in props
    # Required list reflects the new lede field.
    assert "summary_lede" in schema["input_schema"]["required"]
    assert "summary" not in schema["input_schema"]["required"]


def test_phase2_schema_discussion_shape_emits_summary_lede_and_takeaways():
    schema = _phase2_tool_schema(_make_shape(has_edits=False))
    props = schema["input_schema"]["properties"]
    assert "summary_lede" in props
    assert "summary_takeaways" in props
    assert "summary" not in props
    assert "summary_outcomes" not in props


def test_phase2_schema_summary_lede_has_max_length_cap():
    """``summary_lede`` is structurally capped at 160 chars — the
    prompt instruction is doubled-up by the schema so the LLM has
    no room to drift back into prose paragraphs."""
    schema = _phase2_tool_schema(_make_shape(has_edits=True))
    lede_prop = schema["input_schema"]["properties"]["summary_lede"]
    assert lede_prop["type"] == "string"
    assert lede_prop["maxLength"] == 160


def test_phase2_schema_summary_outcomes_has_max_items_cap():
    schema = _phase2_tool_schema(_make_shape(has_edits=True))
    outcomes_prop = schema["input_schema"]["properties"]["summary_outcomes"]
    assert outcomes_prop["type"] == "array"
    assert outcomes_prop["maxItems"] == 4


def test_phase2_schema_summary_takeaways_has_max_items_cap():
    schema = _phase2_tool_schema(_make_shape(has_edits=False))
    takeaways_prop = schema["input_schema"]["properties"]["summary_takeaways"]
    assert takeaways_prop["type"] == "array"
    assert takeaways_prop["maxItems"] == 4


def test_phase2_prompt_drops_legacy_4_5_sentence_paragraph_clause():
    prompt = _phase2_prompt(
        turns_text="some text",
        activity_summary="",
        is_continuation=False,
        continues_wikilink=None,
        shape=_make_shape(has_edits=True),
    )
    # The pre-redesign instruction "4-5 sentence body paragraph" is
    # gone — that clause is what produced verbose prose summaries.
    assert "4-5 sentence" not in prompt
    assert "body paragraph" not in prompt


def test_phase2_prompt_work_shape_mentions_summary_lede_and_outcomes():
    prompt = _phase2_prompt(
        turns_text="some text",
        activity_summary="",
        is_continuation=False,
        continues_wikilink=None,
        shape=_make_shape(has_edits=True),
    )
    assert "summary_lede" in prompt
    assert "summary_outcomes" in prompt
    # Outcomes should be framed as state-of-world, distinct from
    # worked_on which narrates process.
    assert "state-of-world" in prompt or "present-tense" in prompt


def test_phase2_prompt_discussion_shape_mentions_summary_lede_and_takeaways():
    prompt = _phase2_prompt(
        turns_text="some text",
        activity_summary="",
        is_continuation=False,
        continues_wikilink=None,
        shape=_make_shape(has_edits=False),
    )
    assert "summary_lede" in prompt
    assert "summary_takeaways" in prompt


def test_phase2_apply_renders_summary_from_lede_and_outcomes(
    lore_root, patch_collectors, monkeypatch,
):
    """End-to-end: when the LLM emits the new structured Summary fields,
    the body ``## Summary`` block contains the lede on its own line
    followed by outcome bullets."""
    _, stub_path, sidecar_path = _seed_stub(lore_root, monkeypatch)

    composed = {
        "title": "auth handler refactor",
        "description": "Rebuilt auth.py against the policy decorator.",
        "summary_lede": "auth.py now uses the policy decorator.",
        "summary_outcomes": [
            "callbacks pulled out",
            "tests are green",
        ],
        "decisions": [],
        "worked_on": ["**auth.py** — pulled callbacks"],
        "loose_ends": [],
    }
    llm = _FakeLlmClient(_ok_responder(composed))
    outcome = synth_and_close(
        sidecar_path,
        lore_root=lore_root,
        wiki_root=lore_root / "wiki" / "private",
        llm_client=llm,
        model="m",
    )
    assert outcome.phase2_completed is True
    text = outcome.stub_path.read_text()
    assert "## Summary" in text
    # Lede on its own line.
    assert "auth.py now uses the policy decorator." in text
    # Outcomes rendered as bullets under the Summary heading.
    assert "- callbacks pulled out" in text
    assert "- tests are green" in text


def test_phase2_apply_renders_summary_from_lede_only_when_outcomes_empty(
    lore_root, patch_collectors, monkeypatch,
):
    """Thin-signal path: lede with no outcomes produces just the lede,
    no trailing blank-line / bullet artefact."""
    _, stub_path, sidecar_path = _seed_stub(lore_root, monkeypatch)

    composed = {
        "title": "tiny session",
        "description": "d",
        "summary_lede": "tiny touch-up to auth.py.",
        "summary_outcomes": [],
        "decisions": [],
        "worked_on": ["**auth.py** — touched"],
        "loose_ends": [],
    }
    llm = _FakeLlmClient(_ok_responder(composed))
    outcome = synth_and_close(
        sidecar_path,
        lore_root=lore_root,
        wiki_root=lore_root / "wiki" / "private",
        llm_client=llm,
        model="m",
    )
    text = outcome.stub_path.read_text()
    # Body Summary is just the lede, immediately followed by the next
    # H2 heading (no orphan ``- `` bullet line).
    summary_idx = text.index("## Summary")
    next_h2_idx = text.index("## ", summary_idx + len("## Summary"))
    summary_block_text = text[summary_idx:next_h2_idx]
    assert "tiny touch-up to auth.py." in summary_block_text
    # No bullets in the Summary block.
    summary_body_lines = summary_block_text.splitlines()[2:]  # skip heading + blank
    assert not any(line.lstrip().startswith("- ") for line in summary_body_lines)


def test_phase2_apply_renders_summary_from_takeaways_in_discussion_shape(
    lore_root, patch_collectors, monkeypatch,
):
    """Discussion shape uses ``summary_takeaways`` not ``summary_outcomes``."""
    _, stub_path, sidecar_path = _seed_stub(lore_root, monkeypatch)
    monkeypatch.setattr(
        "lore_curator.synthesis._files_modified_from_turns",
        lambda turns: [],
    )

    composed = {
        "title": "Discussed: docs spine",
        "description": "Talked through Diátaxis options.",
        "summary_lede": "leaned toward a Diátaxis spine for docs; nothing landed.",
        "summary_takeaways": [
            "four-quadrant split fits the existing material",
            "ADR backlog still needs validation",
        ],
        "discussion": ["**Diátaxis spine** — explored split options"],
        "loose_ends": [],
    }
    llm = _FakeLlmClient(_ok_responder(composed))
    outcome = synth_and_close(
        sidecar_path,
        lore_root=lore_root,
        wiki_root=lore_root / "wiki" / "private",
        llm_client=llm,
        model="m",
    )
    text = outcome.stub_path.read_text()
    assert "leaned toward a Diátaxis spine" in text
    assert "- four-quadrant split fits the existing material" in text
    assert "- ADR backlog still needs validation" in text


def test_phase2_apply_falls_back_to_legacy_summary_string(
    lore_root, patch_collectors, monkeypatch,
):
    """An in-flight composed dict from an old code path that only set
    the legacy single-string ``summary`` must not crash the applier;
    the string is used verbatim as the body Summary. Note: in normal
    flow, ``compose_session_note`` strips ``summary`` upstream because
    it isn't in the new schema — so this fallback is purely defensive
    for direct-apply callers and rollout-boundary edge cases."""
    from lore_curator.synthesis import _phase2_apply

    buffer, stub_path, sidecar_path = _seed_stub(lore_root, monkeypatch)
    sidecar = buffer.read_sidecar()
    rb = buffer.replay()

    composed_legacy = {
        "title": "legacy shape",
        "description": "d",
        # Old shape: single ``summary`` string, no lede / outcomes.
        "summary": "the legacy prose summary should still land in the body.",
    }
    final_path = _phase2_apply(
        stub_path=stub_path,
        composed=composed_legacy,
        wiki_root=lore_root / "wiki" / "private",
        rb=rb,
        sidecar=sidecar,
    )
    text = final_path.read_text()
    assert "the legacy prose summary should still land in the body." in text


def test_phase2_prompt_carries_discussion_clause():
    prompt = _phase2_prompt(
        turns_text="some user text",
        activity_summary="",
        is_continuation=False,
        continues_wikilink=None,
        shape=_make_shape(has_edits=False, no_edit_intent=True),
    )
    assert "discussion" in prompt.lower()
    # worked_on is excluded from discussion shape; adr_candidates IS available.
    assert "worked_on" in prompt
    assert "adr_candidates" in prompt
    assert "no code change" in prompt.lower() or "disclaimed" in prompt.lower()


def test_phase2_prompt_work_clause_when_edits_present():
    prompt = _phase2_prompt(
        turns_text="some user text",
        activity_summary="",
        is_continuation=False,
        continues_wikilink=None,
        shape=_make_shape(has_edits=True),
    )
    assert "work" in prompt.lower()
    # Empty adr_candidates array is the expected default — the prompt
    # makes that explicit (without it, the model over-emits candidates).
    assert "leave the array empty" in prompt.lower() or "zero is the expected" in prompt.lower()


def test_phase2_drops_decisions_in_discussion_shape_response():
    """End-to-end: even if the LLM emits ``decisions[]`` or ``worked_on[]``
    in discussion shape (against the schema), the caller-side filter
    strips them and emits a ``compose-extra-key`` warning."""
    composed = {
        "title": "Sketched docs",
        "description": "d",
        "summary_lede": "s",
        "discussion": ["- considered Diátaxis"],
        "decisions": ["- this should not survive"],
        "worked_on": ["- nor this"],
    }
    llm = _FakeLlmClient(_ok_responder(composed))
    events: list[tuple[str, dict]] = []

    class _Logger:
        run_id = "test"

        def emit(self, name, **kw):
            events.append((name, kw))

    out = compose_session_note(
        turns_text="some text",
        activity_summary="",
        is_continuation=False,
        continues_wikilink=None,
        llm_client=llm,
        model="m",
        logger=_Logger(),
        shape=_make_shape(has_edits=False),
    )
    assert out is not None
    assert "discussion" in out
    assert "decisions" not in out  # stripped by the schema-key filter
    assert "worked_on" not in out  # stripped by the schema-key filter
    extra_events = [(n, kw) for n, kw in events if kw.get("call") == "compose-extra-key"]
    assert len(extra_events) == 1
    assert sorted(extra_events[0][1]["extra_keys"]) == ["decisions", "worked_on"]


# ---------------------------------------------------------------------------
# Title-verb gate (step-6). Pure post-LLM coercion: in discussion shape
# the title MUST NOT lead with a deliverable verb that promises work
# the session didn't deliver.
# ---------------------------------------------------------------------------


def test_title_coerce_work_shape_passes_through_unchanged():
    shape = _make_shape(has_edits=True)
    assert _coerce_title_for_shape("Refactor auth handler chain", shape) \
        == "Refactor auth handler chain"


def test_title_coerce_none_shape_passes_through_unchanged():
    """``shape=None`` (legacy callers) must not coerce anything — the
    behaviour matches pre-step-4 fixtures."""
    assert _coerce_title_for_shape("Refactor auth", None) == "Refactor auth"


def test_title_coerce_discussion_strips_deliverable_verb():
    shape = _make_shape(has_edits=False)
    assert _coerce_title_for_shape("Refactor docs into Diátaxis spine", shape) \
        == "Discussed: docs into Diátaxis spine"


def test_title_coerce_discussion_handles_each_deliverable_verb():
    shape = _make_shape(has_edits=False)
    cases = [
        ("Add new auth endpoint", "Discussed: new auth endpoint"),
        ("Fix race in scheduler", "Discussed: race in scheduler"),
        ("Implement webhook receiver", "Discussed: webhook receiver"),
        ("Migrate to PostgreSQL", "Discussed: to PostgreSQL"),
        ("Build the new pipeline", "Discussed: the new pipeline"),
        ("Ship release candidate", "Discussed: release candidate"),
        ("Land the auth refactor", "Discussed: the auth refactor"),
    ]
    for title_in, expected in cases:
        assert _coerce_title_for_shape(title_in, shape) == expected, title_in


def test_title_coerce_already_discussion_led_passes_through():
    shape = _make_shape(has_edits=False)
    cases = [
        "Discussed: Diátaxis docs spine",
        "Explored docs structure tradeoffs",
        "Sketched: new auth model",
        "Reviewed migration plan",
        "Considered options for caching",
    ]
    for title in cases:
        assert _coerce_title_for_shape(title, shape) == title, title


def test_title_coerce_noun_phrase_passes_through():
    """Non-deliverable-verb leads (noun phrases, less suspect verbs)
    are not coerced. Over-coercing would replace legitimate framings
    with a generic ``Discussed:`` prefix."""
    shape = _make_shape(has_edits=False)
    cases = [
        "Investigation of standing waves",
        "Notes on ccat docs structure",
        "ccat data-transfer architecture review",
    ]
    for title in cases:
        assert _coerce_title_for_shape(title, shape) == title


def test_title_coerce_empty_or_one_word_handled():
    shape = _make_shape(has_edits=False)
    assert _coerce_title_for_shape("", shape) == ""
    assert _coerce_title_for_shape("Refactor", shape) == "Discussed: session"


def test_title_coerce_truncates_to_8_words():
    shape = _make_shape(has_edits=False)
    long_title = "Refactor the very long title that exceeds the eight word cap"
    coerced = _coerce_title_for_shape(long_title, shape)
    # ``Discussed:`` + 7 trailing words (the original first word is
    # stripped as the deliverable verb)
    assert len(coerced.split()) == 8
    assert coerced.startswith("Discussed:")


def test_title_coerce_truncates_already_discussion_led_titles():
    """Word cap applies even to already-shaped titles."""
    shape = _make_shape(has_edits=False)
    long_title = "Discussed: the entire roadmap for the next quarter and beyond"
    coerced = _coerce_title_for_shape(long_title, shape)
    assert len(coerced.split()) <= 8
    assert coerced.startswith("Discussed:")


def test_phase2_surfaces_adr_flagged_in_frontmatter(
    lore_root, patch_collectors, monkeypatch,
):
    """step-8 of yes-do-that-keen-yeti: when the user explicitly invoked
    ADR vocabulary in the slice (e.g. 'ADR this' / 'let's record this
    as an ADR'), surface the flag in frontmatter so a future ``lore
    curator promote-adr`` flow can find candidates without re-scanning
    transcripts. No auto-stub creation — pure frontmatter signal."""
    _, stub_path, sidecar_path = _seed_stub(lore_root, monkeypatch)
    # Inject an ADR-flagged shape — the model emits a normal work-shape
    # response; the flag is shape-driven, not LLM-driven.
    monkeypatch.setattr(
        "lore_curator.synthesis.select_shape",
        lambda turns, files_modified: NarrativeShape(
            has_edits=True,
            decisions_allowed=True,
            no_edit_intent=False,
            adr_flagged=True,
        ),
    )
    composed = {
        "title": "Auth rewrite",
        "description": "d",
        "summary_lede": "s",
        "adr_candidates": [{
            "choice": "option B over option A",
            "rationale": "B is simpler",
            "evidence": "user chose B at turn 8",
            "alternative_rejected": "option A",
        }],
        "worked_on": ["**auth.py** — touched"],
        "loose_ends": [],
    }
    llm = _FakeLlmClient(_ok_responder(composed))
    outcome = synth_and_close(
        sidecar_path,
        lore_root=lore_root,
        wiki_root=lore_root / "wiki" / "private",
        llm_client=llm,
        model="m",
    )
    assert outcome.phase2_completed is True
    fm = parse_frontmatter(outcome.stub_path.read_text())
    assert fm.get("adr_flagged") is True


def test_phase2_omits_adr_flagged_when_not_set(
    lore_root, patch_collectors, monkeypatch,
):
    _, stub_path, sidecar_path = _seed_stub(lore_root, monkeypatch)
    composed = {
        "title": "Auth rewrite", "description": "d", "summary_lede": "s",
        "adr_candidates": [], "worked_on": ["**auth.py** — touched"], "loose_ends": [],
    }
    llm = _FakeLlmClient(_ok_responder(composed))
    outcome = synth_and_close(
        sidecar_path,
        lore_root=lore_root,
        wiki_root=lore_root / "wiki" / "private",
        llm_client=llm,
        model="m",
    )
    assert outcome.phase2_completed is True
    fm = parse_frontmatter(outcome.stub_path.read_text())
    assert "adr_flagged" not in fm


def _golden_path(name: str) -> Path:
    return Path(__file__).parent / "fixtures" / "prompts" / name


def _golden_prompt_inputs() -> dict[str, Any]:
    """Fixed inputs for golden-file regression. Any change to the prompt
    text — including reordering, rewording, adding clauses — diffs the
    golden file and forces explicit acknowledgement. Prevents silent
    regressions of the load-bearing Phase-2 prompt."""
    return {
        "turns_text": "[user@0] short fixture turn",
        "activity_summary": "1 commit, 0 issues",
        "is_continuation": False,
        "continues_wikilink": None,
    }


def test_phase2_prompt_work_shape_matches_golden():
    """Golden-file diff for the work-shape prompt. To intentionally
    update: regenerate the fixture and commit explicitly."""
    prompt = _phase2_prompt(
        **_golden_prompt_inputs(),
        shape=_make_shape(has_edits=True),
    )
    golden = _golden_path("phase2_work.txt")
    if not golden.exists():
        golden.write_text(prompt)
        # First run writes the fixture; subsequent runs compare.
    assert prompt == golden.read_text(), (
        "Phase-2 work-shape prompt drifted. If intentional, regenerate:\n"
        f"  python -c 'from tests.test_synthesis import *; "
        f"open({str(golden)!r}, \"w\").write(_phase2_prompt(**_golden_prompt_inputs(), "
        f"shape=NarrativeShape(True, True, False, False)))'"
    )


def test_phase2_prompt_discussion_shape_matches_golden():
    """Golden-file diff for the discussion-shape prompt."""
    prompt = _phase2_prompt(
        **_golden_prompt_inputs(),
        shape=_make_shape(has_edits=False, no_edit_intent=True),
    )
    golden = _golden_path("phase2_discussion.txt")
    if not golden.exists():
        golden.write_text(prompt)
    assert prompt == golden.read_text(), (
        "Phase-2 discussion-shape prompt drifted. If intentional, regenerate."
    )


def test_phase2_e2e_05_1212_pattern_yields_discussion_shape(
    lore_root, patch_collectors, monkeypatch,
):
    """End-to-end regression. Seed a buffer with ``files_modified=[]``
    (the bad-note transcript pattern), let synthesis pick the shape,
    and assert the rendered note omits Decisions / What we worked on
    and surfaces the Discussion section. This is the primary fix
    target for plan ``yes-do-that-keen-yeti``."""
    _, stub_path, sidecar_path = _seed_stub(lore_root, monkeypatch)
    # Override the seed's default files: this session is the bad
    # discussion-shape case — no edits.
    monkeypatch.setattr(
        "lore_curator.synthesis._files_modified_from_turns",
        lambda turns: [],
    )

    # The LLM emits a discussion-shape response. In the real failing
    # transcript the model would have been steered by the prompt + the
    # narrowed schema; here we just simulate the well-shaped response.
    composed = {
        "title": "Discussed: ccat docs Diátaxis spine",
        "description": "Sketched a Diátaxis-style refactor of the data-transfer docs; no changes made.",
        "summary_lede": "We talked through the existing Sphinx docs and considered a four-quadrant restructure. No edits — exploration only.",
        "discussion": [
            "**Diátaxis spine** — explored how to split tutorials/how-to/reference/explanation",
            "**ADR extraction** — considered promoting philosophy.md essays into 7 ADRs",
        ],
        "loose_ends": ["**ADR backlog** — not yet validated with stakeholders"],
    }
    llm = _FakeLlmClient(_ok_responder(composed))
    outcome = synth_and_close(
        sidecar_path,
        lore_root=lore_root,
        wiki_root=lore_root / "wiki" / "private",
        llm_client=llm,
        model="m",
    )
    assert outcome.phase2_completed is True
    text = outcome.stub_path.read_text()
    assert "## Decisions made" not in text
    assert "## What we worked on" not in text
    assert "## Discussion" in text
    assert "Diátaxis" in text


# ---------------------------------------------------------------------------
# Issue #60 — narrative: pending sentinel lifecycle
# ---------------------------------------------------------------------------


def test_phase2_pops_narrative_pending_sentinel(
    lore_root, patch_collectors, monkeypatch,
):
    """Phase 2 must drop ``narrative: pending`` once the LLM-composed
    summary, description, and title are in place."""
    _, _stub_path, sidecar_path = _seed_stub(lore_root, monkeypatch)

    composed = {
        "title": "auth handler refactor",
        "description": "Rebuilt auth.py against the policy decorator.",
        "summary_lede": "We pulled the legacy callbacks out and slotted in a decorator.",
    }
    llm = _FakeLlmClient(_ok_responder(composed))
    outcome = synth_and_close(
        sidecar_path,
        lore_root=lore_root,
        wiki_root=lore_root / "wiki" / "private",
        llm_client=llm,
        model="m",
    )
    assert outcome.phase2_completed is True
    fm = parse_frontmatter(outcome.stub_path.read_text())
    assert "narrative" not in fm
    assert fm["description"] == composed["description"]


def test_synth_and_close_pops_narrative_sentinel_without_llm(
    lore_root, patch_collectors, monkeypatch,
):
    """The cap-trip / reaper close path must leave no
    ``narrative: pending`` on the closed note even when no LLM ran —
    Phase 1 (deterministic) is the only writer in that case."""
    from lore_curator.synthesis import synth_and_close

    _, _stub_path, sidecar_path = _seed_stub(lore_root, monkeypatch)
    outcome = synth_and_close(
        sidecar_path,
        lore_root=lore_root,
        wiki_root=lore_root / "wiki" / "private",
        # No LLM: Phase 2 skipped; Phase 1 owns the popping.
    )
    assert outcome.phase1_completed is True
    fm = parse_frontmatter(outcome.stub_path.read_text())
    assert "narrative" not in fm
    # Description reset to the deterministic placeholder so the closed-but-
    # unsynthesised note doesn't carry a "Live stub" framing.
    assert fm["description"] == stub_note.STUB_DESCRIPTION_PLACEHOLDER


def test_synth_in_place_then_heartbeat_preserves_llm_summary(
    lore_root, patch_collectors, monkeypatch,
):
    """After ``synth_in_place`` lands an LLM narrative, the next heartbeat
    against the live buffer must preserve the LLM summary / description /
    title and must NOT re-add ``narrative: pending``."""
    from lore_curator.synthesis import synth_in_place

    buf, stub_path, sidecar_path = _seed_stub(lore_root, monkeypatch)

    # Title that yields the same slug as the deterministic stub
    # ("fix-seeded-commit" — driven by ``patch_collectors``'s seeded
    # commit) so Phase 2 keeps the file at sidecar.stub_path. Rename
    # behaviour is covered by a separate test.
    composed = {
        "title": "fix seeded commit",
        "description": "Rebuilt auth.py against the policy decorator.",
        "summary_lede": "We pulled the legacy callbacks out and slotted in a decorator.",
    }
    llm = _FakeLlmClient(_ok_responder(composed))
    out = synth_in_place(
        sidecar_path,
        lore_root=lore_root,
        wiki_root=lore_root / "wiki" / "private",
        llm_client=llm,
        model="m",
    )
    assert out.phase2_completed is True
    final_path = out.stub_path
    fm_after_phase2 = parse_frontmatter(final_path.read_text())
    assert "narrative" not in fm_after_phase2
    assert fm_after_phase2["description"] == composed["description"]

    # Now drive another heartbeat into the still-accumulating buffer.
    monkeypatch.setattr(
        "lore_curator.buffer_append._files_touched_from_turns",
        lambda _turns: ["/repo/extra.py"],
    )
    monkeypatch.setattr(
        "lore_curator.buffer_append._files_modified_from_turns",
        lambda _turns: ["/repo/extra.py"],
    )
    turns_b = [Turn(index=2, timestamp=None, role="user", text="more work")]
    o2 = append_chunk(
        lore_root=lore_root, chunk_turns=turns_b, local_date="2026-05-01",
        transcript_id="abc", integration="claude-code", wiki="private",
        scope="proj:feature", cwd=lore_root,
        wiki_root=lore_root / "wiki" / "private", cfg=WikiConfig(),
    )
    write_or_update(
        outcome=o2, scope=_make_scope(), transcript=_make_handle(),
        wiki_root=lore_root / "wiki" / "private",
        work_time=datetime(2026, 5, 1, 14, 32, tzinfo=UTC),
        now=datetime(2026, 5, 1, 14, 32, tzinfo=UTC),
        integration="claude-code",
        chunk_from_hash=turns_b[0].content_hash(),
        chunk_to_hash=turns_b[-1].content_hash(),
    )
    text_after_heartbeat = final_path.read_text()
    fm_after_heartbeat = parse_frontmatter(text_after_heartbeat)

    # LLM-composed fields preserved.
    assert "narrative" not in fm_after_heartbeat
    assert fm_after_heartbeat["description"] == composed["description"]
    assert fm_after_heartbeat["title"] == composed["title"]
    # LLM summary text still in body; live-stub framing did NOT come back.
    assert "We pulled the legacy callbacks" in text_after_heartbeat
    assert "_This is a live stub." not in text_after_heartbeat
    # Activity-side accumulators DID refresh (extra.py joined).
    assert "/repo/extra.py" in (fm_after_heartbeat.get("files_modified") or [])
