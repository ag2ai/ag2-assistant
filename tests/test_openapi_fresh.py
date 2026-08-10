"""The OpenAPI artifact and the structural rules that keep it honest."""

import json
import os
import subprocess
import sys

import pydantic

from assistant.gateway import schemas
from assistant.gateway.openapi_schema import ARTIFACT, build_schema


def test_committed_artifact_matches_the_app():
    """Same idiom as the SPA bundle: the artifact is committed, and regenerating
    it must produce no diff. Run `python3 scripts/dump_openapi.py` to refresh."""
    committed = json.loads(ARTIFACT.read_text())
    assert committed == build_schema(), (
        "docs/openapi.json is stale — run 'python3 scripts/dump_openapi.py' and commit it"
    )


def test_schema_generation_is_deterministic_across_processes():
    """Two builds in ONE process share a hash seed and so prove nothing: the real
    risk is set-iteration order, which is randomised per process. Build the
    document in two fresh interpreters under different seeds instead."""
    script = (
        "import json;from assistant.gateway.openapi_schema import build_schema;"
        "print(json.dumps(build_schema(), sort_keys=True))"
    )
    runs = [
        subprocess.run(
            [sys.executable, "-c", script],
            capture_output=True,
            text=True,
            check=True,
            env={**os.environ, "PYTHONHASHSEED": seed},
        ).stdout
        for seed in ("0", "12345")
    ]
    assert json.loads(runs[0]) == json.loads(runs[1]), (
        "schema generation depends on hash order — a CI run would flake"
    )


def test_every_operation_id_is_unique():
    """operationId must be unique for the document to be valid OpenAPI, and a code
    generator names its client methods from it."""
    ids = [
        operation["operationId"]
        for operations in build_schema()["paths"].values()
        for operation in operations.values()
        if "operationId" in operation
    ]
    duplicates = sorted({name for name in ids if ids.count(name) > 1})
    assert duplicates == [], f"duplicate operationIds: {duplicates}"


def test_error_codes_are_documented_on_a_global_and_a_scoped_route():
    """ERROR_RESPONSES is attached in two places; check one route from each."""
    spec = build_schema()
    for path in ("/api/profiles", "/api/p/{pid}/tasks"):
        codes = spec["paths"][path]["get"]["responses"]
        for code in ("400", "404", "409", "410", "422", "502"):
            ref = codes[code]["content"]["application/json"]["schema"]["$ref"]
            assert ref.endswith("/ErrorBody"), f"{path} {code} -> {ref}"


def test_a_model_drops_keys_it_does_not_declare():
    """The whole point of response_model: the model is the contract, so a key it
    does not declare does not reach the client. Completeness is held up by the
    zod gate and the per-phase body tests, not by letting extras through."""

    class Sample(pydantic.BaseModel):
        declared: str

    assert Sample.model_validate({"declared": "x", "undeclared": 1}).model_dump() == {
        "declared": "x"
    }


def test_error_responses_cover_the_codes_app_py_returns():
    assert set(schemas.ERROR_RESPONSES) == {400, 404, 409, 410, 422, 502}
    assert all(spec["model"] is schemas.ErrorBody for spec in schemas.ERROR_RESPONSES.values())


def test_no_response_description_is_left_to_the_interpreter():
    """Every documented response must state its own ``description``.

    Left off, FastAPI falls back to ``http.HTTPStatus``' reason phrase — and that
    table changes between Python versions (3.13 renamed 422 to "Unprocessable
    Content" and 413 to "Content Too Large" after RFC 9110). The artifact would
    then encode the version that generated it, and CI on another interpreter
    would read a current file as stale. This is how that happened once.
    """
    ours = {
        "200": "Successful Response",  # FastAPI's own constant, not the stdlib's
        "400": "Bad Request",
        "403": "Forbidden",
        "404": "Not Found",
        "409": "Conflict",
        "410": "Gone",
        "413": "Content Too Large",
        "422": "Unprocessable Content",
        "502": "Bad Gateway",
    }
    unexpected = {
        f"{method.upper()} {path} {code} -> {response.get('description')!r}"
        for path, operations in build_schema()["paths"].items()
        for method, operation in operations.items()
        for code, response in operation.get("responses", {}).items()
        if response.get("description") != ours.get(code)
    }
    assert unexpected == set(), f"descriptions not spelled out by this repo: {sorted(unexpected)}"
