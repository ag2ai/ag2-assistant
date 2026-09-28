#!/bin/sh
# Generate web/.openapi.json — the document the zod gate compares against.
#
# Run from web/ by `npm test`'s pretest hook, so the gate always reads the CURRENT
# app instead of a file someone forgot to refresh. The document is not committed
# (ADR 0028): nothing ships it, FastAPI serves /docs from the live app.
#
# Which interpreter: $PYTHON if set (CI sets it to the uv-managed venv), else the
# repo's .venv, else whatever `python3` resolves to. It must be able to import
# `assistant`, so a bare system python without the project installed will fail —
# loudly, with pip's own message, which is the point.
set -e
repo="$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)"

if [ -n "${PYTHON:-}" ]; then
  py="$PYTHON"
elif [ -x "$repo/.venv/bin/python" ]; then
  py="$repo/.venv/bin/python"
else
  py=python3
fi

exec $py "$repo/scripts/dump_openapi.py" --out "$repo/web/.openapi.json"
