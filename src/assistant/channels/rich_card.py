"""Draw an A2UI surface as Telegram rich-message HTML, its Buttons as callback buttons;
containers stack, and what Telegram cannot draw is left out and noted under it."""

import html
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from ag2.a2ui.incoming import A2UIIncomingAction

from assistant.events import A2UISurface

# What a card says under itself when part of it could not be drawn here.
OMITTED_NOTE = "Some of this card only shows in the web app."

# Telegram's caps on one rich message.
MAX_RICH_CHARS = 32_768
MAX_RICH_BLOCKS = 500

# Telegram draws at most this many table columns.
_MAX_COLUMNS = 20
_MAX_DEPTH = 32
_GLYPHS = {
    "sunny": "☀️",
    "partly-cloudy": "⛅",
    "cloudy": "☁️",
    "foggy": "🌫️",
    "rainy": "🌧️",
    "thunderstorm": "⛈️",
    "snow": "❄️",
    "windy": "💨",
}
_HEADINGS = {"h1": 2, "h2": 3, "h3": 4, "h4": 5}


@dataclass(frozen=True)
class CardAction:
    """One Button: its label and agent event, the context resolved against the data model."""

    label: str
    name: str
    surface_id: str
    component_id: str
    context: dict

    def incoming(self, timestamp: str) -> A2UIIncomingAction:
        """This click as the action a renderer would post for it."""
        return A2UIIncomingAction(
            name=self.name,
            surface_id=self.surface_id,
            source_component_id=self.component_id,
            timestamp=timestamp,
            context=self.context,
        )


@dataclass(frozen=True)
class RichCard:
    """A surface as rich HTML and as plain text, with the action behind each callback index."""

    html: str
    text: str
    actions: tuple[CardAction, ...]
    omitted: tuple[str, ...]
    blocks: int

    @property
    def fits(self) -> bool:
        """Whether Telegram takes this card as one rich message."""
        return len(self.text) <= MAX_RICH_CHARS and self.blocks <= MAX_RICH_BLOCKS


class _Kind(StrEnum):
    INLINE = "inline"
    ITEM = "item"
    BLOCK = "block"
    BUTTON = "button"


@dataclass(frozen=True)
class _Block:
    """One drawn piece: `flat` is its HTML inside a line, `plain` its unformatted text;
    `role` marks a heading or an eyebrow, which a heading right after it makes redundant."""

    kind: _Kind
    html: str
    flat: str = ""
    plain: str = ""
    role: str = ""


# How a run of same-kind blocks is wrapped, as HTML and as plain text.
_WRAP_HTML = {
    _Kind.INLINE: lambda run: "".join(f"<p>{part}</p>" for part in run),
    _Kind.ITEM: lambda run: "<ul>" + "".join(f"<li>{part}</li>" for part in run) + "</ul>",
    _Kind.BUTTON: lambda run: "<tg-button-row>" + "".join(run) + "</tg-button-row>",
    _Kind.BLOCK: lambda run: "".join(run),
}
_WRAP_PLAIN = {
    _Kind.INLINE: lambda run: "\n\n".join(run),
    _Kind.ITEM: lambda run: "\n".join(f"• {part}" for part in run),
    _Kind.BUTTON: lambda run: "",
    _Kind.BLOCK: lambda run: "\n\n".join(run),
}


def render_card(surface: A2UISurface, callback_prefix: str) -> RichCard:
    """Draw `surface`; Button `i` sends callback data `callback_prefix + str(i)`."""
    nested = surface.component.get("_components")
    components = nested if isinstance(nested, list) else [surface.component]
    drawer = _Drawer(components, surface.data or {}, surface.surface_id, callback_prefix)
    root = surface.component.get("id") or "root"
    blocks = drawer.draw(root, None, 0)
    if drawer.omitted:
        note = html.escape(OMITTED_NOTE)
        blocks.append(_Block(_Kind.BLOCK, f"<p><i>{note}</i></p>", plain=OMITTED_NOTE))
    body, text, count = _assemble(blocks)
    return RichCard(body, text, tuple(drawer.actions), tuple(drawer.omitted), count)


