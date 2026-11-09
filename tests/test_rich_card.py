"""Drawing an A2UI surface as Telegram rich-message HTML (no network)."""

import pytest

from assistant.a2ui import (
    CARD_PRIMITIVES,
    SUPPORTED_BASIC_COMPONENTS,
)
from assistant.cards import bundled_cards_dir, load_cards
from assistant.channels.rich_card import OMITTED_NOTE, render_card
from tests.support.cards import bundled_surface, flat_surface


def test_the_weather_card_draws_its_place_glyph_and_readings():
    card = render_card(bundled_surface("WeatherPanel"), "a2u:k:")

    assert "<h2>Vienna, Austria</h2>" in card.html
    assert "☀️ <b>24°</b>" in card.html
    assert "<ul><li><b>Temperature</b> 24°C (feels 22°C)</li>" in card.html
    assert (card.actions, card.omitted) == ((), ())


def test_the_weather_cards_hours_are_drawn_as_one_table_column_per_hour():
    card = render_card(bundled_surface("WeatherPanel"), "a2u:k:")

    assert card.html.count("<table>") == 1
    assert "<tr><td><i>Now</i></td><td><i>3pm</i></td>" in card.html
    assert "Now | 3pm | 6pm | 9pm | 12am | 3am\n☀️ | ☀️ | ⛅" in card.text


def test_a_comparison_table_marks_each_rows_winner():
    card = render_card(bundled_surface("DecisionMatrix"), "a2u:k:")

    assert card.html.count("<table>") == 1
    assert "<td><b>~15 h</b></td><td>~10 h</td>" in card.html


def test_bindings_read_the_data_the_item_and_the_map():
    surface = flat_surface(
        {"id": "root", "component": "Column", "children": ["title", "rows"]},
        {
            "id": "title",
            "component": "Text",
            "text": {"path": "/status"},
            "map": {"ok": "All good"},
        },
        {"id": "rows", "component": "List", "children": {"componentId": "row", "path": "/items"}},
        {"id": "row", "component": "Text", "text": {"path": "./name"}},
        data={"status": "ok", "items": [{"name": "one"}, {"name": "two"}]},
    )

    assert render_card(surface, "p:").html == "<p>All good</p><p>one</p><p>two</p>"


def test_a_component_whose_when_is_absent_is_not_drawn():
    surface = flat_surface(
        {"id": "root", "component": "Column", "children": ["shown", "hidden"]},
        {"id": "shown", "component": "Text", "text": "here", "when": {"path": "/yes"}},
        {"id": "hidden", "component": "Text", "text": "gone", "when": {"path": "/no"}},
        data={"yes": True, "no": []},
    )

    assert render_card(surface, "p:").html == "<p>here</p>"


def test_text_is_escaped():
    surface = flat_surface({"id": "root", "component": "Text", "text": "<b>&</b>"})

    assert render_card(surface, "p:").html == "<p>&lt;b&gt;&amp;&lt;/b&gt;</p>"


def test_buttons_become_callbacks_carrying_their_resolved_action():
    surface = flat_surface(
        {"id": "root", "component": "Row", "children": ["yes", "no"]},
        {
            "id": "yes",
            "component": "Button",
            "child": "yes_label",
            "action": {"event": {"name": "book", "context": {"time": {"path": "/time"}}}},
        },
        {"id": "yes_label", "component": "Text", "text": "Book it"},
        {
            "id": "no",
            "component": "Button",
            "child": "no_label",
            "action": {"event": {"name": "skip"}},
        },
        {"id": "no_label", "component": "Text", "text": "Not now"},
        data={"time": "19:00"},
    )

    card = render_card(surface, "a2u:abcd1234:")

    assert card.html == (
        '<tg-button-row><tg-button type="callback_data" data="a2u:abcd1234:0">Book it</tg-button>'
        '<tg-button type="callback_data" data="a2u:abcd1234:1">Not now</tg-button></tg-button-row>'
    )
    book = card.actions[0]
    assert (book.name, book.component_id, book.context) == ("book", "yes", {"time": "19:00"})
    assert book.label == "Book it"
    click = book.incoming("t")
    assert (click.surface_id, click.source_component_id, click.context) == (
        "s1",
        "yes",
        {"time": "19:00"},
    )


def test_a_button_context_keeps_its_literal_objects_and_resolves_bindings_inside_them():
    context = {
        "data": {"status": "booked", "at": {"path": "/time"}},
        "tags": ["vip", {"path": "/time"}],
    }
    surface = flat_surface(
        {
            "id": "root",
            "component": "Button",
            "child": "label",
            "action": {"event": {"name": "save_surface", "context": context}},
        },
        {"id": "label", "component": "Text", "text": "Save"},
        data={"time": "19:00"},
    )

    (save,) = render_card(surface, "a2u:abcd1234:").actions

    assert save.context == {
        "data": {"status": "booked", "at": "19:00"},
        "tags": ["vip", "19:00"],
    }


def test_what_telegram_cannot_draw_is_left_out_and_noted():
    surface = flat_surface(
        {"id": "root", "component": "Column", "children": ["text", "chart", "field"]},
        {"id": "text", "component": "Text", "text": "Prices"},
        {"id": "chart", "component": "Sparkline", "values": [1, 2]},
        {"id": "field", "component": "TextField", "label": "Name"},
    )

    card = render_card(surface, "p:")

    assert card.omitted == ("Sparkline", "TextField")
    assert card.html == f"<p>Prices</p><p><i>{OMITTED_NOTE}</i></p>"


