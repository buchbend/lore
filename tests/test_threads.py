"""Tests for lore_core.threads — passive continuation-linking surface.

Threads are connected components of session notes joined by shared
(non-boilerplate) ``files_touched``. The whole module is deterministic —
no LLM, no back-patching of older notes. Each Curator B run regenerates
a single ``threads.md`` at the wiki root.
"""
from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_note(
    wikilink: str,
    *,
    files: list[str],
    title: str | None = None,
    summary: str = "",
    created: str = "2026-04-24",
    scope: str = "proj:test",
):
    """Build a NoteRef for thread tests."""
    from lore_core.threads import NoteRef

    return NoteRef(
        wikilink=wikilink,
        title=title or wikilink.strip("[]").split("-", 1)[-1],
        summary=summary,
        files_touched=list(files),
        created=created,
        scope=scope,
    )


# ---------------------------------------------------------------------------
# compute_threads — graph algorithm
# ---------------------------------------------------------------------------


def test_compute_threads_empty_input_returns_empty_list():
    from lore_core.threads import compute_threads

    assert compute_threads([]) == []


def test_compute_threads_two_notes_sharing_file_form_one_thread():
    from lore_core.threads import compute_threads

    notes = [
        _make_note("[[24-auth-day1]]", files=["auth.py"]),
        _make_note("[[24-auth-day2]]", files=["auth.py", "helpers.py"]),
    ]
    threads = compute_threads(notes)
    assert len(threads) == 1
    members = [m.wikilink for m in threads[0].members]
    assert set(members) == {"[[24-auth-day1]]", "[[24-auth-day2]]"}


def test_compute_threads_disjoint_files_form_separate_threads():
    """Two unrelated topics, each represented by 2+ notes, must surface
    as two separate threads — not bridged by accident."""
    from lore_core.threads import compute_threads

    notes = [
        _make_note("[[24-auth-1]]", files=["auth.py"]),
        _make_note("[[24-auth-2]]", files=["auth.py"]),
        _make_note("[[24-schema-1]]", files=["schema.sql"]),
        _make_note("[[24-schema-2]]", files=["schema.sql"]),
    ]
    threads = compute_threads(notes)
    assert len(threads) == 2


def test_compute_threads_transitive_connection_builds_one_thread():
    """A→B sharing X, B→C sharing Y → A, B, C all in one thread (B is the
    bridge, even though A and C don't directly share any files)."""
    from lore_core.threads import compute_threads

    notes = [
        _make_note("[[24-A]]", files=["auth.py"]),
        _make_note("[[24-B]]", files=["auth.py", "users.py"]),
        _make_note("[[24-C]]", files=["users.py"]),
    ]
    threads = compute_threads(notes)
    assert len(threads) == 1
    assert len(threads[0].members) == 3


def test_compute_threads_boilerplate_overlap_does_not_bridge():
    """Two real topics, each with 2 notes, sharing only CLAUDE.md /
    pyproject.toml across topics → they remain separate threads, not
    bridged by boilerplate. Same heuristic as the Phase C merge rule."""
    from lore_core.threads import compute_threads

    notes = [
        _make_note("[[24-auth-1]]", files=["auth.py", "CLAUDE.md", "pyproject.toml"]),
        _make_note("[[24-auth-2]]", files=["auth.py", "CLAUDE.md", "pyproject.toml"]),
        _make_note("[[24-schema-1]]", files=["schema.sql", "CLAUDE.md", "pyproject.toml"]),
        _make_note("[[24-schema-2]]", files=["schema.sql", "CLAUDE.md", "pyproject.toml"]),
    ]
    threads = compute_threads(notes)
    assert len(threads) == 2


def test_compute_threads_singleton_notes_excluded_by_default():
    """A single isolated note isn't a thread — threads are about CONNECTING
    notes. Solo notes carry their own provenance already."""
    from lore_core.threads import compute_threads

    notes = [
        _make_note("[[24-auth-day1]]", files=["auth.py"]),
        _make_note("[[24-auth-day2]]", files=["auth.py"]),
        _make_note("[[24-isolated]]", files=["random.py"]),
    ]
    threads = compute_threads(notes)
    assert len(threads) == 1


