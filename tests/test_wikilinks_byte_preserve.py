"""Byte-preservation: no collapsing of whitespace around frontmatter."""

from lore_core.wikilinks import strip_broken_wikilinks


def test_blank_line_after_frontmatter_preserved():
    text = (
        "---\n"
        "type: concept\n"
        "---\n"
        "\n"
        "# Title\n"
        "\n"
        "Body has [[ghost]] in it.\n"
    )
    out, n, _ = strip_broken_wikilinks(text, set())
    assert n == 1
    expected = (
        "---\n"
        "type: concept\n"
        "---\n"
        "\n"
        "# Title\n"
        "\n"
        "Body has ghost in it.\n"
    )
    assert out == expected, f"whitespace not preserved:\n{out!r}\nvs\n{expected!r}"


def test_no_changes_means_byte_identical():
    text = (
        "---\n"
        "type: concept\n"
        "---\n"
        "\n\n\n"  # multiple blank lines after fm
        "Body without any wikilinks.\n"
    )
    out, n, _ = strip_broken_wikilinks(text, set())
    assert n == 0
    assert out == text
