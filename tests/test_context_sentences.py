"""The repo glossary obeys the writing rules' sentence ceiling.

The ceiling and its regex come from the packaged Vale rule
(`WritingRules/SentenceLength.yml`), so the linter and this test can never
disagree about the number. The `vale` binary itself is not run here: Vale is
PATH-detected and never bundled (ADR 0006), and CI installs no Vale, so a
Vale-driven check would skip on every machine that matters instead of guarding
the file. Segmenting markdown into sentences is the only part this file owns.
"""

from __future__ import annotations

import re
from pathlib import Path

import yaml
from lore_core.style import default_vale_config_path

REPO_ROOT = Path(__file__).resolve().parent.parent

# A period that ends a sentence, not one inside an abbreviation or a file name.
# The first lookbehind rejects a single letter standing after a space or a
# period, which covers an initial; ABBREVIATIONS covers the longer forms. The
# trailing class lets a sentence end inside markup, as in `**Startup sweep.**`.
ABBREVIATIONS = ("etc", "vs", "cf", "approx", "resp")
_NOT_ABBREVIATION = "".join(rf"(?<!{re.escape(word)})" for word in ABBREVIATIONS)
SENTENCE_END = re.compile(rf"(?<![\s.][A-Za-z]){_NOT_ABBREVIATION}[.!?][*_`\"\')\]]*(?=\s|$)")
LIST_ITEM = re.compile(r"\s*([-*+]|\d+\.)\s")


def _sentence_length_rule() -> tuple[re.Pattern[str], int]:
    """The Vale rule's own regex and the word count it allows."""
    path = default_vale_config_path().parent / "WritingRules" / "SentenceLength.yml"
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))["raw"][0]
    ceiling = re.search(r"\{(\d+),\}", raw)
    assert ceiling, f"the Vale sentence rule lost its word count: {raw!r}"
    return re.compile(raw), int(ceiling.group(1))


def _prose_blocks(text: str) -> list[str]:
    """Markdown prose, one block per paragraph or list item.

    Fenced code, headings and table rows carry no sentences, and a heading left
    in place would glue its words onto the paragraph below it.
    """
    blocks: list[list[str]] = [[]]
    in_fence = False
    for line in text.splitlines():
        if line.startswith("```"):
            in_fence = not in_fence
            continue
        stripped = line.strip()
        if in_fence or stripped.startswith("#") or stripped.startswith("|"):
            continue
        if not stripped or LIST_ITEM.match(line):
            blocks.append([])
        blocks[-1].append(stripped)
    return [joined for block in blocks if (joined := " ".join(block).strip())]


def _sentences(text: str) -> list[str]:
    return [
        cleaned
        for block in _prose_blocks(text)
        for part in SENTENCE_END.split(block)
        if (cleaned := " ".join(part.split()))
    ]


def test_context_md_sentences_stay_inside_the_ceiling() -> None:
    """A definition nobody can read fixes nothing — the glossary obeys the same
    sentence rules as the issue text that cites it."""
    over_long, ceiling = _sentence_length_rule()
    offenders = [
        f"{len(sentence.split())} words: {sentence}"
        for sentence in _sentences((REPO_ROOT / "CONTEXT.md").read_text(encoding="utf-8"))
        if over_long.search(sentence)
    ]
    assert not offenders, (
        f"{len(offenders)} sentences run past the {ceiling}-word ceiling:\n" + "\n".join(offenders)
    )


def test_the_format_spec_names_the_sentence_ceiling() -> None:
    """An agent writing a definition reads the format spec, not this test, so
    the spec has to name the ceiling and point at the document holding it."""
    spec = (REPO_ROOT / "lore-workflow/skills/domain-modeling/CONTEXT-FORMAT.md").read_text(
        encoding="utf-8"
    )
    assert "sentence ceiling" in spec
    assert "lore style show writing-rules" in spec


def test_the_sentence_check_catches_a_long_sentence() -> None:
    """Guards the segmenter: a check that never fires would pass on any file."""
    over_long, ceiling = _sentence_length_rule()
    sentence = " ".join(["word"] * (ceiling + 1)) + "."
    assert [s for s in _sentences(f"# Title\n\n{sentence}\n") if over_long.search(s)]


def test_an_abbreviation_does_not_split_a_long_sentence() -> None:
    """A period inside an abbreviation is not a sentence end. Treating it as
    one hides an over-long sentence behind two short halves."""
    over_long, ceiling = _sentence_length_rule()
    for abbreviation in ABBREVIATIONS:
        head = "word " * 7
        tail = "word " * (ceiling - 6)
        sentence = f"{head}{abbreviation}. {tail}".strip() + "."
        assert [s for s in _sentences(sentence) if over_long.search(s)], abbreviation