def test_the_plain_text_form_keeps_the_words_without_markup_or_buttons():
    surface = flat_surface(
        {"id": "root", "component": "Column", "children": ["title", "row", "done", "go"]},
        {"id": "title", "component": "Text", "text": "Plan & go", "variant": "h2"},
        {"id": "row", "component": "Row", "children": ["a", "b"]},
        {"id": "a", "component": "Text", "text": "<one>", "emphasis": "strong"},
        {"id": "b", "component": "Text", "text": "two"},
        {"id": "done", "component": "CheckBox", "label": "Packed", "value": True},
        {
            "id": "go",
            "component": "Button",
            "child": "go_label",
            "action": {"event": {"name": "go"}},
        },
        {"id": "go_label", "component": "Text", "text": "<b>Go</b>"},
    )

    card = render_card(surface, "p:")

    assert card.text == "Plan & go\n\n<one> · two\n\n• ☑ Packed"
    assert "&lt;b&gt;Go&lt;/b&gt;</tg-button>" in card.html  # a label is text, escaped once
    assert card.actions[0].label == "<b>Go</b>"


def test_a_card_over_telegrams_caps_does_not_fit():
    long = render_card(
        flat_surface({"id": "root", "component": "Text", "text": "x" * 40_000}), "p:"
    )
    short = render_card(flat_surface({"id": "root", "component": "Text", "text": "x"}), "p:")

    assert (long.fits, short.fits) == (False, True)


# Catalog components Telegram has no form for: a card holding one shows the web-app note.
WEB_ONLY = {
    "CalendarHeatmap",
    "Chart",
    "ChoicePicker",
    "DateTimeInput",
    "Diff",
    "Slider",
    "Sparkline",
    "TextField",
}
_LABELLED_BUTTON = (
    {"id": "root", "component": "Button", "child": "label", "action": {"event": {"name": "go"}}},
    {"id": "label", "component": "Text", "text": "Go"},
)


@pytest.mark.parametrize("name", sorted(SUPPORTED_BASIC_COMPONENTS | CARD_PRIMITIVES))
def test_every_catalog_component_is_drawn_or_named_web_only(name):
    """Every web catalog component is drawn in a rich card or named in `WEB_ONLY`."""
    components = _LABELLED_BUTTON if name == "Button" else ({"id": "root", "component": name},)

    omitted = render_card(flat_surface(*components), "p:").omitted

    assert (name in omitted) == (name in WEB_ONLY)


@pytest.mark.parametrize(
    "name", sorted(load_cards(bundled_cards_dir(), SUPPORTED_BASIC_COMPONENTS | CARD_PRIMITIVES))
)
def test_every_bundled_card_draws_leaving_out_only_web_only_parts(name):
    card = render_card(bundled_surface(name), "p:")

    assert card.html and card.text
    assert set(card.omitted) <= WEB_ONLY


def test_an_eyebrow_over_a_heading_goes_and_a_section_eyebrow_stays_bold():
    surface = flat_surface(
        {"id": "root", "component": "Column", "children": ["kicker", "title", "section", "item"]},
        {"id": "kicker", "component": "Text", "text": "Weather", "variant": "eyebrow"},
        {"id": "title", "component": "Text", "text": "Vienna", "variant": "h1"},
        {"id": "section", "component": "Text", "text": "Next", "variant": "eyebrow"},
        {"id": "item", "component": "Text", "text": "Pack an umbrella"},
    )

    assert render_card(surface, "p:").html == (
        "<h2>Vienna</h2><p><b>Next</b></p><p>Pack an umbrella</p>"
    )


def test_a_row_joins_single_lines_but_keeps_a_column_of_lines_apart():
    surface = flat_surface(
        {"id": "root", "component": "Row", "children": ["a", "b", "col"]},
        {"id": "a", "component": "Text", "text": "Daily"},
        {"id": "b", "component": "Text", "text": "07:00"},
        {"id": "col", "component": "Column", "children": ["c", "d"]},
        {"id": "c", "component": "Text", "text": "one"},
        {"id": "d", "component": "Text", "text": "two"},
    )

    assert render_card(surface, "p:").html == "<p>Daily · 07:00</p><p>one</p><p>two</p>"


def test_a_row_of_stacked_lines_is_drawn_as_a_table_one_column_each():
    surface = flat_surface(
        {"id": "root", "component": "Row", "children": {"componentId": "hour", "path": "/hours"}},
        {"id": "hour", "component": "Column", "children": ["at", "temp"]},
        {"id": "at", "component": "Text", "text": {"path": "./at"}},
        {"id": "temp", "component": "Text", "text": {"path": "./temp"}},
        data={"hours": [{"at": "Now", "temp": "18°"}, {"at": "3pm", "temp": "21°"}]},
    )

    card = render_card(surface, "p:")

    assert card.html == (
        "<table><tr><td>Now</td><td>3pm</td></tr><tr><td>18°</td><td>21°</td></tr></table>"
    )
    assert card.text == "Now | 3pm\n18° | 21°"