def _assemble(blocks: list[_Block]) -> tuple[str, str, int]:
    """Wrap each run of same-kind blocks; the HTML, the plain text and the run count."""
    html_parts: list[str] = []
    plain_parts: list[str] = []
    index = 0
    while index < len(blocks):
        kind = blocks[index].kind
        run: list[_Block] = []
        while index < len(blocks) and blocks[index].kind == kind:
            run.append(blocks[index])
            index += 1
        html_parts.append(_WRAP_HTML[kind]([b.html for b in run]))
        plain = _WRAP_PLAIN[kind]([b.plain for b in run if b.plain])
        if plain:
            plain_parts.append(plain)
    return "".join(html_parts), "\n\n".join(plain_parts), len(html_parts)


def _there(value: Any) -> bool:
    """A `when` value counts as present unless missing, false or empty."""
    return value is not None and value is not False and value != [] and value != ""


def _walk(value: Any, parts: list[str]) -> Any:
    for part in parts:
        if isinstance(value, dict):
            value = value.get(part)
        elif isinstance(value, list) and part.isdigit() and int(part) < len(value):
            value = value[int(part)]
        else:
            return None
    return value


def _url(value: Any) -> str:
    return value if isinstance(value, str) and value.startswith(("http://", "https://")) else ""


def _inline(markup: str, plain: str) -> _Block:
    return _Block(_Kind.INLINE, markup, markup, plain)


