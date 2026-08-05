"""Flag review walk — pending scan, accept / retarget / decline / skip.

Pending state is derived by scanning notes for the unreviewed marker
(ADR 0008): no queue store exists, so every assertion here reads the
notes themselves.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from lore_cli.__main__ import app
from lore_core import flag
from lore_core.spine import read_spine, validate_envelope
from typer.testing import CliRunner

runner = CliRunner()


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


def _file(vault: Path, slug: str = "reaper") -> Path:
    return vault / "wiki" / "lore" / "concepts" / f"{slug}.md"


def _flag(vault: Path, lead: str, *, target: str = "concepts/reaper.md", **kw):
    return flag.write(
        lead,
        wiki="lore",
        target=target,
        transcript="tr-1",
        author="claude",
        now="2026-08-05",
        **kw,
    )


def _wiki(vault: Path) -> Path:
    return vault / "wiki" / "lore"


# ---------------------------------------------------------------------------
# Pending state is derived by scanning, never stored
# ---------------------------------------------------------------------------


def test_pending_lists_unreviewed_flags_only(vault: Path):
    _topic_note(vault, "reaper")
    _flag(vault, "Unreviewed fact.")
    _flag(vault, "Human fact.", human=True)
    pending = flag.pending(_wiki(vault))
    assert len(pending) == 1
    # `lead` is the rendered line, so it carries the code-owned stamp.
    assert pending[0].lead == "Reported in session: Unreviewed fact."


def test_pending_reports_the_note_each_flag_sits_in(vault: Path):
    _topic_note(vault, "reaper")
    _topic_note(vault, "drain")
    _flag(vault, "In reaper.")
    _flag(vault, "In drain.", target="concepts/drain.md")
    homes = {
        p.lead.split(": ", 1)[1]: Path(p.note_path).stem
        for p in flag.pending(_wiki(vault))
    }
    assert homes == {"In reaper.": "reaper", "In drain.": "drain"}


def test_count_pending_matches_the_pending_list(vault: Path):
    _topic_note(vault, "reaper")
    for i in range(3):
        _flag(vault, f"Fact {i}.")
    assert flag.count_pending(_wiki(vault)) == 3


def test_count_pending_is_zero_on_a_clean_wiki(vault: Path):
    _topic_note(vault, "reaper")
    assert flag.count_pending(_wiki(vault)) == 0


# ---------------------------------------------------------------------------
# AC6 — accept removes the marker and changes nothing else
# ---------------------------------------------------------------------------


def test_accept_removes_only_the_marker(vault: Path):
    _topic_note(vault, "reaper")
    result = _flag(vault, "Keep this.", refs=[("pr", "357")])
    before = _file(vault).read_text()

    assert flag.accept(_wiki(vault), result.flag_id) is True

    after = _file(vault).read_text()
    assert flag.UNREVIEWED_TOKEN not in after
    assert after == before.replace(f" · {flag.UNREVIEWED_TOKEN}", "")
    assert flag.count_pending(_wiki(vault)) == 0


def test_accept_leaves_a_sibling_flag_pending(vault: Path):
    _topic_note(vault, "reaper")
    first = _flag(vault, "First.")
    _flag(vault, "Second.")
    flag.accept(_wiki(vault), first.flag_id)
    still_pending = flag.pending(_wiki(vault))
    assert len(still_pending) == 1
    assert still_pending[0].lead.endswith("Second.")


# ---------------------------------------------------------------------------
# AC7 — decline deletes the block
# ---------------------------------------------------------------------------


def test_decline_deletes_the_block_and_leaves_the_note(vault: Path):
    _topic_note(vault, "reaper")
    result = _flag(vault, "Drop this.")
    assert flag.decline(_wiki(vault), result.flag_id) is True

    text = _file(vault).read_text()
    assert "Drop this." not in text
    assert flag.BLOCK_OPEN_PREFIX not in text
    assert "Existing prose." in text
    assert text.startswith("---\n")


def test_decline_removes_only_the_named_block(vault: Path):
    _topic_note(vault, "reaper")
    first = _flag(vault, "Drop this.")
    _flag(vault, "Keep this.")
    flag.decline(_wiki(vault), first.flag_id)
    text = _file(vault).read_text()
    assert "Drop this." not in text
    assert "Keep this." in text


# ---------------------------------------------------------------------------
# Retarget moves the block; skip leaves it alone
# ---------------------------------------------------------------------------


def test_retarget_moves_the_block_to_another_note(vault: Path):
    _topic_note(vault, "reaper")
    _topic_note(vault, "drain")
    result = _flag(vault, "Belongs in drain.")

    moved = flag.retarget(_wiki(vault), result.flag_id, "concepts/drain.md")
    assert Path(moved).stem == "drain"

    assert "Belongs in drain." not in _file(vault, "reaper").read_text()
    assert "Belongs in drain." in _file(vault, "drain").read_text()
    # The verdict is a routing correction, not an endorsement (ADR 0008).
    assert flag.count_pending(_wiki(vault)) == 1


def test_retarget_creates_the_destination_when_it_is_missing(vault: Path):
    _topic_note(vault, "reaper")
    result = _flag(vault, "Needs a new home.")
    moved = Path(flag.retarget(_wiki(vault), result.flag_id, "concepts/brand-new.md"))
    assert moved.exists()
    assert "Needs a new home." in moved.read_text()


def test_unknown_flag_id_is_a_no_op(vault: Path):
    _topic_note(vault, "reaper")
    _flag(vault, "Untouched.")
    assert flag.accept(_wiki(vault), "deadbeefdead") is False
    assert flag.decline(_wiki(vault), "deadbeefdead") is False
    assert flag.count_pending(_wiki(vault)) == 1


# ---------------------------------------------------------------------------
# AC9 (review half) — one spine event per verdict
# ---------------------------------------------------------------------------


def _review_events(vault: Path) -> list[dict]:
    return [
        r
        for r in read_spine(vault, source=flag.SPINE_SOURCE)
        if r["event"] == flag.EV_REVIEW
    ]


def test_each_verdict_emits_one_spine_event(vault: Path):
    _topic_note(vault, "reaper")
    _topic_note(vault, "drain")
    accepted = _flag(vault, "Accept me.")
    declined = _flag(vault, "Decline me.")
    moved = _flag(vault, "Move me.")

    flag.accept(_wiki(vault), accepted.flag_id)
    flag.decline(_wiki(vault), declined.flag_id)
    flag.retarget(_wiki(vault), moved.flag_id, "concepts/drain.md")

    events = _review_events(vault)
    assert [e["data"]["verdict"] for e in events] == ["accept", "decline", "retarget"]
    for e in events:
        validate_envelope(e)


def test_a_no_op_verdict_emits_nothing(vault: Path):
    _topic_note(vault, "reaper")
    flag.accept(_wiki(vault), "deadbeefdead")
    assert _review_events(vault) == []


# ---------------------------------------------------------------------------
# AC5 — `lore flag review` presents accept / retarget / decline / skip
# ---------------------------------------------------------------------------


def test_review_walk_presents_the_four_verdicts(vault: Path):
    _topic_note(vault, "reaper")
    _flag(vault, "Look at me.")
    result = runner.invoke(app, ["flag", "review", "--wiki", "lore"], input="s\n")
    assert result.exit_code == 0, result.output
    assert "Look at me." in result.output
    for verdict in ("accept", "retarget", "decline", "skip"):
        assert verdict in result.output.lower()


def test_review_walk_skip_leaves_the_flag_pending(vault: Path):
    _topic_note(vault, "reaper")
    _flag(vault, "Still pending.")
    runner.invoke(app, ["flag", "review", "--wiki", "lore"], input="s\n")
    assert flag.count_pending(_wiki(vault)) == 1


def test_review_walk_accept_clears_the_marker(vault: Path):
    _topic_note(vault, "reaper")
    _flag(vault, "Accept via the walk.")
    result = runner.invoke(app, ["flag", "review", "--wiki", "lore"], input="a\n")
    assert result.exit_code == 0, result.output
    assert flag.count_pending(_wiki(vault)) == 0
    assert "Accept via the walk." in _file(vault).read_text()


def test_review_walk_decline_deletes_the_block(vault: Path):
    _topic_note(vault, "reaper")
    _flag(vault, "Decline via the walk.")
    runner.invoke(app, ["flag", "review", "--wiki", "lore"], input="d\n")
    assert "Decline via the walk." not in _file(vault).read_text()


def test_review_walk_retarget_prompts_for_the_destination(vault: Path):
    _topic_note(vault, "reaper")
    _topic_note(vault, "drain")
    _flag(vault, "Retarget via the walk.")
    result = runner.invoke(
        app, ["flag", "review", "--wiki", "lore"], input="r\nconcepts/drain.md\n"
    )
    assert result.exit_code == 0, result.output
    assert "Retarget via the walk." in _file(vault, "drain").read_text()


def test_review_walk_on_an_empty_queue_says_so(vault: Path):
    _topic_note(vault, "reaper")
    result = runner.invoke(app, ["flag", "review", "--wiki", "lore"])
    assert result.exit_code == 0, result.output
    assert "no pending flags" in result.output.lower()


# ---------------------------------------------------------------------------
# `lore flag` write verb
# ---------------------------------------------------------------------------


def test_cli_write_lands_a_human_flag_without_the_marker(vault: Path):
    _topic_note(vault, "reaper")
    result = runner.invoke(
        app,
        [
            "flag",
            "write",
            "Humans own their own words.",
            "--wiki",
            "lore",
            "--target",
            "concepts/reaper.md",
            "--transcript",
            "tr-1",
        ],
    )
    assert result.exit_code == 0, result.output
    text = _file(vault).read_text()
    assert "Humans own their own words." in text
    assert flag.UNREVIEWED_TOKEN not in text


def test_cli_write_without_origin_exits_nonzero(vault: Path):
    _topic_note(vault, "reaper")
    result = runner.invoke(
        app,
        ["flag", "write", "No origin.", "--wiki", "lore", "--target", "concepts/reaper.md"],
    )
    assert result.exit_code != 0
    assert flag.BLOCK_OPEN_PREFIX not in _file(vault).read_text()
