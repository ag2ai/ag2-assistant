"""Draw an A2UI surface as a Telegram HTML widget: the card laid out as HTML in the chat's
theme, its Buttons asking the bot with widget queries."""

import html
import re
import zlib
from dataclasses import dataclass
from datetime import UTC, datetime
from importlib.resources import files
from typing import Any

from assistant.channels.rich_card import (
    MAX_COLUMNS,
    MAX_DEPTH,
    OMITTED_NOTE,
    Bindings,
    CardAction,
    is_number,
    surface_components,
)
from assistant.events import A2UISurface

# Telegram's caps on one widget's HTML, raw and zlib-packed.
MAX_HTML_BYTES = 32_768
MAX_PACKED_BYTES = 4_096

# Components that show a picture; a card holding one is not drawn as a widget.
PICTURES = frozenset({"Figure", "Image", "Video"})

_GAP = {"none": 0, "xs": 2, "sm": 6, "md": 10, "lg": 14}
_FLEX = {
    "start": "flex-start",
    "end": "flex-end",
    "center": "center",
    "stretch": "stretch",
    "between": "space-between",
}
_TONES = {"neutral", "muted", "accent", "positive", "negative"}
_HEADINGS = {"h1", "h2", "h3", "h4"}
_GRID_MAX = 3


def _asset(name: str) -> str:
    """A bundled widget file without its comments or indentation."""
    text = files("assistant.channels").joinpath("widget", name).read_text(encoding="utf-8")
    text = re.sub(r"/\*.*?\*/", "", text, flags=re.S)
    return "\n".join(line.strip() for line in text.splitlines() if line.strip())


_RUNTIME = _asset("runtime.js")
_STYLE = _asset("widget.css")

# Each weather condition as artwork in a 64-unit box: a sun, a cloud and what falls.
_CLOUD = '<path d="M19 47h27a10 10 0 0 0 0-20 13 13 0 0 0-25 4 8 8 0 0 0-2 16z" fill="#fff"/>'
_SUN = (
    '<circle cx="24" cy="24" r="9" fill="#ffd54f"/><circle cx="24" cy="24" r="15" '
    'fill="none" stroke="#ffd54f" stroke-width="3" stroke-dasharray="3 4.85"/>'
)
_LINES = 'stroke-width="3" stroke-linecap="round"'
_ART = {
    "sunny": f'<g transform="translate(8 8)">{_SUN}</g>',
    "partly-cloudy": _SUN + _CLOUD,
    "cloudy": '<path d="M14 36h20a8 8 0 0 0 0-16 10 10 0 0 0-19 3 6 6 0 0 0-1 13z" '
    f'fill="#fff9"/>{_CLOUD}',
    "foggy": f'{_CLOUD}<path d="M14 53h36M20 59h30" stroke="#fff" {_LINES}/>',
    "rainy": f'{_CLOUD}<path d="M24 52l-3 7M33 52l-3 7M42 52l-3 7" stroke="#bfe3ff" {_LINES}/>',
    "thunderstorm": f'{_CLOUD}<path d="M34 46l-7 10h6l-3 8 10-12h-6l4-6z" fill="#ffd54f"/>',
    "snow": f'{_CLOUD}<g fill="#fff"><circle cx="23" cy="55" r="2.5"/>'
    '<circle cx="33" cy="59" r="2.5"/><circle cx="43" cy="55" r="2.5"/></g>',
    "windy": '<path d="M8 24h30a6 6 0 1 0-6-6M8 34h42a6 6 0 1 1-6 6M8 44h24" fill="none" '
    'stroke="#fff" stroke-width="4" stroke-linecap="round"/>',
}


@dataclass(frozen=True)
class WidgetCard:
    """A surface as widget HTML, with the action behind each button's query index and
    the components it left out."""

    html: str
    actions: tuple[CardAction, ...]
    omitted: tuple[str, ...] = ()

    @property
    def fits(self) -> bool:
        """Whether Telegram takes this widget as one message."""
        raw = self.html.encode()
        return len(raw) <= MAX_HTML_BYTES and len(zlib.compress(raw, 9)) <= MAX_PACKED_BYTES


def render_widget(surface: A2UISurface, query_prefix: str) -> WidgetCard | None:
    """Draw `surface`; Button `i` asks `query_prefix + str(i)`. None for a card with pictures
    or one that draws nothing."""
    components = surface_components(surface)
    if any(isinstance(c, dict) and c.get("component") in PICTURES for c in components):
        return None
    painter = _Painter(surface, query_prefix)
    body = painter.draw(surface.component.get("id") or "root", None, 0)
    if not body:
        return None
    if painter.omitted:
        body += f'<div class="text caption muted">{html.escape(OMITTED_NOTE)}</div>'
    page = f'<meta charset="utf-8"><style>{_STYLE}</style><div id="page">{body}</div>'
    if painter.actions or painter.links:
        page += f"<script>{_RUNTIME}</script>"
    return WidgetCard(page, tuple(painter.actions), tuple(painter.omitted))


