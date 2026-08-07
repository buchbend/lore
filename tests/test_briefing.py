"""Tests for `lore_core.briefing` and the `lore briefing` CLI.

`gather()`'s walk over `<wiki>/sessions/` was retired (PRD 0013): nothing
writes a session note since the compose pipeline was retired, so
`new_sessions` is always empty regardless of what a wiki holds. That makes
`lore briefing`'s one-shot pipeline always report "no new sessions" —
everything downstream of the sessions check (LLM compose, sink dispatch,
ledger mark) is unreachable through it now. What survives here: the ledger
primitives (`mark_incorporated`), the deterministic renderer and the LLM
prose composer (both pure functions over a `gather_result` dict), and the
gather/publish/mark CLI wiring that does not depend on real session content.

The chapter lifecycle (`create_note`, `append_chapter`, `Chapter`,
`TopicBlock`, `close_note`) that used to seed fixtures here is also gone
(issue #393) — `_seed_note`/`_append_topic_chapter`/`_close` below write the
same file shape by hand.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml
from lore_cli import briefing_cmd
from lore_core.briefing import gather, mark_incorporated, render_briefing
from lore_core.note_document import DISCLAIMER
from lore_core.schema import parse_frontmatter, strip_frontmatter

# ---------------------------------------------------------------------------
# Note-seeding helpers.
#
# The chapter lifecycle these fixtures used (create_note, append_chapter,
# Chapter, TopicBlock, close_note) was deleted with the compose pipeline
# (PRD 0013) — nothing writes a session note through it any more. These
# helpers write the same file shape by hand so the fixtures below still
# produce a real note on disk (gather() ignores it either way — see above).
# ---------------------------------------------------------------------------


def _seed_note(
    path, *, title, description, scope, created=None, extra_frontmatter=None, linkage=None
):
    day = created or "2026-01-01"
    fm = {
        "schema_version": 2,
        "type": "session",
        "note_status": "open",
        "created": day,
        "last_reviewed": day,
        "title": title,
        "description": description,
        "scope": scope,
        "chapters": [],
    }
    if extra_frontmatter:
        fm.update(extra_frontmatter)
    if linkage is not None:
        fm["linkage"] = {
            "schema_version": linkage.schema_version,
            "repo": linkage.repo,
            "branch": linkage.branch,
            "issues": list(linkage.issues),
            "prs": list(linkage.prs),
            "epics": list(linkage.epics),
            "author": linkage.author,
            "trace_id": linkage.trace_id,
        }
    path.parent.mkdir(parents=True, exist_ok=True)
    dumped = yaml.safe_dump(fm, sort_keys=False, allow_unicode=True).strip()
    path.write_text(f"---\n{dumped}\n---\n\n{DISCLAIMER}\n")


def _append_topic_chapter(path, *, lead, body="", anchor_turn, from_turn, to_turn):
    """Append a topic chapter in the exact shape the old renderer wrote."""
    fm = parse_frontmatter(path.read_text())
    existing_body = strip_frontmatter(path.read_text())
    n = len(fm.get("chapters") or []) + 1
    lead_para = f"**{lead}**"
    if body:
        lead_para = f"{lead_para} {body}"
    segment = f"<!-- lore:chapter {n} @{from_turn}-{to_turn} -->\n\n{lead_para}\n\n@{anchor_turn}"
    new_body = f"{existing_body.rstrip()}\n\n{segment}"
    chapters = list(fm.get("chapters") or [])
    chapters.append({"n": n, "kind": "topic", "from_turn": from_turn, "to_turn": to_turn})
    fm["chapters"] = chapters
    dumped = yaml.safe_dump(fm, sort_keys=False, allow_unicode=True).strip()
    path.write_text(f"---\n{dumped}\n---\n\n{new_body.rstrip()}\n")


def _close(path):
    fm = parse_frontmatter(path.read_text())
    body = strip_frontmatter(path.read_text())
    fm["note_status"] = "closed"
    dumped = yaml.safe_dump(fm, sort_keys=False, allow_unicode=True).strip()
    path.write_text(f"---\n{dumped}\n---\n\n{body.rstrip()}\n")


def _write_briefing_yaml(
    wiki: Path,
    sink: str = "markdown",
    target_path: str | None = None,
) -> None:
    body = f"sink: {sink}\n"
    if target_path is not None and sink == "markdown":
        body += f"markdown:\n  path: {target_path}\n"
    (wiki / ".lore-briefing.yml").write_text(body)


@pytest.fixture
def briefing_vault(tmp_path, monkeypatch):
    """A wiki holding real session notes — proves gather ignores them."""
    vault_root = tmp_path / "vault"
    wiki = vault_root / "wiki" / "ccat"
    (wiki / "sessions").mkdir(parents=True)

    for name in ["2026-04-15-fix-a", "2026-04-16-fix-b", "2026-04-17-fix-c"]:
        _seed_note(
            wiki / "sessions" / f"{name}.md",
            title=name[11:],
            description=f"session {name}",
            scope="lore:test",
            created=name[:10],
        )

    monkeypatch.setenv("LORE_ROOT", str(vault_root))
    return vault_root, wiki


# ---------------------------------------------------------------------------
# gather() ignores the sessions tree
# ---------------------------------------------------------------------------


def test_gather_reports_no_new_session_despite_real_session_files(briefing_vault) -> None:
    """AC: `gather` reports no new session for a wiki holding `sessions/`."""
    result = gather(wiki="ccat")
    assert "error" not in result
    assert result["new_sessions"] == []


def test_gather_unknown_wiki(briefing_vault):
    result = gather(wiki="nonexistent")
    assert "error" in result


def test_mark_incorporated_writes_ledger(briefing_vault):
    _, wiki = briefing_vault
    result = mark_incorporated(
        wiki="ccat",
        session_paths=["2026-04-15-fix-a.md", "2026-04-16-fix-b.md"],
    )
    assert result["incorporated_count"] == 2
    assert result["last_briefing"] is not None
    ledger = json.loads((wiki / ".briefing-ledger.json").read_text())
    assert "2026-04-15-fix-a.md" in ledger["incorporated"]
    assert "2026-04-16-fix-b.md" in ledger["incorporated"]


def test_mark_incorporated_idempotent(briefing_vault):
    _, _ = briefing_vault
    mark_incorporated(wiki="ccat", session_paths=["2026-04-15-fix-a.md"])
    result = mark_incorporated(wiki="ccat", session_paths=["2026-04-15-fix-a.md"])
    # No new additions on second call
    assert result["added"] == []
    assert result["incorporated_count"] == 1


def test_cli_gather_emits_envelope(briefing_vault, capsys):
    rc = briefing_cmd.main(["gather", "--wiki", "ccat"])
    assert rc == 0
    out = capsys.readouterr().out
    envelope = json.loads(out)
    assert envelope["schema"] == "lore.briefing.gather/1"
    assert envelope["data"]["wiki"] == "ccat"
    assert envelope["data"]["new_sessions"] == []


def test_cli_publish_markdown_sink(tmp_path, capsys, monkeypatch):
    """Publish a briefing via the markdown sink to a target file."""
    out_path = tmp_path / "out.md"
    monkeypatch.setattr(
        "sys.stdin",
        type(
            "S",
            (),
            {
                "read": staticmethod(lambda: "## Briefing\n\nbody\n"),
                "isatty": staticmethod(lambda: False),
            },
        )(),
    )
    rc = briefing_cmd.main(["publish", "--sink", "markdown", "--out", str(out_path), "--json"])
    assert rc == 0
    assert out_path.exists()
    assert "Briefing" in out_path.read_text()


def test_cli_mark_writes_and_emits(briefing_vault, capsys):
    rc = briefing_cmd.main(["mark", "--wiki", "ccat", "--session", "2026-04-15-fix-a.md"])
    assert rc == 0
    envelope = json.loads(capsys.readouterr().out)
    assert envelope["schema"] == "lore.briefing.mark/1"
    assert envelope["data"]["incorporated_count"] == 1


# ---------------------------------------------------------------------------
# Deterministic formatter
# ---------------------------------------------------------------------------


def test_render_briefing_empty_returns_empty_string():
    assert render_briefing({"new_sessions": []}) == ""


def test_render_briefing_uses_summary_frontmatter():
    result = {
        "wiki": "ccat",
        "today": "2026-04-29",
        "ledger": {"last_briefing": "2026-04-28", "incorporated_count": 0},
        "new_sessions": [
            {
                "path": "sessions/2026-04-29-thing.md",
                "date": "2026-04-29",
                "slug": "thing",
                "frontmatter": {"summary": "shipped the thing"},
                "body": "**Shipped the thing.**\n\n@12",
            }
        ],
    }
    out = render_briefing(result)
    assert "# Briefing — 2026-04-29 · ccat" in out
    assert "1 session since 2026-04-28." in out
    assert "## 2026-04-29" in out
    assert "- **thing** — shipped the thing" in out


def test_render_briefing_falls_back_to_description():
    """No `summary` frontmatter: falls straight to `description` — the new
    note shape has no H2 sections to mine a bullet from, so there is no
    intermediate tier between summary and description."""
    result = {
        "wiki": "ccat",
        "today": "2026-04-29",
        "ledger": {"last_briefing": None, "incorporated_count": 0},
        "new_sessions": [
            {
                "path": "p",
                "date": "2026-04-29",
                "slug": "thing",
                "frontmatter": {"description": "fallback line"},
                "body": "**Did the thing.**\n\n@12",
            }
        ],
    }
    out = render_briefing(result)
    assert "- **thing** — fallback line" in out
    assert "since the start" in out


def test_render_briefing_groups_by_date_descending():
    result = {
        "wiki": "ccat",
        "today": "2026-04-29",
        "ledger": {"last_briefing": None, "incorporated_count": 0},
        "new_sessions": [
            {
                "path": "p1",
                "date": "2026-04-15",
                "slug": "older",
                "frontmatter": {"summary": "older work"},
                "body": "",
            },
            {
                "path": "p2",
                "date": "2026-04-29",
                "slug": "newer",
                "frontmatter": {"summary": "newer work"},
                "body": "",
            },
        ],
    }
    out = render_briefing(result)
    assert "2 sessions since the start." in out
    # Newer date comes first.
    assert out.index("## 2026-04-29") < out.index("## 2026-04-15")


def test_render_briefing_links_to_source_note():
    """Digest bullets carry a wikilink back to the source session note."""
    result = {
        "wiki": "ccat",
        "today": "2026-04-29",
        "ledger": {"last_briefing": None, "incorporated_count": 0},
        "new_sessions": [
            {
                "path": "sessions/2026-04-29-thing.md",
                "date": "2026-04-29",
                "slug": "thing",
                "frontmatter": {"summary": "shipped the thing"},
                "linkage": {},
                "body": "",
            }
        ],
    }
    out = render_briefing(result)
    # Pre-existing bullet text stays intact (backward compatible).
    assert "- **thing** — shipped the thing" in out
    assert "[[2026-04-29-thing]]" in out


def test_render_briefing_shows_drill_down_refs():
    """Author + epic/issue refs ride along so a reader can drill down."""
    result = {
        "wiki": "ccat",
        "today": "2026-04-29",
        "ledger": {"last_briefing": None, "incorporated_count": 0},
        "new_sessions": [
            {
                "path": "sessions/2026-04-29-thing.md",
                "date": "2026-04-29",
                "slug": "thing",
                "frontmatter": {"summary": "shipped the thing"},
                "linkage": {"author": "Alice", "epics": [162], "issues": [175]},
                "body": "",
            }
        ],
    }
    out = render_briefing(result)
    assert "Alice" in out
    assert "epic #162" in out
    assert "#175" in out


# ---------------------------------------------------------------------------
# One-shot `lore briefing --wiki ...` — always reports no new sessions
# ---------------------------------------------------------------------------


def test_oneshot_reports_no_new_sessions(tmp_path, capsys, monkeypatch):
    """Nothing downstream of the sessions check is reachable any more:
    LLM compose, sink dispatch and ledger mark all sit past this return."""
    vault_root = tmp_path / "vault"
    wiki = vault_root / "wiki" / "ccat"
    wiki.mkdir(parents=True)
    _write_briefing_yaml(wiki, target_path=str(tmp_path / "never.md"))
    monkeypatch.setenv("LORE_ROOT", str(vault_root))

    rc = briefing_cmd.main(["--wiki", "ccat"])
    assert rc == 0
    err = capsys.readouterr().err
    assert "no new sessions" in err.lower()


def test_oneshot_no_args_shows_help(tmp_path, capsys, monkeypatch):
    monkeypatch.setenv("LORE_ROOT", str(tmp_path / "vault"))
    rc = briefing_cmd.main([])
    assert rc == 0
    out = capsys.readouterr().out
    assert "briefing" in out.lower()


# ---------------------------------------------------------------------------
# LLM-composed briefing prose — a pure function over a gather_result dict,
# independent of the (now-empty) sessions walk.
# ---------------------------------------------------------------------------


class _FakeBlock:
    def __init__(self, text: str) -> None:
        self.text = text


class _FakeResp:
    def __init__(self, text: str) -> None:
        self.content = [_FakeBlock(text)]


class _FakeMessages:
    def __init__(self, *, text: str = "", raises: Exception | None = None) -> None:
        self._text = text
        self._raises = raises
        self.calls: list[dict] = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        if self._raises is not None:
            raise self._raises
        return _FakeResp(self._text)


class _FakeClient:
    def __init__(self, *, text: str = "", raises: Exception | None = None) -> None:
        self.messages = _FakeMessages(text=text, raises=raises)


def test_compose_briefing_prose_returns_llm_text():
    from lore_core.briefing import compose_briefing_prose

    fake = _FakeClient(text="## Briefing: 2026-04-29 (ccat)\n\n### What happened\n- shipped\n")
    out = compose_briefing_prose(
        gather_result={
            "wiki": "ccat",
            "today": "2026-04-29",
            "ledger": {"last_briefing": None},
            "new_sessions": [
                {
                    "path": "p",
                    "date": "2026-04-29",
                    "slug": "thing",
                    "frontmatter": {"summary": "shipped the thing"},
                    "body": "**Shipped the thing.**\n\nWired up the last piece.\n\n@12",
                }
            ],
        },
        llm_client=fake,
        model_resolver=lambda t: "claude-sonnet-4-6",
    )
    assert "Briefing" in out
    assert fake.messages.calls[0]["model"] == "claude-sonnet-4-6"
    prompt = fake.messages.calls[0]["messages"][0]["content"]
    assert "shipped the thing" in prompt


def test_compose_briefing_prompt_includes_linkage():
    """Author + epic ride into the prompt so the LLM can key digests on them."""
    from lore_core.briefing import compose_briefing_prose

    fake = _FakeClient(text="## Briefing: 2026-04-29 (ccat)\n")
    compose_briefing_prose(
        gather_result={
            "wiki": "ccat",
            "today": "2026-04-29",
            "ledger": {"last_briefing": None},
            "new_sessions": [
                {
                    "path": "p",
                    "date": "2026-04-29",
                    "slug": "thing",
                    "frontmatter": {"summary": "shipped thing"},
                    "linkage": {"author": "Alice", "epics": [162]},
                    "body": "",
                }
            ],
        },
        llm_client=fake,
        model_resolver=lambda t: "claude-sonnet-4-6",
    )
    prompt = fake.messages.calls[0]["messages"][0]["content"]
    assert "Alice" in prompt
    assert "epic #162" in prompt


def test_compose_briefing_prose_empty_input_short_circuits():
    from lore_core.briefing import compose_briefing_prose

    fake = _FakeClient(text="should-not-appear")
    out = compose_briefing_prose(
        gather_result={"new_sessions": []},
        llm_client=fake,
        model_resolver=lambda t: "claude-sonnet-4-6",
    )
    assert out == ""
    assert fake.messages.calls == []
