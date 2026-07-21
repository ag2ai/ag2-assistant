#!/bin/sh
# AG2 Assistant installer — installs the ag2-assistant CLI as an isolated uv tool.
#
#   curl -fsSL https://raw.githubusercontent.com/ag2ai/ag2-assistant/main/scripts/install.sh | sh
#
# Installs uv first if it isn't present (uv also downloads a compatible Python,
# so no system Python 3.12 is required). Re-running the script upgrades an
# existing install to the latest commit on the ref.
#
# Environment overrides:
#   AG2_ASSISTANT_REF     git branch or tag to install (default: main)
#   AG2_ASSISTANT_EXTRAS  optional extras, e.g. "google" (default: none)
#   AG2_ASSISTANT_REPO    git URL to install from, e.g. a fork (default: the ag2ai repo)
set -eu

REF="${AG2_ASSISTANT_REF:-main}"
EXTRAS="${AG2_ASSISTANT_EXTRAS:-}"
REPO="${AG2_ASSISTANT_REPO:-https://github.com/ag2ai/ag2-assistant.git}"
SPEC="ag2-assistant${EXTRAS:+[$EXTRAS]} @ git+${REPO}@${REF}"

if ! command -v git >/dev/null 2>&1; then
    echo "error: git is required (the AG2 dependency installs from git)." >&2
    echo "       Install git (https://git-scm.com/downloads) and re-run this script." >&2
    exit 1
fi

if ! command -v uv >/dev/null 2>&1; then
    echo "uv not found — installing it first (https://astral.sh/uv) ..."
    curl -LsSf https://astral.sh/uv/install.sh | sh
    # The uv installer targets ~/.local/bin (or XDG_BIN_HOME); pick it up for this run.
    PATH="${XDG_BIN_HOME:-$HOME/.local/bin}:$PATH"
    export PATH
fi

echo "Installing ag2-assistant (${REF}) ..."
uv tool install --force --python ">=3.12" "$SPEC"

echo ""
if command -v ag2-assistant >/dev/null 2>&1; then
    echo "Installed: $(ag2-assistant --version 2>/dev/null || echo ag2-assistant)"
else
    # uv placed the executable outside PATH; it prints the same warning itself.
    echo "Installed, but the uv tool bin directory is not on your PATH."
    echo "Run 'uv tool update-shell' (then open a new terminal) to fix that."
fi
echo ""
echo "Start it with:"
echo "  ag2-assistant run        # gateway + web UI at http://localhost:8800/"
