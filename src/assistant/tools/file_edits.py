"""Block replacements with normalized matching and unchanged-line preservation."""

import difflib
import re
import unicodedata
from dataclasses import dataclass

from pydantic import BaseModel, Field


class FileEdit(BaseModel):
    """A unique text block and its replacement."""

    old_text: str = Field(alias="oldText")
    new_text: str = Field(alias="newText")


_TYPOGRAPHY = str.maketrans(
    {
        **dict.fromkeys("\u2018\u2019\u201a\u201b", "'"),
        **dict.fromkeys("\u201c\u201d\u201e\u201f", '"'),
        **dict.fromkeys("\u2010\u2011\u2012\u2013\u2014\u2015\u2212", "-"),
        **dict.fromkeys(
            "\u00a0\u2002\u2003\u2004\u2005\u2006\u2007\u2008\u2009\u200a\u202f\u205f\u3000", " "
        ),
    }
)


def normalize_newlines(text: str) -> str:
    """Convert CRLF and CR line endings to LF."""
    return text.replace("\r\n", "\n").replace("\r", "\n")


def _fuzzy(text: str) -> str:
    text = unicodedata.normalize("NFKC", text)
    return "\n".join(line.rstrip() for line in text.split("\n")).translate(_TYPOGRAPHY)


@dataclass
class _Replacement:
    index: int
    start: int
    end: int
    text: str


def _replace(text: str, replacements: list[_Replacement], offset: int = 0) -> str:
    for replacement in reversed(replacements):
        text = (
            text[: replacement.start - offset] + replacement.text + text[replacement.end - offset :]
        )
    return text


def _preserve_lines(original: str, base: str, replacements: list[_Replacement]) -> str:
    """Rewrite touched lines from the matching view and retain all other original lines."""
    original_lines = re.findall(r"[^\n]*\n|[^\n]+", original)
    spans = [(match.start(), match.end()) for match in re.finditer(r"[^\n]*\n|[^\n]+", base)]
    groups: list[tuple[int, int, list[_Replacement]]] = []
    for replacement in replacements:
        start = next(i for i, (_, end) in enumerate(spans) if end > replacement.start)
        end = next(i + 1 for i, (_, end) in enumerate(spans) if end >= replacement.end)
        if groups and start < groups[-1][1]:
            first, last, members = groups[-1]
            groups[-1] = (first, max(last, end), [*members, replacement])
        else:
            groups.append((start, end, [replacement]))
    result: list[str] = []
    cursor = 0
    for start, end, members in groups:
        result.extend(original_lines[cursor:start])
        left, right = spans[start][0], spans[end - 1][1]
        result.append(_replace(base[left:right], members, left))
        cursor = end
    result.extend(original_lines[cursor:])
    return "".join(result)


def apply_edits(original: str, edits: list[FileEdit]) -> str:
    """Validate all replacements against the original, then apply disjoint matches."""
    if not edits:
        raise ValueError("edits must contain at least one replacement.")
    pairs = [(normalize_newlines(e.old_text), normalize_newlines(e.new_text)) for e in edits]
    for index, (old, _) in enumerate(pairs):
        if not old or not _fuzzy(old):
            raise ValueError(f"edits[{index}].oldText must not be empty after normalization.")
    fuzzy_original = _fuzzy(original)
    use_fuzzy = any(old not in original and _fuzzy(old) in fuzzy_original for old, _ in pairs)
    base = fuzzy_original if use_fuzzy else original
    replacements: list[_Replacement] = []
    for index, (old, new) in enumerate(pairs):
        needle = old if old in base else _fuzzy(old)
        start = base.find(needle)
        if start < 0:
            raise ValueError(f"Could not find edits[{index}].oldText. Read the file and retry.")
        occurrences = fuzzy_original.count(_fuzzy(old))
        if occurrences > 1:
            raise ValueError(
                f"Found {occurrences} occurrences of edits[{index}].oldText; "
                "the text must be unique. Include more surrounding context."
            )
        replacements.append(_Replacement(index, start, start + len(needle), new))
    replacements.sort(key=lambda replacement: replacement.start)
    for previous, current in zip(replacements, replacements[1:]):
        if previous.end > current.start:
            raise ValueError(f"edits[{previous.index}] and edits[{current.index}] overlap.")
    result = (
        _preserve_lines(original, base, replacements)
        if use_fuzzy
        else _replace(original, replacements)
    )
    if result == original:
        raise ValueError("No changes made: the replacements produced identical content.")
    return result


def edit_details(path: str, old: str, new: str) -> dict:
    """Produce a numbered diff, unified patch and first changed line."""
    old_lines = re.findall(r"[^\n]*\n|[^\n]+", old)
    new_lines = re.findall(r"[^\n]*\n|[^\n]+", new)
    matcher = difflib.SequenceMatcher(a=old_lines, b=new_lines, autojunk=False)
    first = next(j + 1 for tag, _, _, j, _ in matcher.get_opcodes() if tag != "equal")
    diff: list[str] = []
    for group in matcher.get_grouped_opcodes(4):
        if diff:
            diff.append(" ...")
        for tag, left, right, start, end in group:
            if tag == "equal":
                diff.extend(f" {i + 1} {new_lines[i].rstrip(chr(10))}" for i in range(start, end))
            else:
                diff.extend(f"-{i + 1} {old_lines[i].rstrip(chr(10))}" for i in range(left, right))
                diff.extend(f"+{i + 1} {new_lines[i].rstrip(chr(10))}" for i in range(start, end))
    patch_parts = difflib.unified_diff(old_lines, new_lines, fromfile=path, tofile=path, n=4)
    patch = "".join(
        part if part.endswith("\n") else part + "\n\\ No newline at end of file\n"
        for part in patch_parts
    )
    return {"diff": "\n".join(diff), "patch": patch, "firstChangedLine": first}
