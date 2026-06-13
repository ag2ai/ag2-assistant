"""Tests for outbound Markdown -> plain-text formatting."""

from agclaw.channels.formatting import markdown_to_plain


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
