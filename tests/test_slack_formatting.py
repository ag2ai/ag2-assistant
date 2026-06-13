"""Tests for Markdown -> Slack mrkdwn conversion."""

from agclaw.channels.formatting import markdown_to_slack


def test_bold_double_to_single_asterisk():
    assert markdown_to_slack("This is **bold**.") == "This is *bold*."


def test_underscore_bold_to_single_asterisk():
    assert markdown_to_slack("__strong__") == "*strong*"


def test_italic_asterisk_to_underscore():
    assert markdown_to_slack("This is *italic*.") == "This is _italic_."


def test_underscore_italic_kept():
    assert markdown_to_slack("This is _italic_.") == "This is _italic_."


def test_heading_becomes_bold():
    assert markdown_to_slack("# Weather") == "*Weather*"


def test_bullets_become_dots():
    assert markdown_to_slack("- one\n- two") == "• one\n• two"


def test_bullet_with_bold():
    assert markdown_to_slack("- **High**: 21C") == "• *High*: 21C"


def test_link_becomes_slack_format():
    assert markdown_to_slack("See [AG2](https://ag2.ai)") == "See <https://ag2.ai|AG2>"


def test_inline_code_kept():
    assert markdown_to_slack("Run `pytest`") == "Run `pytest`"


def test_code_block_kept():
    md = "```python\nprint(1)\n```"
    assert markdown_to_slack(md) == "```python\nprint(1)\n```"


def test_code_block_protected_from_bold():
    # Asterisks inside code must not be converted.
    md = "```\na ** b\n```"
    assert markdown_to_slack(md) == "```\na ** b\n```"


def test_realistic_reply():
    md = "## Tips\n\n- **First**: do X\n- *maybe* do Y\n\nSee [docs](https://x.io)"
    out = markdown_to_slack(md)
    assert "*Tips*" in out
    assert "• *First*: do X" in out
    assert "_maybe_ do Y" in out
    assert "<https://x.io|docs>" in out
    assert "**" not in out
