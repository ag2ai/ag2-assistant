"""Drawing an A2UI surface as a Telegram HTML widget (no network)."""

import zlib

import pytest

from assistant.a2ui import CARD_PRIMITIVES, SUPPORTED_BASIC_COMPONENTS
from assistant.cards import bundled_cards_dir, load_cards
from assistant.channels.rich_card import OMITTED_NOTE
from assistant.channels.widget_card import MAX_PACKED_BYTES, PICTURES, render_widget
from assistant.events import A2UISurface
from tests.support.cards import bundled_surface, flat_surface

_CARDS = load_cards(bundled_cards_dir(), SUPPORTED_BASIC_COMPONENTS | CARD_PRIMITIVES)


def _pictured(name: str) -> bool:
    return any(c.get("component") in PICTURES for c in _CARDS[name].layout)


# What a widget leaves out for the web app to draw, as the rich card does.
WEB_ONLY = {
    "CalendarHeatmap",
    "Chart",
    "ChoicePicker",
    "DateTimeInput",
    "Diff",
    "Slider",
    "TextField",
}
_LABELLED_BUTTON = (
    {"id": "it", "component": "Button", "child": "label", "action": {"event": {"name": "go"}}},
    {"id": "label", "component": "Text", "text": "Go"},
)
_SAMPLES = {
    "Button": _LABELLED_BUTTON,
    "Text": ({"id": "it", "component": "Text", "text": "Hi"},),
}


@pytest.mark.parametrize("name", sorted((SUPPORTED_BASIC_COMPONENTS | CARD_PRIMITIVES) - PICTURES))
def test_every_catalog_component_is_drawn_or_named_web_only(name):
    """Every web catalog component is drawn in a widget or named in `WEB_ONLY`."""
    sample = _SAMPLES.get(name, ({"id": "it", "component": name},))
    root = {"id": "root", "component": "Column", "children": ["it", "note"]}
    beside = {"id": "note", "component": "Text", "text": "Beside it"}

    widget = render_widget(flat_surface(root, *sample, beside), "p:")

    assert widget is not None
    assert (name in widget.omitted) == (name in WEB_ONLY)


@pytest.mark.parametrize("name", sorted(n for n in _CARDS if not _pictured(n)))
def test_every_bundled_card_without_pictures_fits_one_widget(name):
    widget = render_widget(bundled_surface(name), "a2u:0123456789ab:")

    assert widget is not None and widget.fits
    assert set(widget.omitted) <= WEB_ONLY


def test_a_card_with_pictures_is_left_to_the_rich_card():
    surface = flat_surface({"id": "root", "component": "Figure", "url": "https://x.test/a.png"})

    assert render_widget(surface, "p:") is None


def test_a_card_that_draws_nothing_is_no_widget():
    surface = flat_surface({"id": "root", "component": "Text", "text": {"path": "/missing"}})

    assert render_widget(surface, "p:") is None


def test_the_weather_card_draws_its_sky_and_its_hourly_strip():
    widget = render_widget(bundled_surface("WeatherPanel"), "p:")

    assert widget is not None
    assert '<div class="sky sunny"><div class="degrees">24°</div><svg' in widget.html
    assert widget.html.count('<div class="text caption muted">') >= 6  # each hour's time
    assert '<div class="text body">⛅</div>' in widget.html
    assert '<div class="text heading h1">Vienna, Austria</div>' in widget.html


def test_an_unknown_condition_is_drawn_as_cloudy():
    surface = flat_surface(
        {"id": "root", "component": "WeatherGlyph", "condition": "hail", "size": "lg"}
    )

    widget = render_widget(surface, "p:")

    assert widget is not None and 'class="sky cloudy"' in widget.html


def test_buttons_ask_their_index_and_carry_their_resolved_action():
    surface = flat_surface(
        {"id": "root", "component": "List", "children": {"componentId": "pick", "path": "/slots"}},
        {
            "id": "pick",
            "component": "Button",
            "child": "pick_label",
            "action": {"event": {"name": "book", "context": {"time": {"path": "./at"}}}},
        },
        {"id": "pick_label", "component": "Text", "text": {"path": "./at"}},
        data={"slots": [{"at": "19:00"}, {"at": "20:30"}]},
    )

    widget = render_widget(surface, "a2u:k:")

    assert widget is not None
    assert 'data-query="a2u:k:1" onclick="ask(this)"' in widget.html
    assert [(a.label, a.name, a.context) for a in widget.actions] == [
        ("19:00", "book", {"time": "19:00"}),
        ("20:30", "book", {"time": "20:30"}),
    ]
    assert "widget.request(" in widget.html


def test_a_card_with_no_buttons_or_links_carries_no_script():
    widget = render_widget(bundled_surface("WeatherPanel"), "p:")

    assert widget is not None and "<script>" not in widget.html


def test_a_link_opens_its_page_through_telegram_and_any_other_target_is_text():
    surface = flat_surface(
        {"id": "root", "component": "Column", "children": ["web", "task"]},
        {"id": "web", "component": "Link", "url": "https://ag2.ai/", "child": "a"},
        {"id": "a", "component": "Text", "text": "Site"},
        {"id": "task", "component": "Link", "task": "t1", "child": "b"},
        {"id": "b", "component": "Text", "text": "Task"},
    )

    widget = render_widget(surface, "p:")

    assert widget is not None
    assert '<a data-url="https://ag2.ai/" onclick="openLink(this)">' in widget.html
    assert '<div class="text body">Task</div></div>' in widget.html
    assert "widget.openLink(" in widget.html


def test_text_is_escaped():
    surface = flat_surface({"id": "root", "component": "Text", "text": "<b>&</b>"})

    widget = render_widget(surface, "p:")

    assert widget is not None and "&lt;b&gt;&amp;&lt;/b&gt;" in widget.html


def test_what_a_widget_cannot_draw_is_left_out_and_noted():
    surface = flat_surface(
        {"id": "root", "component": "Column", "children": ["t", "chart"]},
        {"id": "t", "component": "Text", "text": "Trend"},
        {"id": "chart", "component": "Chart", "variant": "line"},
    )

    widget = render_widget(surface, "p:")

    assert widget is not None and OMITTED_NOTE in widget.html


def test_a_card_over_telegrams_packed_cap_does_not_fit():
    words = [f"{n:x}{n * 7919 % 104729:x}" for n in range(3000)]
    surface = A2UISurface(
        "s", component={"id": "root", "component": "Text", "text": " ".join(words)}
    )

    widget = render_widget(surface, "p:")

    assert widget is not None
    assert len(zlib.compress(widget.html.encode(), 9)) > MAX_PACKED_BYTES
    assert not widget.fits