def test_compute_threads_notes_lacking_files_touched_dont_form_threads():
    """Legacy notes with no files_touched can't be linked — they're
    excluded from the graph rather than dragged into something arbitrary."""
    from lore_core.threads import compute_threads

    notes = [
        _make_note("[[24-legacy]]", files=[]),
        _make_note("[[24-real-1]]", files=["auth.py"]),
        _make_note("[[24-real-2]]", files=["auth.py"]),
    ]
    threads = compute_threads(notes)
    assert len(threads) == 1
    members = [m.wikilink for m in threads[0].members]
    assert "[[24-legacy]]" not in members


def test_compute_threads_members_sorted_by_created_then_wikilink():
    """Within a thread, members are listed in temporal order so a reader
    sees the work progression."""
    from lore_core.threads import compute_threads

    notes = [
        _make_note("[[24-auth-day3]]", files=["auth.py"], created="2026-04-25"),
        _make_note("[[24-auth-day1]]", files=["auth.py"], created="2026-04-23"),
        _make_note("[[24-auth-day2]]", files=["auth.py"], created="2026-04-24"),
    ]
    threads = compute_threads(notes)
    assert len(threads) == 1
    members = [m.wikilink for m in threads[0].members]
    assert members == ["[[24-auth-day1]]", "[[24-auth-day2]]", "[[24-auth-day3]]"]


def test_compute_threads_thread_label_from_most_common_file():
    """Each thread gets a short label derived from the most-shared file
    across its members. Deterministic — useful as a section header in
    the rendered threads.md."""
    from lore_core.threads import compute_threads

    notes = [
        _make_note("[[24-a]]", files=["auth.py", "logger.py"]),
        _make_note("[[24-b]]", files=["auth.py", "models.py"]),
    ]
    threads = compute_threads(notes)
    assert threads[0].label == "auth.py"  # touched by all members


def test_compute_threads_label_ranks_paths_not_basenames():
    """Label-counter must rank by full path, not basename.

    Real-world bug: one note touched 4 different `SKILL.md` files in
    different skill directories (`skills/quiet/SKILL.md`,
    `skills/off/SKILL.md`, ...). Counting by basename gave SKILL.md 4
    votes from a single note, outweighing the actual cross-note signal
    (e.g. `curator_b.py`, which appeared in 3 separate notes). The
    thread heading then read 'SKILL.md' even though SKILL.md was a
    single-note artifact.

    Correct behavior: count *paths* across notes (one vote per
    note-path pair), then derive the basename of the winning path for
    display.
    """
    from lore_core.threads import compute_threads

    notes = [
        # Note A touches 4 different SKILL.md paths AND curator_b.py.
        _make_note("[[a]]", files=[
            "skills/quiet/SKILL.md",
            "skills/off/SKILL.md",
            "skills/on/SKILL.md",
            "skills/loud/SKILL.md",
            "lib/lore_curator/curator_b.py",
        ]),
        # Notes B and C share curator_b.py with A — that's the real
        # cross-note signal worth labelling on.
        _make_note("[[b]]", files=["lib/lore_curator/curator_b.py", "x.py"]),
        _make_note("[[c]]", files=["lib/lore_curator/curator_b.py", "y.py"]),
    ]
    threads = compute_threads(notes)
    assert len(threads) == 1
    # Label should be the basename of curator_b.py (3 cross-note votes),
    # NOT 'SKILL.md' (4 votes from a single note).
    assert threads[0].label == "curator_b.py"


def test_compute_threads_label_distinguishes_same_basename_different_paths():
    """Two paths with the same basename in different directories must NOT
    aggregate as the same vote. Each path votes independently; the
    basename is only derived for display.

    If both ``a/foo.py`` and ``b/foo.py`` happened to be the most-shared
    single path, the label would still render as 'foo.py' — but the
    *count* must reflect path-level distinctness so a real cross-note
    file beats a within-note collection of same-basename paths.
    """
    from lore_core.threads import compute_threads

    notes = [
        _make_note("[[a]]", files=["repo1/foo.py", "repo2/foo.py", "shared.py"]),
        _make_note("[[b]]", files=["shared.py", "other.py"]),
    ]
    threads = compute_threads(notes)
    assert len(threads) == 1
    # shared.py appears in both notes (2 votes); each foo.py path appears
    # in only one note (1 vote each). shared.py wins.
    assert threads[0].label == "shared.py"


