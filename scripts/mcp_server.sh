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

has_deps() { PYTHONPATH="$ROOT/src" "$1" -c "import mcp, pydantic" >/dev/null 2>&1; }

pick_python() {
  # Ordered most-specific first, and each candidate is only accepted if it can
  # actually import the dependencies. Picking an interpreter that merely
  # exists is how the original `"command": "python"` bug happened: it
  # resolved, then died on import. Any environment manager that puts a
  # `bin/python` somewhere findable works here - the check is behavioural,
  # not a list of blessed layouts.
  local candidates=(
    "$ROOT/.venv/bin/python"          # what the README's install produces
    "$ROOT/venv/bin/python"
    "$ROOT/.v/bin/python"
    "${VIRTUAL_ENV:-/nonexistent}/bin/python"
    "${CONDA_PREFIX:-/nonexistent}/bin/python"
  )
  # Any other virtualenv sitting in the repo root, whatever it's called.
  for d in "$ROOT"/*/bin/python; do candidates+=("$d"); done
  candidates+=("$(command -v python3 2>/dev/null || true)")
  candidates+=("$(command -v python  2>/dev/null || true)")

  for py in "${candidates[@]}"; do
    [ -n "$py" ] && [ -x "$py" ] && has_deps "$py" && { echo "$py"; return; }
  done

  # Nothing had the dependencies. Fall back to the most plausible interpreter
  # so the error below can name it and print an install line that will work.
  for py in "$ROOT/.venv/bin/python" "${VIRTUAL_ENV:-/nonexistent}/bin/python" \
            "$(command -v python3 2>/dev/null || true)" "$(command -v python 2>/dev/null || true)"; do
    [ -n "$py" ] && [ -x "$py" ] && { echo "$py"; return; }
  done
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
