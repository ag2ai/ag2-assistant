"""Moving routes out of app.py must change neither the route table nor its order.

Order is load-bearing: FastAPI matches in registration order, so a literal path
registered after the parameterised one that covers it becomes unreachable
(POST /api/llm-configs/test vs POST /api/llm-configs/{cid}). A pure move that
reorders is not a pure move.
"""

from fastapi import routing
from fastapi.routing import APIRoute

from assistant.gateway.app import create_app
from tests.support.apps import make_manager, make_paths


def _table(app) -> list[tuple[str, str]]:
    """Every route the app matches on, in registration order.

    Read through ``iter_route_contexts``, not ``app.routes``: since FastAPI
    0.137 an included router is stored as one opaque node rather than flattened,
    so walking ``app.routes`` would see the node and none of the routes behind
    it — a move into a router would read as the routes vanishing AND as the
    order being intact. This is the same traversal ``get_openapi`` uses, so the
    table matches the document the zod gate compares against.
    """
    return [
        (method, context.path)
        for context in routing.iter_route_contexts(app.routes)
        if isinstance(context.route, APIRoute)
        for method in sorted(context.methods)
    ]


def test_the_two_catch_alls_are_registered_last(tmp_path):
    """Everything else must be included before them, or an unmatched /api write
    would win over a real route."""
    app = create_app(make_manager(make_paths(tmp_path)))
    tail = [path for _method, path in _table(app)][-2:]
    assert tail == ["/api/{full_path:path}", "/{full_path:path}"]


def test_a_literal_path_is_registered_before_the_parameter_that_covers_it(tmp_path):
    """The invariant a careless move would break."""
    app = create_app(make_manager(make_paths(tmp_path)))
    table = _table(app)
    for literal, parameterised in (
        ("/api/llm-configs/test", "/api/llm-configs/{cid}"),
        ("/api/live-configs/test", "/api/live-configs/{cid}"),
        ("/api/secrets/key", "/api/secrets/{sid}"),
        ("/api/coding/agents", "/api/coding/{agent}/models"),
    ):
        first = next(i for i, (_m, p) in enumerate(table) if p == literal)
        second = next(i for i, (_m, p) in enumerate(table) if p == parameterised)
        assert first < second, f"{literal} must be registered before {parameterised}"