# ---------------------------------------------------------------------------
# render_threads_markdown — single index file
# ---------------------------------------------------------------------------


def test_label_threads_with_llm_populates_llm_label_field():
    """One simple-tier LLM call per thread produces a concise title.
    Input is small (titles + summaries of members), no full-note bodies."""
    from lore_core.threads import NoteRef, Thread, label_threads_with_llm

    base = Thread(
        label="curator_a.py",
        members=[
            NoteRef(wikilink="[[24-a]]", title="Phase B day-split",
                    summary="Day-boundary outer split in Curator A.",
                    files_touched=["curator_a.py"], created="2026-04-24"),
            NoteRef(wikilink="[[25-b]]", title="Phase C topic-aware merge",
                    summary="File-set Jaccard merge gate.",
                    files_touched=["session_writer.py"], created="2026-04-25"),
        ],
        shared_files=[],
    )

    captured: dict = {}

    class _FakeClient:
        class messages:
            @staticmethod
            def create(**kwargs):
                captured.update(kwargs)
                class _Block:
                    type = "tool_use"
                    input = {"label": "Curator A chunking + topic-aware merge"}
                class _Resp:
                    content = [_Block()]
                return _Resp()

    def resolver(tier: str) -> str:
        assert tier == "simple", "thread labelling must use the cheapest tier"
        return "claude-haiku-4-5"

    out = label_threads_with_llm([base], llm_client=_FakeClient(),
                                  model_resolver=resolver)
    assert len(out) == 1
    assert out[0].llm_label == "Curator A chunking + topic-aware merge"
    # Original algorithmic label preserved as fallback
    assert out[0].label == "curator_a.py"
    # Confirm the prompt actually went out at simple tier with one tool
    assert captured["model"] == "claude-haiku-4-5"
    assert captured["tool_choice"] == {"type": "tool", "name": "label"}


def test_label_threads_with_llm_falls_back_silently_on_failure():
    """If the LLM call raises (rate-limit, bad gateway, missing model
    config), we keep the algorithmic label and move on. Threads.md
    must never fail because a label call did."""
    from lore_core.threads import Thread, label_threads_with_llm

    base = Thread(label="curator_a.py", members=[], shared_files=[])

    class _RaisingClient:
        class messages:
            @staticmethod
            def create(**kwargs):
                raise RuntimeError("simulated 429")

    out = label_threads_with_llm([base], llm_client=_RaisingClient(),
                                  model_resolver=lambda t: "x")
    assert len(out) == 1
    assert out[0].llm_label == ""
    assert out[0].label == "curator_a.py"  # fallback


def test_label_threads_with_llm_skips_when_no_client():
    """Passing client=None is a no-op — same shape, no llm_label.
    Render falls back to algorithmic label."""
    from lore_core.threads import Thread, label_threads_with_llm

    base = Thread(label="auth.py", members=[], shared_files=[])
    out = label_threads_with_llm([base], llm_client=None,
                                  model_resolver=lambda t: "x")
    assert out[0].llm_label == ""


def test_render_uses_llm_label_when_present():
    from lore_core.threads import Thread, render_threads_markdown

    from lore_core.threads import NoteRef as _NR
    threads = [Thread(
        label="curator_a.py",
        llm_label="Curator A chunking + topic-aware merge",
        members=[_NR(wikilink="[[24-a]]", created="2026-04-24")],
        shared_files=[],
    )]
    md = render_threads_markdown(threads, generated_at=datetime(2026, 4, 25, tzinfo=UTC))
    assert "## Curator A chunking + topic-aware merge" in md
    # Algorithmic label visible as a small annotation, not the heading
    assert "## curator_a.py" not in md


