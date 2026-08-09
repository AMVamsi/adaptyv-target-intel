# Container image for the target-intel MCP server.
#
# Ported from the thesis pipeline's deployment image. Serves the
# streamable-HTTP transport with a /health endpoint, so the service can sit
# behind an orchestrator that expects to poll HTTP rather than complete an
# MCP handshake to know whether the thing is alive.
FROM python:3.12-slim

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/*

# Dependency install is its own layer, keyed on the manifest only, so a
# source edit doesn't invalidate the pip cache and rebuild the world.
COPY pyproject.toml README.md ./
COPY src/ ./src/
RUN pip install --no-cache-dir .

ENV MCP_TRANSPORT=streamable-http \
    MCP_HOST=0.0.0.0 \
    MCP_PORT=8002 \
    MOCK=1 \
    LITERATURE_MODE=snapshot

EXPOSE 8002

# Matches the compose healthcheck. /health returns 503 when degraded, so
# `curl -f` fails on it without needing to parse the body.
HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
    CMD curl -f http://localhost:8002/health || exit 1

CMD ["target-intel-mcp"]
