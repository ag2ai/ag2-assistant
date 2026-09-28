"""Generate the gateway's OpenAPI document — the input to the zod gate.

The file is generated, never committed (ADR 0028): nothing ships it, and `web/`
regenerates it before `npm test`. Run it by hand to read the document, or to point
a code generator at it.

    python3 scripts/dump_openapi.py                 # → web/.openapi.json
    python3 scripts/dump_openapi.py --out /tmp/x.json
"""

import argparse
from pathlib import Path

from assistant.gateway.openapi_schema import DEFAULT_OUT, write_schema

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--out",
        type=Path,
        default=DEFAULT_OUT,
        help=f"where to write the document (default: {DEFAULT_OUT})",
    )
    print(f"wrote {write_schema(parser.parse_args().out)}")
