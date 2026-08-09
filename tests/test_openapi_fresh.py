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


def test_every_schema_model_inherits_response_model():
    """A model that inherits BaseModel directly would drop undeclared keys."""
    offenders = []
    for name in dir(schemas):
        obj = getattr(schemas, name)
        if not isinstance(obj, type) or not issubclass(obj, pydantic.BaseModel):
            continue
        if obj is schemas.ResponseModel:
            continue
        if not issubclass(obj, schemas.ResponseModel):
            offenders.append(name)
    assert offenders == [], f"must inherit ResponseModel: {offenders}"


def test_response_model_allows_undeclared_keys():
    """extra="allow" is the whole safety story: an incomplete model must not
    silently strip a key the gateway really sends."""

    class Sample(schemas.ResponseModel):
        declared: str

    parsed = Sample.model_validate({"declared": "x", "undeclared": 1})
    assert parsed.model_dump() == {"declared": "x", "undeclared": 1}


def test_error_responses_cover_the_codes_app_py_returns():
    assert set(schemas.ERROR_RESPONSES) == {400, 404, 409, 410, 422, 502}
    assert all(spec["model"] is schemas.ErrorBody for spec in schemas.ERROR_RESPONSES.values())
