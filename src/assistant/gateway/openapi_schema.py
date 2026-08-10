"""The gateway's OpenAPI document, generated on demand.

The document depends on route declarations alone — no lifespan runs, so no
profile is booted and no collaborator is called. A throwaway layout keeps
generation off the developer's real install and makes the output deterministic.

It is NOT committed. Nothing ships it — FastAPI serves ``/docs`` from the live
app — so its only consumer is the zod gate in ``web/`` (ADR 0028), which
regenerates it before ``npm test``. A copy in git would have guaranteed nothing
but itself, at 500 KB of generated diff per route change.
"""

import json
import re
import tempfile
from pathlib import Path

from assistant.gateway.app import create_app
from assistant.gateway.profile_manager import ProfileManager
from assistant.paths import Paths

# Where the gate reads it from (``web/src/schemas/routes.test.ts``), and therefore
# where a bare ``dump_openapi.py`` writes it. Gitignored.
DEFAULT_OUT = Path(__file__).resolve().parents[3] / "web" / ".openapi.json"


def _stable_operation_ids(spec: dict) -> dict:
    """Give every operation an id derived from its own method and path.

    FastAPI's default builds the id from ``list(route.methods)[0]`` — and
    ``methods`` is a SET, whose iteration order varies between processes because
    string hashing is randomised. A route declared for several methods at once
    (``api_route(..., methods=["POST", "PUT", "PATCH", "DELETE"])``) therefore gets
    a different id on every run, AND the same id for all four, which is invalid
    OpenAPI besides. Both problems go away once the id is a function of the
    operation itself, which is also what a code generator wants.
    """
    for path, operations in spec.get("paths", {}).items():
        for method, operation in operations.items():
            if isinstance(operation, dict) and "operationId" in operation:
                slug = re.sub(r"\W", "_", path).strip("_")
                operation["operationId"] = f"{slug}_{method}"
    return spec


def build_schema() -> dict:
    """The OpenAPI document, built over a throwaway install layout."""
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        paths = Paths(
            root=root / "state",
            workspace=root / "workspace",
            codex_auth=root / "codex-cli-auth.json",
            home=root / "home",
        )
        app = create_app(ProfileManager(paths, persist=False))
        return _stable_operation_ids(app.openapi())


def write_schema(out: Path | None = None) -> Path:
    """Write the document to ``out`` (default ``DEFAULT_OUT``) and return the path.

    ``sort_keys`` because a generated file that a human occasionally opens is worth
    reading in a stable order, not because anything compares two copies.
    """
    target = Path(out) if out is not None else DEFAULT_OUT
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(build_schema(), indent=2, sort_keys=True) + "\n")
    return target
