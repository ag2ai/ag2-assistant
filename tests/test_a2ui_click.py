"""Reading a client's A2UI click envelope (no gateway)."""

import pytest

from assistant.a2ui_click import InvalidClick, parse_click


def test_a_well_formed_envelope_reads_as_its_action():
    click = parse_click(
        {
            "version": "1.0",
            "action": {
                "name": "book",
                "surfaceId": "s1",
                "sourceComponentId": "yes",
                "timestamp": "t",
                "context": {"time": "19:00"},
            },
        }
    )

    assert (click.name, click.surface_id, click.context) == ("book", "s1", {"time": "19:00"})


@pytest.mark.parametrize(
    ("message", "reason"),
    [
        ({"version": "1.0"}, "Invalid A2UI action."),
        ({"version": "1.0", "action": {"name": "book"}}, "Unsupported A2UI action."),
    ],
)
def test_an_envelope_that_is_not_a_usable_action_is_refused(message, reason):
    with pytest.raises(InvalidClick, match=reason):
        parse_click(message)