class _Drawer:
    def __init__(self, components: list, data: dict, surface_id: str, prefix: str) -> None:
        self.by_id = {c["id"]: c for c in components if isinstance(c, dict) and "id" in c}
        self.data = data
        self.surface_id = surface_id
        self.prefix = prefix
        self.actions: list[CardAction] = []
        self.omitted: list[str] = []

    # ---- values ----

    def resolve(self, value: Any, item: Any) -> Any:
        """A literal as written, a `{path}` binding read from the data or the item."""
        if not isinstance(value, dict):
            return value
        path = value.get("path")
        if not isinstance(path, str):
            return None  # a function call has no value outside the web renderer
        if path == ".":
            return item
        if path.startswith("./"):
            return _walk(item, [p for p in path[2:].split("/") if p])
        if path.startswith("/"):
            return _walk(self.data, [p for p in path.split("/") if p])
        return _walk(item if item is not None else self.data, [p for p in path.split("/") if p])

    def raw(self, value: Any, item: Any, mapping: Any = None) -> str:
        """A bound value as unescaped text, through the component's `map` if it has one."""
        resolved = self.resolve(value, item)
        if isinstance(mapping, dict) and isinstance(resolved, str | int | float | bool):
            resolved = mapping.get(str(resolved), resolved)
        if resolved is None or isinstance(resolved, dict | list):
            return ""
        if isinstance(resolved, float) and resolved.is_integer():
            resolved = int(resolved)
        return str(resolved)

    def present(self, component: dict, item: Any) -> bool:
        when = component.get("when")
        if when is None:
            return True
        conditions = when if isinstance(when, list) else [when]
        return any(_there(self.resolve(c, item)) for c in conditions)

    # ---- tree ----

    def draw(self, cid: Any, item: Any, depth: int) -> list[_Block]:
        component = self.by_id.get(cid) if isinstance(cid, str) else None
        if component is None or depth > _MAX_DEPTH or not self.present(component, item):
            return []
        kind = component.get("component")
        method = getattr(self, f"_draw_{str(kind).lower()}", None)
        if method is None:
            if kind not in self.omitted and kind != "Icon":
                self.omitted.append(str(kind))
            return []
        return method(component, item, depth + 1)

    def child_groups(self, component: dict, item: Any, depth: int) -> list[list[_Block]]:
        """The blocks each child draws, one list per child, in order."""
        groups = [self.draw(component["child"], item, depth)] if "child" in component else []
        children = component.get("children")
        if isinstance(children, list):
            groups.extend(self.draw(cid, item, depth) for cid in children)
        elif isinstance(children, dict):
            items = self.resolve({"path": children.get("path")}, item)
            start = children.get("start") or 0
            if isinstance(items, list) and isinstance(start, int):
                groups.extend(
                    self.draw(children.get("componentId"), each, depth) for each in items[start:]
                )
        return groups

    def children(self, component: dict, item: Any, depth: int) -> list[_Block]:
        """Every child's blocks in order, without an eyebrow a heading right after repeats."""
        blocks = [block for group in self.child_groups(component, item, depth) for block in group]
        return [
            block
            for block, after in zip(blocks, [*blocks[1:], None], strict=False)
            if not (block.role == "eyebrow" and after is not None and after.role == "heading")
        ]

    def flat(self, blocks: list[_Block], sep: str = " · ") -> str:
        return sep.join(b.flat for b in blocks if b.flat)

    def plain(self, blocks: list[_Block], sep: str = " · ") -> str:
        return sep.join(b.plain for b in blocks if b.plain)

    # ---- layout ----

    def _draw_card(self, component: dict, item: Any, depth: int) -> list[_Block]:
        return self.children(component, item, depth)

    _draw_column = _draw_card
    _draw_list = _draw_card
    _draw_grid = _draw_card

    def _draw_row(self, component: dict, item: Any, depth: int) -> list[_Block]:
        """Children that each draw one line of text join on one line; anything else
        (a column of several lines, a list, a heading) keeps its own place."""
        merged: list[_Block] = []
        joinable = False
        for group in self.child_groups(component, item, depth):
            one_line = len(group) == 1 and group[0].kind is _Kind.INLINE
            if one_line and joinable:
                last, block = merged[-1], group[0]
                merged[-1] = _inline(f"{last.html} · {block.html}", f"{last.plain} · {block.plain}")
            else:
                merged.extend(group)
            joinable = one_line
        return merged

    def _draw_divider(self, component: dict, item: Any, depth: int) -> list[_Block]:
        return [_Block(_Kind.BLOCK, "<hr/>", plain="———")]

    def _draw_table(self, component: dict, item: Any, depth: int) -> list[_Block]:
        columns = self.resolve(component.get("columns"), item)
        rows = self.resolve(component.get("rows"), item)
        if not isinstance(columns, list) or not isinstance(rows, list):
            return []
        columns = columns[:_MAX_COLUMNS]
        lead = component.get("lead")
        headers = [self.draw(component.get("header"), col, depth) for col in columns]
        head = ("<th></th>" if lead else "") + "".join(f"<th>{self.flat(h)}</th>" for h in headers)
        lines = [" | ".join(([""] if lead else []) + [self.plain(h) for h in headers])]
        body = []
        for row in rows:
            cells = self.resolve(component.get("cells"), row)
            cells = cells if isinstance(cells, list) else []
            win = self.resolve(component.get("win"), row)
            led = self.draw(lead, row, depth) if lead else []
            line = f"<td>{self.flat(led)}</td>" if lead else ""
            plain = [self.plain(led)] if lead else []
            for index, col in enumerate(columns):
                if index >= len(cells):
                    line += "<td>—</td>"
                    plain.append("—")
                    continue
                drawn = self.draw(component.get("cell"), cells[index], depth)
                value = self.flat(drawn)
                won = win is not None and win == self.resolve(component.get("key"), col)
                line += f"<td><b>{value}</b></td>" if won else f"<td>{value}</td>"
                plain.append(self.plain(drawn))
            body.append(f"<tr>{line}</tr>")
            lines.append(" | ".join(plain))
        table = f"<table><tr>{head}</tr>{''.join(body)}</table>"
        return [_Block(_Kind.BLOCK, table, plain="\n".join(lines))]

    # ---- content ----

    def _draw_text(self, component: dict, item: Any, depth: int) -> list[_Block]:
        raw = self.raw(component.get("text"), item, component.get("map"))
        if not raw:
            return []
        text = html.escape(raw)
        variant = component.get("variant")
        if variant in _HEADINGS:
            level = _HEADINGS[variant]
            heading = f"<h{level}>{text}</h{level}>"
            return [_Block(_Kind.BLOCK, heading, f"<b>{text}</b>", raw, "heading")]
        if variant == "quote":
            return [_Block(_Kind.BLOCK, f"<blockquote>{text}</blockquote>", f"<i>{text}</i>", raw)]
        if variant == "eyebrow":
            return [_Block(_Kind.INLINE, f"<b>{text}</b>", f"<b>{text}</b>", raw, "eyebrow")]
        if variant == "code":
            text = f"<code>{text}</code>"
        elif variant == "caption":
            text = f"<i>{text}</i>"
        if variant in ("pill", "badge") or component.get("emphasis") == "strong":
            text = f"<b>{text}</b>"
        return [_inline(text, raw)]

    def _draw_metric(self, component: dict, item: Any, depth: int) -> list[_Block]:
        value = self.raw(component.get("value"), item)
        if not value:
            return []
        label = self.raw(component.get("label"), item)
        unit = self.raw(component.get("unit"), item)
        delta = self.resolve(component.get("deltaPercent"), item)
        suffix = "%"
        if not isinstance(delta, int | float) or isinstance(delta, bool):
            delta, suffix = self.resolve(component.get("delta"), item), ""
        tail = f" {unit}" if unit else ""
        if isinstance(delta, int | float) and not isinstance(delta, bool) and delta:
            arrow = "▲" if delta > 0 else "▼"
            tail += f" ({arrow} {delta:+g}{suffix})"
        if label:
            line = f"<b>{html.escape(label)}</b> {html.escape(value)}"
            plain = f"{label} {value}{tail}"
        else:
            line, plain = f"<b>{html.escape(value)}</b>", f"{value}{tail}"
        line += html.escape(tail)
        return [_Block(_Kind.ITEM, line, line, plain)]

    def _draw_link(self, component: dict, item: Any, depth: int) -> list[_Block]:
        inner = self.children(component, item, depth)
        url = _url(self.resolve(component.get("url"), item))
        label = self.flat(inner)
        if not url or not label:
            return inner
        return [
            _inline(f'<a href="{html.escape(url)}">{label}</a>', f"{self.plain(inner)} ({url})")
        ]

    def _draw_figure(self, component: dict, item: Any, depth: int) -> list[_Block]:
        url = _url(self.resolve(component.get("url"), item))
        if not url:
            return []
        caption = self.raw(component.get("caption"), item)
        image = _Block(_Kind.BLOCK, f'<img src="{html.escape(url)}"/>', plain=url)
        if not caption:
            return [image]
        escaped = html.escape(caption)
        return [image, _inline(f"<i>{escaped}</i>", caption)]

    _draw_image = _draw_figure

    def _draw_video(self, component: dict, item: Any, depth: int) -> list[_Block]:
        url = _url(self.resolve(component.get("url"), item))
        if not url:
            return []
        return [_Block(_Kind.BLOCK, f'<video src="{html.escape(url)}"></video>', plain=url)]

    def _draw_weatherglyph(self, component: dict, item: Any, depth: int) -> list[_Block]:
        condition = self.resolve(component.get("condition"), item)
        glyph = _GLYPHS.get(condition if isinstance(condition, str) else "", _GLYPHS["cloudy"])
        temperature = self.raw(component.get("temperature"), item)
        line = f"{glyph} <b>{html.escape(temperature)}</b>" if temperature else glyph
        plain = f"{glyph} {temperature}" if temperature else glyph
        if component.get("size") == "lg":  # the card's hero reading
            return [_Block(_Kind.BLOCK, f"<h3>{line}</h3>", line, plain, "heading")]
        return [_inline(line, plain)]

    def _draw_checkbox(self, component: dict, item: Any, depth: int) -> list[_Block]:
        label = self.raw(component.get("label"), item)
        box = "☑" if self.resolve(component.get("value"), item) is True else "☐"
        line = f"{box} {html.escape(label)}"
        return [_Block(_Kind.ITEM, line, line, f"{box} {label}")]

    def _draw_button(self, component: dict, item: Any, depth: int) -> list[_Block]:
        action = component.get("action")
        event = action.get("event") if isinstance(action, dict) else None
        label = self.plain(self.children({"child": component.get("child")}, item, depth))
        if not isinstance(event, dict) or not event.get("name") or not label:
            if "Button" not in self.omitted:
                self.omitted.append("Button")
            return []
        context = event.get("context")
        resolved = (
            {key: self.resolve(value, item) for key, value in context.items()}
            if isinstance(context, dict)
            else {}
        )
        index = len(self.actions)
        self.actions.append(
            CardAction(
                label, str(event["name"]), self.surface_id, str(component.get("id")), resolved
            )
        )
        data = html.escape(f"{self.prefix}{index}")
        markup = f'<tg-button type="callback_data" data="{data}">{html.escape(label)}</tg-button>'
        return [_Block(_Kind.BUTTON, markup)]
