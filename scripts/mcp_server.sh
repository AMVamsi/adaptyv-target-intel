#!/usr/bin/env bash
# Launcher for the MCP server, used by .mcp.json.
#
# This exists because "command": "python" in an MCP config is wrong on most
# machines, in two independent ways:
#
#   1. Debian/Ubuntu ship `python3` and no `python` at all, so the config
#      fails before it starts with `Executable not found in $PATH: "python"`.
#   2. Even where `python` resolves, it's the *system* interpreter, which
#      doesn't have this project's dependencies - those live in whatever
#      virtualenv `pip install -e .` was run in.
#
# A config that only works when the user happens to have activated the right
# venv before launching their editor isn't installable, it's a puzzle. So we
# resolve the interpreter here, in order of how likely it is to be correct,
# and fail with an instruction rather than a stack trace.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

pick_python() {
  # A venv inside the repo is the most specific answer available, and the
  # one the README's install instructions produce.
  for venv in "$ROOT/.venv" "$ROOT/venv"; do
    [ -x "$venv/bin/python" ] && { echo "$venv/bin/python"; return; }
  done

  # Otherwise trust the caller's environment - an activated venv exports
  # VIRTUAL_ENV, and `python3`/`python` follow PATH.
  [ -n "${VIRTUAL_ENV:-}" ] && [ -x "$VIRTUAL_ENV/bin/python" ] && { echo "$VIRTUAL_ENV/bin/python"; return; }
  command -v python3 2>/dev/null && return
  command -v python  2>/dev/null && return
}

# `|| true` matters: pick_python returns non-zero when it finds nothing, and
# under `set -e` a failing command substitution would abort the script right
# here - swallowing the actionable message below in favour of a silent exit.
PY="$(pick_python || true)"

if [ -z "$PY" ]; then
  echo "target-intel: no Python interpreter found (looked for .venv, \$VIRTUAL_ENV, python3, python)." >&2
  exit 127
fi

# Check the deps are actually importable before handing control to the MCP
# runtime. Failing here prints something a person can act on; failing later
# surfaces as an opaque "server exited" in the client.
if ! PYTHONPATH="$ROOT/src" "$PY" -c "import mcp, pydantic" 2>/dev/null; then
  echo "target-intel: '$PY' is missing this project's dependencies." >&2
  echo "  Install them into it:  '$PY' -m pip install -e '$ROOT[dev]'" >&2
  exit 1
fi

export PYTHONPATH="$ROOT/src${PYTHONPATH:+:$PYTHONPATH}"
exec "$PY" -m target_intel.mcp_server.server "$@"
