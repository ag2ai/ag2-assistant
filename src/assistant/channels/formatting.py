"""Outbound formatting helpers — render an agent's Markdown reply per channel.

The agent emits Markdown (ideal for a web UI). Chat channels like Telegram show
raw Markdown literally, so we convert it. `markdown_to_plain` produces clean plain
text — no parse modes, so it can never trigger a platform parse error.
"""

import re


def markdown_to_plain(text: str) -> str:
    """Convert Markdown to tidy plain text.

    Bold/italic markers are removed, inline/blocked code is unwrapped, headings
    become plain lines, list items become `•` bullets, and links become
    `text (url)`.
    """
    # Fenced code blocks: drop the ``` fences, keep the inner content.
    text = re.sub(r"```[^\n]*\n", "", text)
    text = text.replace("```", "")

    # Inline code: strip the backticks.
    text = re.sub(r"`([^`]+)`", r"\1", text)

    # Images then links.
    text = re.sub(r"!\[([^\]]*)\]\([^)]*\)", r"\1", text)
    text = re.sub(
        r"\[([^\]]+)\]\(([^)]+)\)",
        lambda m: m.group(1) if m.group(1) == m.group(2) else f"{m.group(1)} ({m.group(2)})",
        text,
    )

    # Line-level: horizontal rules, headings, blockquotes, bullets.
    out_lines: list[str] = []
    for line in text.split("\n"):
        if re.match(r"^\s*([-*_])\1{2,}\s*$", line):  # --- *** ___ rule
            continue
        line = re.sub(r"^\s*#{1,6}\s+", "", line)  # heading markers
        line = re.sub(r"^\s*>\s?", "", line)  # blockquote
        line = re.sub(r"^(\s*)[-*+]\s+", r"\1• ", line)  # bullet
        out_lines.append(line)
    text = "\n".join(out_lines)

    # Emphasis: ***x*** / **x** / *x* / __x__ / _x_
    text = re.sub(r"\*\*\*([^*]+)\*\*\*", r"\1", text)
    text = re.sub(r"\*\*([^*]+)\*\*", r"\1", text)
    text = re.sub(r"\*([^*]+)\*", r"\1", text)
    text = re.sub(r"__([^_]+)__", r"\1", text)
    text = re.sub(r"_([^_]+)_", r"\1", text)

    # Collapse excessive blank lines.
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def markdown_to_slack(text: str) -> str:
    """Convert standard Markdown to Slack 'mrkdwn'.

    Slack differs from Markdown: bold is `*x*` (single asterisk), italic is `_x_`,
    links are `<url|text>`, and there are no headings. Code spans/blocks and
    underscore-italics carry over unchanged.
    """
    BOLD = "\x01"  # temporary marker so bold asterisks survive italic conversion
    stash: dict[str, str] = {}

    def _stash(m: "re.Match") -> str:
        key = f"\x00{len(stash)}\x00"
        stash[key] = m.group(0)
        return key

    # Protect code (Slack supports ``` and ` natively) from other rules.
    text = re.sub(r"```.*?```", _stash, text, flags=re.S)
    text = re.sub(r"`[^`]+`", _stash, text)

    # Images then links -> <url|text>.
    text = re.sub(r"!\[([^\]]*)\]\([^)]*\)", r"\1", text)
    text = re.sub(r"\[([^\]]+)\]\(([^)]+)\)", r"<\2|\1>", text)

    # Line-level first so leading bullet '*' isn't seen as emphasis.
    out_lines: list[str] = []
    for line in text.split("\n"):
        if re.match(r"^\s*([-*_])\1{2,}\s*$", line):  # horizontal rule
            continue
        line = re.sub(r"^\s*#{1,6}\s+(.*)$", rf"{BOLD}\1{BOLD}", line)  # heading->bold
        line = re.sub(r"^(\s*)[-*+]\s+", r"\1• ", line)  # bullet
        out_lines.append(line)
    text = "\n".join(out_lines)

    # Bold (** or __) -> marker; remaining single * -> _italic_ (Slack italic).
    text = re.sub(r"\*\*([^*]+)\*\*", rf"{BOLD}\1{BOLD}", text)
    text = re.sub(r"__([^_]+)__", rf"{BOLD}\1{BOLD}", text)
    text = re.sub(r"\*([^*]+)\*", r"_\1_", text)
    text = text.replace(BOLD, "*")

    text = re.sub(r"\n{3,}", "\n\n", text)
    for key, original in stash.items():
        text = text.replace(key, original)
    return text.strip()


def split_for_limit(text: str, limit: int = 2000) -> list[str]:
    """Split text into chunks no longer than `limit`, preferring line boundaries."""
    if len(text) <= limit:
        return [text]

    chunks: list[str] = []
    current = ""
    for line in text.split("\n"):
        while len(line) > limit:  # a single over-long line: hard-split
            if current:
                chunks.append(current)
                current = ""
            chunks.append(line[:limit])
            line = line[limit:]
        if current and len(current) + 1 + len(line) > limit:
            chunks.append(current)
            current = line
        else:
            current = f"{current}\n{line}" if current else line
    if current:
        chunks.append(current)
    return chunks
