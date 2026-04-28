"""Tests for _last_session_hint — recent session note breadcrumbs at SessionStart.

Phase 4 of the session-note revision fixes two latent bugs and one
preference reordering:

- Sharded layout. The session dir is ``sessions/<YYYY>/<MM>/<DD>-slug.md``
  (and team-mode-shard adds an extra ``<handle>/`` segment); the prior
  flat ``sessions_dir.glob("*.md")`` only ever found ``_recent.md`` (a
  cached pointer file with no real frontmatter), so the status-line
  silently went empty against the real vault.
- 1024-byte head-read cap. Real notes carry SHA-256 hashes in
  ``source_transcripts`` plus a long ``summary`` paragraph, so the
  hint field routinely sat past the cap. Cap removed.
- Status-line preference: ``title`` (revision; explicit slug-source
  field) → ``description`` (revision short paragraph; legacy short
  headline) → ``summary`` (legacy paragraph).
"""

from pathlib import Path

from lore_cli.hooks import _last_session_hint


def _write_session_flat(
    wiki: Path,
    slug: str,
    *,
    title: str | None = None,
    description: str | None = None,
    summary: str | None = None,
) -> None:
    """Write a session note in the flat-legacy layout (sessions/<slug>.md)."""
    sessions = wiki / "sessions"
    sessions.mkdir(parents=True, exist_ok=True)
    lines = ["---"]
    lines.append("type: session")
    if title is not None:
        lines.append(f"title: '{title}'")
    if description is not None:
        lines.append(f"description: '{description}'")
    if summary is not None:
        lines.append(f"summary: '{summary}'")
    lines.append("---")
    lines.append("")
    (sessions / f"{slug}.md").write_text("\n".join(lines))


def _write_session_sharded(
    wiki: Path,
    *,
    year: int,
    month: int,
    day: int,
    slug: str,
    title: str | None = None,
    description: str | None = None,
    summary: str | None = None,
    extra_frontmatter: str = "",
) -> Path:
    """Write a session note in the canonical sharded layout."""
    sessions_dir = wiki / "sessions" / str(year) / f"{month:02d}"
    sessions_dir.mkdir(parents=True, exist_ok=True)
    lines = ["---"]
    lines.append("type: session")
    if title is not None:
        lines.append(f"title: '{title}'")
    if description is not None:
        lines.append(f"description: '{description}'")
    if summary is not None:
        lines.append(f"summary: '{summary}'")
    if extra_frontmatter:
        lines.append(extra_frontmatter)
    lines.append("---")
    lines.append("")
    fname = f"{day:02d}-{slug}.md"
    path = sessions_dir / fname
    path.write_text("\n".join(lines))
    return path


# Back-compat alias for the older tests below.
_write_session = _write_session_flat


def test_empty_sessions_dir(tmp_path: Path) -> None:
    wiki = tmp_path / "wiki" / "private"
    wiki.mkdir(parents=True)
    assert _last_session_hint(wiki) == []


def test_no_sessions_dir(tmp_path: Path) -> None:
    wiki = tmp_path / "wiki" / "private"
    wiki.mkdir(parents=True)
    assert _last_session_hint(wiki) == []


def test_returns_most_recent(tmp_path: Path) -> None:
    wiki = tmp_path / "wiki" / "private"
    _write_session(wiki, "2026-04-20-old-session", description="Old work")
    _write_session(wiki, "2026-04-21-middle-session", description="Middle work")
    _write_session(wiki, "2026-04-22-latest-session", description="Latest work")

    result = _last_session_hint(wiki, max_notes=2)
    assert len(result) == 2
    assert result[0] == ("2026-04-22-latest-session", "Latest work")
    assert result[1] == ("2026-04-21-middle-session", "Middle work")


def test_skips_notes_without_description(tmp_path: Path) -> None:
    wiki = tmp_path / "wiki" / "private"
    _write_session(wiki, "2026-04-22-no-desc")
    _write_session(wiki, "2026-04-21-has-desc", description="Has a description")

    result = _last_session_hint(wiki, max_notes=2)
    assert len(result) == 1
    assert result[0] == ("2026-04-21-has-desc", "Has a description")


def test_tuple_format(tmp_path: Path) -> None:
    wiki = tmp_path / "wiki" / "private"
    _write_session(wiki, "2026-04-22-some-slug", description="Some work")

    result = _last_session_hint(wiki, max_notes=1)
    assert len(result) == 1
    slug, desc = result[0]
    assert slug == "2026-04-22-some-slug"
    assert desc == "Some work"


def test_single_note_available(tmp_path: Path) -> None:
    wiki = tmp_path / "wiki" / "private"
    _write_session(wiki, "2026-04-22-only-one", description="Only session")

    result = _last_session_hint(wiki, max_notes=2)
    assert len(result) == 1


def test_max_notes_respected(tmp_path: Path) -> None:
    wiki = tmp_path / "wiki" / "private"
    for i in range(5):
        _write_session(wiki, f"2026-04-{20+i:02d}-session-{i}", description=f"Session {i}")

    result = _last_session_hint(wiki, max_notes=1)
    assert len(result) == 1