def test_render_threads_markdown_lists_threads_with_member_wikilinks():
    from lore_core.threads import compute_threads, render_threads_markdown

    notes = [
        _make_note("[[24-auth-day1]]", files=["auth.py"], created="2026-04-23"),
        _make_note("[[24-auth-day2]]", files=["auth.py"], created="2026-04-24"),
        _make_note("[[24-schema-1]]", files=["schema.sql"], created="2026-04-23"),
        _make_note("[[24-schema-2]]", files=["schema.sql"], created="2026-04-24"),
    ]
    threads = compute_threads(notes)
    md = render_threads_markdown(threads, generated_at=datetime(2026, 4, 25, 12, 0, 0, tzinfo=UTC))

    # Header + provenance comment that this is a generated file
    assert "Threads" in md
    assert "generated" in md.lower()
    # Each thread's wikilinks present, in temporal order
    for wl in ("[[24-auth-day1]]", "[[24-auth-day2]]", "[[24-schema-1]]", "[[24-schema-2]]"):
        assert wl in md
    # auth-day1 appears before auth-day2 (temporal order)
    assert md.index("[[24-auth-day1]]") < md.index("[[24-auth-day2]]")


def test_render_threads_markdown_empty_threads_renders_placeholder():
    from lore_core.threads import render_threads_markdown

    md = render_threads_markdown([], generated_at=datetime(2026, 4, 25, tzinfo=UTC))
    assert "Threads" in md
    assert "no threads" in md.lower() or "No threads" in md


def test_render_empty_with_note_count_explains_why():
    """M1 discoverability: when notes were scanned but none formed a thread,
    say so — otherwise the user wonders if Curator B is broken."""
    from lore_core.threads import render_threads_markdown

    md = render_threads_markdown(
        [],
        generated_at=datetime(2026, 4, 25, tzinfo=UTC),
        notes_scanned=42,
    )
    assert "42" in md
    assert "share" in md.lower() or "files" in md.lower()


# ---------------------------------------------------------------------------
# scan_session_notes — wiki-walk that produces NoteRef-shaped dicts
# ---------------------------------------------------------------------------


def _write_note(path: Path, *, scope: str, created: str, files: list[str], wikilink_hint: str | None = None) -> None:
    import yaml
    fm = {
        "schema_version": 2,
        "type": "session",
        "created": created,
        "last_reviewed": created,
        "description": wikilink_hint or path.stem,
        "scope": scope,
        "draft": True,
        "files_touched": files,
    }
    dumped = yaml.safe_dump(fm, sort_keys=False, allow_unicode=True).strip()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"---\n{dumped}\n---\n\nbody\n")


def test_scan_session_notes_skips_malformed_frontmatter(tmp_path):
    """L1 regression: a single note with broken YAML must not abort the
    entire scan. Other notes still surface; the bad note is skipped."""
    from lore_core.threads import scan_session_notes

    sessions = tmp_path / "wiki" / "private" / "sessions" / "2026" / "04"
    sessions.mkdir(parents=True)

    # One good note
    _write_note(sessions / "23-good.md", scope="proj:test",
                created="2026-04-23", files=["auth.py"])

    # One note with malformed YAML in frontmatter
    (sessions / "23-broken.md").write_text("---\nthis: is: not: valid: yaml\n---\nbody\n")

    # One note that's not even close to YAML
    (sessions / "23-empty.md").write_text("not a session note at all")

    notes = scan_session_notes(tmp_path / "wiki" / "private")
    wikilinks = [n.wikilink for n in notes]
    assert "[[23-good]]" in wikilinks
    # malformed/empty notes silently dropped
    assert "[[23-broken]]" not in wikilinks
    assert "[[23-empty]]" not in wikilinks


def test_scan_session_notes_reads_files_touched_from_frontmatter(tmp_path):
    from lore_core.threads import scan_session_notes

    sessions = tmp_path / "wiki" / "private" / "sessions" / "2026" / "04"
    _write_note(sessions / "23-auth.md", scope="proj:test", created="2026-04-23",
                files=["auth.py"])
    _write_note(sessions / "24-auth.md", scope="proj:test", created="2026-04-24",
                files=["auth.py", "helpers.py"])

    notes = scan_session_notes(tmp_path / "wiki" / "private")
    wikilinks = sorted(n.wikilink for n in notes)
    assert wikilinks == ["[[23-auth]]", "[[24-auth]]"]
    by_wl = {n.wikilink: n for n in notes}
    assert by_wl["[[24-auth]]"].files_touched == ["auth.py", "helpers.py"]
