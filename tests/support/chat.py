"""Driving a Chat through the public routes, and reading back what it logged."""

import json
import time

from assistant.gateway.wire import as_drawn, is_binary_event, to_wire
from tests.support.apps import api


def gateway_of(app, pid):
    return app.state.profiles.get(pid).require_gateway()


async def drawn_log(gateway, chat="c1") -> list[dict]:
    """The Chat's whole Event log, as the `{type, data}` wire shape a client is drawn."""
    return [
        to_wire(as_drawn(event, gateway.catalog, gateway.card_sources))
        for event in await gateway.replay_events(chat)
        if not is_binary_event(event)
    ]


def logged(client, app, pid, chat="c1") -> list[dict]:
    return client.portal.call(drawn_log, gateway_of(app, pid), chat)


def run_input(text=None, resume=None, run_id="r1", chat="c1", props=None):
    body = {
        "threadId": chat,
        "runId": run_id,
        "state": {},
        "messages": [{"id": "m1", "role": "user", "content": text}] if text else [],
        "tools": [],
        "context": [],
        "forwardedProps": props or {},
    }
    if resume:
        body["resume"] = resume
    return body


def sse(response) -> list[dict]:
    return [json.loads(line[6:]) for line in response.iter_lines() if line.startswith("data: ")]


def run(client, pid, chat="c1", **kwargs) -> list[dict]:
    """POST one AG-UI run and read its events to the end of the exchange."""
    with client.stream(
        "POST", api(pid, f"/chats/{chat}/agui"), json=run_input(chat=chat, **kwargs)
    ) as response:
        assert response.status_code == 200, response.read()
        return sse(response)


def click(client, pid, message, state=None, chat="c1"):
    body = {"message": message} if state is None else {"message": message, "state": state}
    return client.post(api(pid, f"/chats/{chat}/a2ui"), json=body)


def wait_for(condition, timeout=5.0):
    deadline = time.monotonic() + timeout
    while not condition():
        assert time.monotonic() < deadline, "timed out waiting"
        time.sleep(0.02)