def widget_actions(surface: A2UISurface) -> tuple[CardAction, ...]:
    """The action behind each of `surface`'s widget buttons, in query index order."""
    drawn = render_widget(surface, "")
    return drawn.actions if drawn is not None else ()


def _ago(stamp: datetime, now: datetime) -> str:
    minutes = round((now - stamp).total_seconds() / 60)
    if minutes < 1:
        return "now"
    if minutes < 60:
        return f"{minutes}m ago"
    if minutes < 1440:
        return f"{round(minutes / 60)}h ago"
    return f"{round(minutes / 1440)}d ago"


def _formatted(raw: str, format_: Any) -> str:
    """A timestamp written as `format_` (time, datetime or ago); other text as it is."""
    try:
        stamp = datetime.fromisoformat(raw)
    except ValueError:
        return raw
    if format_ == "time":
        return stamp.strftime("%H:%M")
    if format_ == "datetime":
        return stamp.strftime("%d.%m %H:%M")
    if format_ == "ago":
        aware = stamp if stamp.tzinfo else stamp.replace(tzinfo=UTC)
        return _ago(aware, datetime.now(UTC))
    return raw


class _Painter(Bindings):
    def __init__(self, surface: A2UISurface, query_prefix: str) -> None:
        super().__init__(surface, query_prefix)
        self.links = 0

    # ---- tree ----

    def draw(self, cid: Any, item: Any, depth: int) -> str:
        component = self.by_id.get(cid) if isinstance(cid, str) else None
        if component is None or depth > MAX_DEPTH or not self.present(component, item):
            return ""
        kind = component.get("component")
        method = getattr(self, f"_paint_{str(kind).lower()}", None)
        if method is None:
            if kind not in self.omitted and kind != "Icon":
                self.omitted.append(str(kind))
            return ""
        return method(component, item, depth + 1)

    def drawn_children(self, component: dict, item: Any, depth: int) -> list[str]:
        drawn = (self.draw(cid, each, depth) for cid, each in self.child_items(component, item))
        return [part for part in drawn if part]

    def tone(self, value: Any, item: Any, mapping: Any = None) -> str:
        """The tone class a `tone` names: a word, a bound number's sign or a mapped word."""
        resolved = self.resolve(value, item)
        if is_number(resolved):
            return " positive" if resolved > 0 else " negative" if resolved < 0 else ""
        if isinstance(mapping, dict) and isinstance(resolved, str):
            resolved = mapping.get(resolved, resolved)
        return f" {resolved}" if resolved in _TONES else ""

    def box(self, component: dict, item: Any, cls: str, inner: str) -> str:
        style = ""
        if component.get("gap") in _GAP:
            style += f"gap:{_GAP[component['gap']]}px;"
        if component.get("align") in _FLEX:
            style += f"align-items:{_FLEX[component['align']]};"
        if component.get("justify") in _FLEX:
            style += f"justify-content:{_FLEX[component['justify']]};"
        if component.get("grow") is True:
            style += "flex:1;"
        marker = component.get("marker")
        if marker and (not isinstance(marker, dict) or self.present({"when": marker}, item)):
            cls += " marked"
        cls += self.tone(component.get("tone"), item)
        styled = f' style="{style}"' if style else ""
        return f'<div class="{cls}"{styled}>{inner}</div>'

    # ---- layout ----

    def _paint_card(self, component: dict, item: Any, depth: int) -> str:
        cls = "col card" + (" feature" if component.get("variant") == "feature" else "")
        return self.box(component, item, cls, "".join(self.drawn_children(component, item, depth)))

    def _paint_column(self, component: dict, item: Any, depth: int) -> str:
        return self.box(
            component, item, "col", "".join(self.drawn_children(component, item, depth))
        )

    def _paint_row(self, component: dict, item: Any, depth: int) -> str:
        return self.box(
            component, item, "row", "".join(self.drawn_children(component, item, depth))
        )

    def _paint_list(self, component: dict, item: Any, depth: int) -> str:
        parts = self.drawn_children(component, item, depth)
        if component.get("variant") == "ranked":
            children = component.get("children")
            start = children.get("start", 0) if isinstance(children, dict) else 0
            start = start if isinstance(start, int) else 0
            parts = [
                f'<div class="row ranked"><b>{start + n + 1:02d}</b>{part}</div>'
                for n, part in enumerate(parts)
            ]
        return self.box(component, item, "col", "".join(parts))

    def _paint_grid(self, component: dict, item: Any, depth: int) -> str:
        columns = component.get("columns")
        count = min(max(columns, 1), _GRID_MAX) if isinstance(columns, int) else 2
        inner = "".join(self.drawn_children(component, item, depth))
        return f'<div class="grid" style="grid-template-columns:repeat({count},1fr)">{inner}</div>'

    def _paint_divider(self, component: dict, item: Any, depth: int) -> str:
        return '<hr class="strong">' if component.get("emphasis") == "strong" else "<hr>"

    def _paint_table(self, component: dict, item: Any, depth: int) -> str:
        columns = self.resolve(component.get("columns"), item)
        rows = self.resolve(component.get("rows"), item)
        if not isinstance(columns, list) or not isinstance(rows, list):
            return ""
        columns = columns[:MAX_COLUMNS]
        lead = component.get("lead")
        head = "<th></th>" if lead else ""
        head += "".join(f"<th>{self.draw(component.get('header'), c, depth)}</th>" for c in columns)
        body = ""
        for row in rows:
            cells = self.resolve(component.get("cells"), row)
            cells = cells if isinstance(cells, list) else []
            win = self.resolve(component.get("win"), row)
            line = f"<td>{self.draw(lead, row, depth)}</td>" if lead else ""
            for index, column in enumerate(columns):
                if index >= len(cells):
                    line += "<td>—</td>"
                    continue
                won = win is not None and win == self.resolve(component.get("key"), column)
                cell = self.draw(component.get("cell"), cells[index], depth)
                line += f'<td class="win">{cell}</td>' if won else f"<td>{cell}</td>"
            body += f"<tr>{line}</tr>"
        return f'<div class="table-scroll"><table><tr>{head}</tr>{body}</table></div>'

    # ---- content ----

    def _paint_text(self, component: dict, item: Any, depth: int) -> str:
        raw = self.raw(component.get("text"), item, component.get("map"))
        if not raw:
            return ""
        if component.get("format"):
            raw = _formatted(raw, component.get("format"))
        variant = component.get("variant") or "body"
        inner = html.escape(raw)
        if component.get("emphasis") == "strong":
            inner = f"<b>{inner}</b>"
        cls = "text " + ("heading " if variant in _HEADINGS else "") + str(variant)
        cls += self.tone(component.get("tone"), item, component.get("map"))
        return f'<div class="{html.escape(cls)}">{inner}</div>'

    def _paint_metric(self, component: dict, item: Any, depth: int) -> str:
        value = self.raw(component.get("value"), item)
        if not value:
            return ""
        label = self.raw(component.get("label"), item)
        unit = self.raw(component.get("unit"), item)
        move = ""
        if found := self.delta(component, item):
            delta, suffix = found
            arrow, cls = ("▲", "positive") if delta > 0 else ("▼", "negative")
            move = f'<span class="{cls}">{arrow} {delta:+g}{suffix}</span>'
        size = component.get("size") if component.get("size") in ("sm", "lg") else "md"
        cls = f"metric {size}" + self.tone(component.get("tone"), item)
        caption = f'<div class="text caption muted">{html.escape(label)}</div>' if label else ""
        tail = f" <small>{html.escape(unit)}</small>" if unit else ""
        return f'<div class="{cls}">{caption}<div class="metric-value">{html.escape(value)}{tail}</div>{move}</div>'

    def _paint_link(self, component: dict, item: Any, depth: int) -> str:
        inner = "".join(self.drawn_children(component, item, depth))
        url = self.resolve(component.get("url"), item)
        if not isinstance(url, str) or not url.startswith(("http://", "https://")) or not inner:
            return inner
        self.links += 1
        return f'<a data-url="{html.escape(url)}" onclick="openLink(this)">{inner}</a>'

    def _paint_weatherglyph(self, component: dict, item: Any, depth: int) -> str:
        condition = self.resolve(component.get("condition"), item)
        condition = condition if isinstance(condition, str) and condition in _ART else "cloudy"
        temperature = html.escape(self.raw(component.get("temperature"), item))
        art = f'<svg viewBox="0 0 64 64">{_ART[condition]}</svg>'
        if component.get("size") != "lg":
            reading = f"<b>{temperature}</b>" if temperature else ""
            return f'<span class="weather"><span class="{condition}">{art}</span>{reading}</span>'
        return f'<div class="sky {condition}"><div class="degrees">{temperature}</div>{art}</div>'

    def _paint_checkbox(self, component: dict, item: Any, depth: int) -> str:
        box = "☑" if self.resolve(component.get("value"), item) is True else "☐"
        label = html.escape(self.raw(component.get("label"), item))
        return f'<div class="text">{box} {label}</div>'

    def _paint_sparkline(self, component: dict, item: Any, depth: int) -> str:
        values = self.resolve(component.get("values"), item)
        if not isinstance(values, list) or len(values) < 2:
            return ""
        numbers = [v if isinstance(v, int | float) else 0 for v in values]
        step = 100 / (len(numbers) - 1)
        points = " ".join(f"{i * step:.1f},{100 - v:.1f}" for i, v in enumerate(numbers))
        cls = "spark" + self.tone(component.get("tone"), item)
        return (
            f'<svg class="{cls}" viewBox="0 0 100 100" preserveAspectRatio="none">'
            f'<polyline points="{points}"/></svg>'
        )

    def _paint_button(self, component: dict, item: Any, depth: int) -> str:
        label = self.draw(component.get("child"), item, depth)
        plain = re.sub(r"<[^>]+>", "", label)
        data = self.button(component, item, html.unescape(plain))
        if data is None:
            return ""
        query = html.escape(data)
        return f'<button data-query="{query}" onclick="ask(this)">{label}</button>'