def test_title_preferred_over_description_and_summary(tmp_path: Path) -> None:
    """Revised notes carry an explicit ``title`` field (the slug source).
    SessionStart's status line wants the punchy short form, not the
    paragraph — so ``title`` wins over both ``description`` and
    ``summary`` when present.
    """
    wiki = tmp_path / "wiki" / "private"
    _write_session(
        wiki, "2026-04-22-revision-form",
        title="Add Ledger Feature",
        description="Added an append-only ledger module for tracking curator runs.",
        summary="(legacy paragraph that should be ignored when title is present)",
    )

    result = _last_session_hint(wiki, max_notes=1)
    assert len(result) == 1
    _, hint = result[0]
    assert hint == "Add Ledger Feature"


def test_falls_back_to_description_when_no_title(tmp_path: Path) -> None:
    """Legacy v2 notes have no ``title`` field — fall through to
    ``description`` (which on legacy notes was the short headline)."""
    wiki = tmp_path / "wiki" / "private"
    _write_session(wiki, "2026-04-22-desc-only", description="Just a description")

    result = _last_session_hint(wiki, max_notes=1)
    assert len(result) == 1
    _, hint = result[0]
    assert hint == "Just a description"


def test_falls_back_to_summary_for_truly_legacy_notes(tmp_path: Path) -> None:
    """Some old notes have only ``summary`` (no description, no title).
    Final fallback so we don't show empty status lines on real vaults
    that pre-date the schema-v2 split."""
    wiki = tmp_path / "wiki" / "private"
    _write_session(wiki, "2026-04-22-summary-only",
                    summary="Some old paragraph")

    result = _last_session_hint(wiki, max_notes=1)
    assert len(result) == 1
    _, hint = result[0]
    assert hint == "Some old paragraph"


# ---------------------------------------------------------------------------
# Phase 4: sharded-layout walking + no byte cap on frontmatter reads.
# ---------------------------------------------------------------------------


def test_walks_sharded_layout(tmp_path: Path) -> None:
    """Real session notes live at ``sessions/<YYYY>/<MM>/<DD>-slug.md``.
    The previous flat ``glob("*.md")`` only ever matched ``_recent.md``,
    so the hint silently went empty against any real vault."""
    wiki = tmp_path / "wiki" / "private"
    _write_session_sharded(wiki, year=2026, month=4, day=21, slug="earlier",
                            title="Earlier work")
    _write_session_sharded(wiki, year=2026, month=4, day=22, slug="latest",
                            title="Latest work")

    result = _last_session_hint(wiki, max_notes=2)
    assert len(result) == 2
    # Latest first (reverse-sorted by full path; YYYY/MM/DD prefix
    # sorts lexicographically the same as chronologically).
    assert result[0][1] == "Latest work"
    assert result[1][1] == "Earlier work"


def test_reads_full_frontmatter_no_byte_cap(tmp_path: Path) -> None:
    """A real note has SHA-256 hashes in ``source_transcripts`` and a
    long ``summary`` — the title-or-description-or-summary field can sit
    past 1024 bytes from the file head. The previous cap silently
    dropped it; this test plants a heavy frontmatter and asserts we
    still surface the title."""
    wiki = tmp_path / "wiki" / "private"
    big_provenance = "\n".join(
        f"  - {{integration: claude-code, id: {'x' * 36}, "
        f"from_hash: 'sha256:{'a' * 64}', to_hash: 'sha256:{'b' * 64}'}}"
        for _ in range(8)
    )
    extra = (
        "source_transcripts:\n"
        f"{big_provenance}\n"
        f"summary: '{'L' * 800}'"  # 800-char paragraph stays on one line
    )
    _write_session_sharded(
        wiki, year=2026, month=4, day=22, slug="heavy-frontmatter",
        title="Heavy frontmatter title",
        description="A short description.",
        extra_frontmatter=extra,
    )

    result = _last_session_hint(wiki, max_notes=1)
    assert len(result) == 1
    _, hint = result[0]
    assert hint == "Heavy frontmatter title"


def test_skips_non_session_notes_in_sharded_walk(tmp_path: Path) -> None:
    """A wiki may carry non-session markdown under sessions/ for
    historical reasons (cached pointers, README, etc.). They shouldn't
    pollute the hint."""
    wiki = tmp_path / "wiki" / "private"
    sessions_dir = wiki / "sessions"
    sessions_dir.mkdir(parents=True)
    # Cached pointer file with no proper session frontmatter.
    (sessions_dir / "_recent.md").write_text("# Recent\n\n- [[foo]]\n")
    # Real session in sharded layout.
    _write_session_sharded(wiki, year=2026, month=4, day=22, slug="real",
                            title="Real work")

    result = _last_session_hint(wiki, max_notes=2)
    assert len(result) == 1
    assert result[0] == ("22-real", "Real work")
