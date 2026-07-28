"""Outbound formatting helpers — render an agent's Markdown reply per channel.

The agent emits Markdown (ideal for a web UI). Chat channels like Telegram show
raw Markdown literally, so we convert it. `markdown_to_plain` produces clean plain
text — no parse modes, so it can never trigger a platform parse error.
"""

import re
from collections.abc import Callable


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


_FENCED_BLOCK = re.compile(r"```.*?(?:\n```|\Z)", re.S)
_PARAGRAPH_GAP = re.compile(r"(\n[ \t]*\n\s*)")
_LINE_GAP = re.compile(r"(\n)")
_SENTENCE_GAP = re.compile(r"((?<=[.!?…])[ \t]+)")

# (body, separator that followed it, how to split the body if it alone is too long)
_Refine = Callable[[str, int], list[str]]
_Unit = tuple[str, str, _Refine]


def split_for_limit(text: str, limit: int = 2000) -> list[str]:
    """Split text into chunks no longer than `limit`.

    Breaks prefer paragraph boundaries, then line, then sentence boundaries;
    whatever is still too long is hard-split. A fenced code block is kept whole
    unless it alone exceeds the limit.
    """
    if len(text) <= limit:
        return [text]
    return _pack(_fenced_units(text), limit)


def _pack(units: list[_Unit], limit: int) -> list[str]:
    """Greedily fill chunks with units, refining any unit that is itself too long."""
    chunks: list[str] = []
    current = ""
    separator = ""
    for body, sep, refine in units:
        candidate = f"{current}{separator}{body}" if current else body
        if len(candidate) <= limit:
            current = candidate
        else:
            if current:
                chunks.append(current)
            if len(body) <= limit:
                current = body
            else:
                pieces = refine(body, limit)
                chunks.extend(pieces[:-1])
                current = pieces[-1]
        separator = sep
    if current:
        chunks.append(current)
    return chunks


def _fenced_units(text: str) -> list[_Unit]:
    """Units for the whole text: fenced code blocks are atoms, prose is paragraph-split."""
    segments: list[tuple[str, _Refine]] = []
    pos = 0
    for match in _FENCED_BLOCK.finditer(text):
        segments.append((text[pos : match.start()], _by_paragraph))
        segments.append((match.group(), _split_fenced))
        pos = match.end()
    segments.append((text[pos:], _by_paragraph))

    units: list[_Unit] = []
    carry = ""  # whitespace between the previous unit and the next one
    for raw, refine in segments:
        body = raw.strip()
        if not body:
            carry += raw
            continue
        if units:
            lead = raw[: len(raw) - len(raw.lstrip())]
            prev_body, prev_sep, prev_refine = units[-1]
            units[-1] = (prev_body, prev_sep + carry + lead, prev_refine)
        carry = raw[len(raw.rstrip()) :]
        units.append((body, "", refine))
    return units


def _split_on(text: str, gap: re.Pattern[str], refine: _Refine) -> list[_Unit]:
    parts = gap.split(text)
    return [
        (parts[i], parts[i + 1] if i + 1 < len(parts) else "", refine)
        for i in range(0, len(parts), 2)
    ]


def _by_paragraph(text: str, limit: int) -> list[str]:
    return _pack(_split_on(text, _PARAGRAPH_GAP, _by_line), limit)


def _by_line(text: str, limit: int) -> list[str]:
    return _pack(_split_on(text, _LINE_GAP, _by_sentence), limit)


def _by_sentence(text: str, limit: int) -> list[str]:
    return _pack(_split_on(text, _SENTENCE_GAP, _hard_split), limit)


def _hard_split(text: str, limit: int) -> list[str]:
    return [text[i : i + limit] for i in range(0, len(text), limit)]


def _split_fenced(text: str, limit: int) -> list[str]:
    """Hard-split a code block that alone exceeds the limit, re-opening and closing the
    fence on every part so each chunk still renders as code."""
    opener, _, rest = text.partition("\n")
    inner = rest[: -len("```")] if rest.rstrip().endswith("```") else rest
    inner = inner.strip("\n")
    budget = limit - len(opener) - len("\n\n```")
    if budget <= 0 or not inner:
        return _hard_split(text, limit)
    return [f"{opener}\n{inner[i : i + budget]}\n```" for i in range(0, len(inner), budget)]
