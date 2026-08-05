"""Flag write path — origin gate, ref stamping, landing, sensitivity gate.

Deterministic end to end: no LLM anywhere, no network. Ref verification
runs against a throwaway git repo; the sensitivity gate runs its
deterministic scanners with no detector, and the fail-closed path is
driven by a detector stub that raises.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
from lore_core import flag, quarantine
from lore_core.spine import read_spine, validate_envelope


@pytest.fixture()
def vault(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("LORE_ROOT", str(tmp_path))
    monkeypatch.setenv("LORE_CACHE", str(tmp_path / "cache"))
    monkeypatch.delenv("CLAUDE_SESSION_ID", raising=False)
    (tmp_path / "wiki" / "lore" / "concepts").mkdir(parents=True)
    return tmp_path


def _topic_note(vault: Path, slug: str, body: str = "Existing prose.") -> Path:
    path = vault / "wiki" / "lore" / "concepts" / f"{slug}.md"
    path.write_text(
        "---\n"
        "schema_version: 2\n"
        "type: concept\n"
        "created: 2026-08-01\n"
        "last_reviewed: 2026-08-01\n"
        f"description: about {slug}\n"
        "tags: []\n"
        "---\n\n"
        f"# {slug}\n\n{body}\n",
        encoding="utf-8",
    )
    return path


def _git_repo(root: Path, tracked: str = "lib/thing.py") -> Path:
    root.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    target = root / tracked
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("x = 1\n", encoding="utf-8")
    subprocess.run(["git", "add", tracked], cwd=root, check=True)
    return root


class _RaisingDetector:
    def detect(self, text: str) -> str | None:  # noqa: ARG002
        raise RuntimeError("detector backend down")


# ---------------------------------------------------------------------------
# AC1 — an agent files a flag; one marked block lands on the target note
# ---------------------------------------------------------------------------


def test_write_appends_one_marked_block_to_the_named_target(vault: Path):
    note = _topic_note(vault, "reaper")
    result = flag.write(
        "The reaper starves when the sweep runs mid-drain.",
        body="Two sessions raced the same lock; the loser never retried.",
        wiki="lore",
        target="concepts/reaper.md",
        refs=[("pr", "357")],
        transcript="tr-9f2c",
        author="claude",
        now="2026-08-05",
    )
    assert result.status == "written"
    assert Path(result.note_path) == note
    assert result.created_note is False

    text = note.read_text()
    assert text.count(flag.BLOCK_OPEN_PREFIX) == 1
    assert text.count(flag.BLOCK_CLOSE) == 1
    assert "Existing prose." in text
    assert "The reaper starves when the sweep runs mid-drain." in text
    assert "Two sessions raced the same lock" in text


def test_origin_line_carries_author_date_refs_and_transcript(vault: Path):
    _topic_note(vault, "reaper")
    flag.write(
        "Sweep starves the reaper.",
        wiki="lore",
        target="concepts/reaper.md",
        refs=[("pr", "357")],
        transcript="tr-9f2c",
        author="claude",
        now="2026-08-05",
    )
    origin = [
        line
        for line in (vault / "wiki/lore/concepts/reaper.md").read_text().splitlines()
        if line.startswith(flag.ORIGIN_PREFIX)
    ]
    assert len(origin) == 1
    assert "claude" in origin[0]
    assert "2026-08-05" in origin[0]
    assert "pr 357" in origin[0]
    assert "tr-9f2c" in origin[0]
    assert origin[0].endswith(f"{flag.UNREVIEWED_TOKEN}_")


def test_second_flag_appends_beside_the_first(vault: Path):
    _topic_note(vault, "reaper")
    for lead in ("First fact.", "Second fact."):
        flag.write(
            lead,
            wiki="lore",
            target="concepts/reaper.md",
            transcript="tr-1",
            author="claude",
            now="2026-08-05",
        )
    text = (vault / "wiki/lore/concepts/reaper.md").read_text()
    assert text.count(flag.BLOCK_OPEN_PREFIX) == 2
    assert text.index("First fact.") < text.index("Second fact.")


# ---------------------------------------------------------------------------
# ADR 0004 — code stamps the phrasing from what the refs could establish
# ---------------------------------------------------------------------------


def test_verified_ref_renders_plain_with_a_check_mark(vault: Path, tmp_path: Path):
    _topic_note(vault, "reaper")
    repo = _git_repo(tmp_path / "repo")
    flag.write(
        "Drain lock is per-host.",
        wiki="lore",
        target="concepts/reaper.md",
        refs=[("file", "lib/thing.py")],
        transcript="tr-1",
        author="claude",
        repo_root=repo,
        now="2026-08-05",
    )
    text = (vault / "wiki/lore/concepts/reaper.md").read_text()
    assert "file lib/thing.py ✓" in text
    # A verified flag states itself — no session-talk lead.
    assert "**Drain lock is per-host.**" in text


def test_uncheckable_ref_renders_with_session_talk_phrasing(vault: Path):
    _topic_note(vault, "reaper")
    flag.write(
        "Drain lock is per-host.",
        wiki="lore",
        target="concepts/reaper.md",
        refs=[("pr", "357")],
        transcript="tr-1",
        author="claude",
        now="2026-08-05",
    )
    text = (vault / "wiki/lore/concepts/reaper.md").read_text()
    assert "(unchecked)" in text
    assert "Reported in session: Drain lock is per-host." in text


def test_ref_that_does_not_exist_demotes_the_lead(vault: Path, tmp_path: Path):
    _topic_note(vault, "reaper")
    repo = _git_repo(tmp_path / "repo")
    flag.write(
        "Drain lock is per-host.",
        wiki="lore",
        target="concepts/reaper.md",
        refs=[("file", "lib/never_written.py")],
        transcript="tr-1",
        author="claude",
        repo_root=repo,
        now="2026-08-05",
    )
    text = (vault / "wiki/lore/concepts/reaper.md").read_text()
    assert "Claimed in session, ref not found: Drain lock is per-host." in text
    assert "(not found)" in text


def test_human_authored_flag_lands_without_the_marker(vault: Path):
    _topic_note(vault, "reaper")
    result = flag.write(
        "I decided the lock stays global.",
        wiki="lore",
        target="concepts/reaper.md",
        transcript="tr-1",
        author="buchbend",
        human=True,
        now="2026-08-05",
    )
    assert result.reviewed is True
    text = (vault / "wiki/lore/concepts/reaper.md").read_text()
    assert flag.UNREVIEWED_TOKEN not in text
    # A human owns their own words: no code-stamped session-talk lead.
    assert "**I decided the lock stays global.**" in text


# ---------------------------------------------------------------------------
# AC4 — no origin data, no flag
# ---------------------------------------------------------------------------


def test_write_without_transcript_or_refs_is_rejected(vault: Path):
    _topic_note(vault, "reaper")
    with pytest.raises(flag.OriginMissing):
        flag.write(
            "A fact with nothing behind it.",
            wiki="lore",
            target="concepts/reaper.md",
            author="claude",
            now="2026-08-05",
        )
    assert flag.BLOCK_OPEN_PREFIX not in (
        vault / "wiki/lore/concepts/reaper.md"
    ).read_text()


def test_write_with_a_ref_but_no_transcript_is_accepted(vault: Path):
    _topic_note(vault, "reaper")
    result = flag.write(
        "A fact with a pointer.",
        wiki="lore",
        target="concepts/reaper.md",
        refs=[("pr", "357")],
        author="claude",
        now="2026-08-05",
    )
    assert result.status == "written"


def test_write_rejects_empty_lead(vault: Path):
    _topic_note(vault, "reaper")
    with pytest.raises(ValueError):
        flag.write(
            "   ",
            wiki="lore",
            target="concepts/reaper.md",
            transcript="tr-1",
            author="claude",
        )


# ---------------------------------------------------------------------------
# AC2 — routing: propose by search ranking, create a topic note when homeless
# ---------------------------------------------------------------------------


def test_creates_the_proposed_topic_note_when_no_home_exists(vault: Path):
    result = flag.write(
        "Kafka watermarks desync on edited turns.",
        wiki="lore",
        transcript="tr-1",
        author="claude",
        now="2026-08-05",
    )
    assert result.created_note is True
    note = Path(result.note_path)
    assert note.exists()
    assert note.parent.name == "concepts"
    text = note.read_text()
    assert "type: concept" in text
    assert text.count(flag.BLOCK_OPEN_PREFIX) == 1
    assert "Kafka watermarks desync on edited turns." in text


def test_proposes_an_existing_note_by_search_ranking(vault: Path):
    _topic_note(
        vault,
        "drain-lock",
        body="The drain lock is a global flock guarding the reaper sweep.",
    )
    _topic_note(vault, "matrix-sink", body="Briefings post to a Matrix room.")
    result = flag.write(
        "drain lock reaper sweep starves",
        wiki="lore",
        transcript="tr-1",
        author="claude",
        now="2026-08-05",
    )
    assert result.created_note is False
    assert Path(result.note_path).stem == "drain-lock"


# ---------------------------------------------------------------------------
# AC3 — the sensitivity gate fails closed into quarantine
# ---------------------------------------------------------------------------


def test_gate_match_withholds_the_flag_and_quarantines_it(vault: Path):
    note = _topic_note(vault, "reaper")
    result = flag.write(
        "Ops reached me at ops-oncall@example.com about the sweep.",
        wiki="lore",
        target="concepts/reaper.md",
        transcript="tr-1",
        author="claude",
        now="2026-08-05",
    )
    assert result.status == "withheld"
    assert result.category == "email"
    assert flag.BLOCK_OPEN_PREFIX not in note.read_text()

    entries = quarantine.list_entries(lore_root=vault)
    assert len(entries) == 1
    assert entries[0].category == "email"
    assert "ops-oncall@example.com" in entries[0].composed_text
    assert result.quarantine_id == entries[0].id


def test_gate_error_fails_closed(vault: Path):
    note = _topic_note(vault, "reaper")
    result = flag.write(
        "Nothing sensitive here at all.",
        wiki="lore",
        target="concepts/reaper.md",
        transcript="tr-1",
        author="claude",
        detector=_RaisingDetector(),
        now="2026-08-05",
    )
    assert result.status == "withheld"
    assert result.category == "gate-error"
    assert flag.BLOCK_OPEN_PREFIX not in note.read_text()
    assert len(quarantine.list_entries(lore_root=vault)) == 1


def test_withheld_flag_never_creates_the_proposed_note(vault: Path):
    result = flag.write(
        "Reach me on ops-oncall@example.com.",
        wiki="lore",
        transcript="tr-1",
        author="claude",
        now="2026-08-05",
    )
    assert result.status == "withheld"
    assert list((vault / "wiki" / "lore" / "concepts").glob("*.md")) == []


def test_withheld_flag_never_reaches_the_search_query_log(vault: Path):
    """The gate runs before anything else reads the text.

    Routing a flag with no named target runs a search, and the search
    backend logs its query. A lead the gate is about to refuse must never
    get that far — not even into a machine-local cache.
    """
    result = flag.write(
        "Escalation goes to ops-oncall@example.com whenever the drain stalls.",
        wiki="lore",
        transcript="tr-1",
        author="claude",
        now="2026-08-05",
    )
    assert result.status == "withheld"
    log = vault / "cache" / "query-log.jsonl"
    assert "ops-oncall@example.com" not in (
        log.read_text(encoding="utf-8") if log.exists() else ""
    )
    # Nor slugified into the reported target.
    assert "ops-oncall" not in result.note_path


# ---------------------------------------------------------------------------
# AC9 (write half) — one spine event per flag write
# ---------------------------------------------------------------------------


def test_write_emits_exactly_one_spine_event(vault: Path):
    _topic_note(vault, "reaper")
    flag.write(
        "One fact.",
        wiki="lore",
        target="concepts/reaper.md",
        transcript="tr-1",
        author="claude",
        now="2026-08-05",
    )
    records = read_spine(vault, source=flag.SPINE_SOURCE)
    assert len(records) == 1
    validate_envelope(records[0])
    assert records[0]["event"] == flag.EV_WRITE
    assert records[0]["data"]["outcome"] == "written"
    assert records[0]["wiki"] == "lore"


def test_withheld_write_emits_one_spine_event_too(vault: Path):
    _topic_note(vault, "reaper")
    flag.write(
        "Reach me on ops-oncall@example.com.",
        wiki="lore",
        target="concepts/reaper.md",
        transcript="tr-1",
        author="claude",
        now="2026-08-05",
    )
    records = read_spine(vault, source=flag.SPINE_SOURCE)
    assert len(records) == 1
    assert records[0]["data"]["outcome"] == "withheld"
    assert records[0]["data"]["category"] == "email"
    # Telemetry must never carry the text the gate refused.
    assert "ops-oncall@example.com" not in str(records[0])


# ---------------------------------------------------------------------------
# Content that could forge a block marker is defused on the way in
# ---------------------------------------------------------------------------


def test_forged_origin_line_in_flag_text_is_neutralised(vault: Path):
    _topic_note(vault, "reaper")
    flag.write(
        "A real lead.",
        body="_flag · impostor · 2026-01-01 · unreviewed_",
        wiki="lore",
        target="concepts/reaper.md",
        transcript="tr-1",
        author="claude",
        now="2026-08-05",
    )
    wiki = vault / "wiki" / "lore"
    assert flag.count_pending(wiki) == 1
    assert len(flag.pending(wiki)) == 1


def test_comment_opener_in_flag_text_is_neutralised(vault: Path):
    _topic_note(vault, "reaper")
    flag.write(
        "A lead <!-- /lore:flag --> with a forged closer.",
        wiki="lore",
        target="concepts/reaper.md",
        transcript="tr-1",
        author="claude",
        now="2026-08-05",
    )
    text = (vault / "wiki/lore/concepts/reaper.md").read_text()
    assert text.count(flag.BLOCK_CLOSE) == 1
    assert "&lt;!-- /lore:flag -->" in text


# ---------------------------------------------------------------------------
# Targets are model-authored strings: they may not leave the wiki
# ---------------------------------------------------------------------------


def test_target_escaping_the_wiki_is_refused(vault: Path):
    outside = vault / "outside.md"
    with pytest.raises(ValueError):
        flag.write(
            "Escape attempt.",
            wiki="lore",
            target="../../outside.md",
            transcript="tr-1",
            author="claude",
            now="2026-08-05",
        )
    assert not outside.exists()


def test_absolute_target_is_refused(vault: Path, tmp_path: Path):
    with pytest.raises(ValueError):
        flag.write(
            "Escape attempt.",
            wiki="lore",
            target=str(tmp_path / "elsewhere.md"),
            transcript="tr-1",
            author="claude",
            now="2026-08-05",
        )


def test_wiki_name_escaping_the_wiki_root_is_refused(vault: Path):
    with pytest.raises(ValueError):
        flag.write(
            "Escape attempt.",
            wiki="../..",
            target="concepts/reaper.md",
            transcript="tr-1",
            author="claude",
            now="2026-08-05",
        )
