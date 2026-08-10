#!/usr/bin/env bash
# Launcher for the MCP server, used by .mcp.json.
#
# `"command": "python"` fails on most machines for two separate reasons:
# Debian/Ubuntu ship `python3` and no `python`, and where `python` does
# resolve it is the system interpreter, without this project's dependencies.
# So the interpreter is resolved here instead, and a failure prints an
# install line rather than a stack trace.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

has_deps() { PYTHONPATH="$ROOT/src" "$1" -c "import mcp, pydantic" >/dev/null 2>&1; }

pick_python() {
  # A candidate is accepted only if it can import the dependencies. Testing
  # for existence is what the original bug did: `python` resolved, then died
  # on import. Behavioural check, so any environment layout works.
  local candidates=(
    "$ROOT/.venv/bin/python"          # what the README's install produces
    "$ROOT/venv/bin/python"
    "${VIRTUAL_ENV:-/nonexistent}/bin/python"
    "${CONDA_PREFIX:-/nonexistent}/bin/python"
  )
  for d in "$ROOT"/*/bin/python; do candidates+=("$d"); done
  candidates+=("$(command -v python3 2>/dev/null || true)")
  candidates+=("$(command -v python  2>/dev/null || true)")

  for py in "${candidates[@]}"; do
    [ -n "$py" ] && [ -x "$py" ] && has_deps "$py" && { echo "$py"; return; }
  done

  # Nothing had the dependencies. Return the most plausible interpreter so the
  # error below can name it in an install line that will actually work.
  for py in "$ROOT/.venv/bin/python" "${VIRTUAL_ENV:-/nonexistent}/bin/python" \
            "$(command -v python3 2>/dev/null || true)" "$(command -v python 2>/dev/null || true)"; do
    [ -n "$py" ] && [ -x "$py" ] && { echo "$py"; return; }
  done
}

# `|| true` because pick_python returns non-zero when it finds nothing, and
# under `set -e` that would abort before the message below is printed.
PY="$(pick_python || true)"

if [ -z "$PY" ]; then
  echo "target-intel: no Python interpreter found (looked for .venv, \$VIRTUAL_ENV, python3, python)." >&2
  exit 127
fi

if ! has_deps "$PY"; then
  echo "target-intel: '$PY' is missing this project's dependencies." >&2
  echo "  Install them into it:  '$PY' -m pip install -e '$ROOT[dev]'" >&2
  exit 1
fi

export PYTHONPATH="$ROOT/src${PYTHONPATH:+:$PYTHONPATH}"
exec "$PY" -m target_intel.mcp_server.server "$@"
