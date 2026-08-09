"""Regenerate docs/openapi.json — the gateway's committed OpenAPI document.

Run after changing any route's response model, then commit the result. CI fails
if the committed file and the app disagree (tests/test_openapi_fresh.py).

    python3 scripts/dump_openapi.py
"""

from assistant.gateway.openapi_schema import ARTIFACT, write_artifact

if __name__ == "__main__":
    changed = write_artifact()
    print(f"{'wrote' if changed else 'unchanged'} {ARTIFACT}")
