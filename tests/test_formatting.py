"""Tests for outbound Markdown -> plain-text formatting and length splitting."""

from assistant.channels.formatting import markdown_to_plain, split_for_limit


def test_strips_bold_and_italic():
    assert markdown_to_plain("This is **bold** and *italic* text.") == (
        "This is bold and italic text."
    )


def test_bold_italic_underscore():
    assert markdown_to_plain("__strong__ and _em_") == "strong and em"


def test_headings_become_plain_lines():
    assert markdown_to_plain("# Weather\n\nSunny") == "Weather\n\nSunny"


def test_bullets_become_dots():
    out = markdown_to_plain("- one\n- two\n* three")
    assert out == "• one\n• two\n• three"


def test_inline_code_unwrapped():
    assert markdown_to_plain("Run `pytest` now") == "Run pytest now"


def test_code_fence_content_kept():
    md = "Here:\n```python\nprint(1)\n```"
    out = markdown_to_plain(md)
    assert "print(1)" in out
    assert "```" not in out


def test_links_become_text_and_url():
    assert markdown_to_plain("See [AG2](https://ag2.ai)") == "See AG2 (https://ag2.ai)"


def test_horizontal_rule_removed():
    out = markdown_to_plain("above\n\n---\n\nbelow")
    assert "---" not in out
    assert "above" in out and "below" in out


def test_bold_inside_bullet():
    assert markdown_to_plain("- **High**: 21C") == "• High: 21C"


def test_collapses_blank_lines():
    assert markdown_to_plain("a\n\n\n\nb") == "a\n\nb"


def test_plain_text_unchanged():
    assert markdown_to_plain("Just a normal sentence.") == "Just a normal sentence."


def test_realistic_weather_reply():
    md = (
        "## Weather in Sydney\n\n"
        "- **Now**: 18C, partly cloudy\n"
        "- **High**: 21C  **Low**: 14C\n\n"
        "Rain likely after 4pm."
    )
    out = markdown_to_plain(md)
    assert "**" not in out
    assert "##" not in out
    assert "• Now: 18C, partly cloudy" in out
    assert "Weather in Sydney" in out


# --- split_for_limit: paragraph -> sentence -> hard split, code blocks kept whole ---


def test_text_within_the_limit_is_one_chunk():
    assert split_for_limit("short answer", 100) == ["short answer"]


def test_breaks_fall_on_paragraph_boundaries():
    text = "A" * 10 + "\n\n" + "B" * 10
    assert split_for_limit(text, 12) == ["A" * 10, "B" * 10]


def test_breaks_fall_on_sentence_boundaries_inside_a_paragraph():
    text = "One sentence here. Two sentence here."
    assert split_for_limit(text, 25) == ["One sentence here.", "Two sentence here."]


def test_a_paragraph_longer_than_the_limit_is_hard_split():
    chunks = split_for_limit("x" * 30, 10)
    assert chunks == ["x" * 10] * 3
    assert "".join(chunks) == "x" * 30


def test_nothing_is_dropped():
    """Only the whitespace a break lands on is consumed; every word survives."""
    text = "One sentence here. Two sentence here.\n\n" + "y" * 40
    chunks = split_for_limit(text, 18)
    assert all(len(chunk) <= 18 for chunk in chunks)
    assert "".join(chunks) == "One sentence here." + "Two sentence here." + "y" * 40


def test_a_fenced_code_block_is_never_split():
    code = "```python\n" + "print(1)\n" * 3 + "```"
    text = f"Intro paragraph.\n\n{code}\n\nOutro."
    chunks = split_for_limit(text, 45)
    assert code in chunks
    assert all(chunk.count("```") % 2 == 0 for chunk in chunks)


def test_a_code_block_too_big_for_one_message_is_re_fenced_per_part():
    """It has to be broken, but every part still has to arrive as code rather than
    the tail rendering as prose."""
    code = "```python\n" + "print(1)\n" * 12 + "```"
    chunks = split_for_limit(code, 60)
    assert len(chunks) > 1
    for chunk in chunks:
        assert len(chunk) <= 60
        assert chunk.startswith("```python\n")
        assert chunk.endswith("\n```")
    body = "".join(chunk[len("```python\n") : -len("\n```")] for chunk in chunks)
    assert body == "print(1)\n" * 11 + "print(1)"


def test_every_chunk_stays_within_the_limit():
    text = "\n\n".join(f"Paragraph {i}. " + "word " * 40 for i in range(5))
    assert all(len(chunk) <= 200 for chunk in split_for_limit(text, 200))
