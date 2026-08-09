"""
Protocol-level MCP tests.

`test_engine_and_mcp.py` calls the tool functions directly, which proves the
business logic but NOT that the server speaks MCP - a server can have
perfectly good tool functions and still fail to import, register or
serialize. These tests launch the server as a real subprocess over stdio
and drive it with a real ClientSession: initialize -> tools/list ->
tools/call -> parse the payload back.

That is the exact path Claude Code takes when it reads `.mcp.json`, so
these tests cover the install story as well as the code. They are also
deliberately written against `ClientSession`/`stdio_client`, whose API is
stable across mcp 1.x and 2.x, rather than an in-memory helper that isn't -
the point is to catch version breakage, not to depend on it.

Each test opens and closes its own server subprocess through `session()`
rather than a shared async fixture. The stdio transport is built on anyio
cancel scopes, which must be entered and exited inside the same task; an
async-generator fixture yields across a task boundary and tears down with
"attempted to exit cancel scope in a different task". Keeping the `async
with` inside the test body sidesteps that entirely, and gives every test a
clean server process for free.
"""

from __future__ import annotations

import json
import os
import sys
from contextlib import asynccontextmanager
from pathlib import Path

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

SRC = str(Path(__file__).parent.parent / "src")

EXPECTED_TOOLS = {
    "list_targets",
    "list_experiments",
    "get_target_literature_context",
    "interpret_experiment_result",
    "get_portfolio_coverage_gaps",
    "export_target_knowledge_graph_cypher",
    "get_calibration_report",
}

EGFR_EXP = "019d4a2b-3c5e-7890-a002-000000000002"


@asynccontextmanager
async def session():
    """A live client session against the server running as a subprocess."""
    params = StdioServerParameters(
        command=sys.executable,
        args=["-m", "target_intel.mcp_server.server"],
        env={
            **os.environ,
            "PYTHONPATH": SRC,
            "MOCK": "1",
            "LITERATURE_MODE": "demo",  # deterministic fixtures; also never reaches the network
        },
    )
    async with stdio_client(params) as (read, write), ClientSession(read, write) as client:
        await client.initialize()
        yield client


def _is_error(result) -> bool:
    """mcp 1.x spells this `isError`, mcp 2.x spells it `is_error`."""
    return bool(getattr(result, "is_error", None) or getattr(result, "isError", None))


def _text(result) -> str:
    return result.content[0].text


def _payload(result):
    """Pull the JSON body back out of an MCP tool result."""
    assert not _is_error(result), result.content
    return json.loads(_text(result))


async def test_server_advertises_every_tool_with_a_description():
    async with session() as client:
        listed = await client.list_tools()
        assert {t.name for t in listed.tools} == EXPECTED_TOOLS
        # An agent selects a tool on its description, so an undescribed tool is
        # an unusable one.
        for tool in listed.tools:
            assert tool.description, f"{tool.name} has no description"


async def test_interpret_experiment_result_over_the_wire():
    async with session() as client:
        payload = _payload(await client.call_tool("interpret_experiment_result", {"experiment_id": EGFR_EXP}))

        assert payload["target_name"] == "EGFR"
        assert payload["flagged_count"] == 1
        flagged = [v for v in payload["verdicts"] if v["flag_for_review"]]
        assert len(flagged) == 1
        assert flagged[0]["label"] == "outside_known_range_flag_artifact"
        # A verdict with no rationale isn't actionable for a scientist.
        assert flagged[0]["rationale"]
        assert flagged[0]["citations_pmids"]


async def test_literature_context_over_the_wire():
    async with session() as client:
        ctx = _payload(await client.call_tool("get_target_literature_context", {"target_id": "comp-her2-human"}))
        assert "trastuzumab" in ctx["known_binders"]
        assert ctx["literature_density"] in ("rich", "moderate", "sparse")
        assert ctx["calibration_status"] == "calibrated"


async def test_calibration_report_ships_its_sample_size():
    async with session() as client:
        calib = _payload(await client.call_tool("get_calibration_report", {}))
        assert 0.0 <= calib["ece"] <= 1.0
        # The sample size must travel with the number, never be quoted bare.
        assert calib["n_golden_examples"] >= 10


async def test_conflicting_target_is_reported_not_averaged():
    async with session() as client:
        ctx = _payload(await client.call_tool("get_target_literature_context", {"target_id": "comp-cd20-human"}))
        assert ctx["evidence_level"] == "conflicting"
        # Both disagreeing sources must be cited, not silently blended.
        assert len(ctx["conflicting_source_ranges"]) >= 1


async def test_cypher_export_is_a_string_not_a_json_blob():
    async with session() as client:
        result = await client.call_tool("export_target_knowledge_graph_cypher", {"target_id": "comp-her2-human"})
        assert not _is_error(result)
        cypher = _text(result)
        assert "MERGE" in cypher and "BINDS" in cypher


async def test_unknown_target_surfaces_an_error_not_a_silent_empty():
    async with session() as client:
        result = await client.call_tool("get_target_literature_context", {"target_id": "comp-does-not-exist"})
        assert _is_error(result)


async def test_coverage_gaps_are_never_an_unexplained_empty_list_over_the_wire():
    """The demo path. An agent that receives `[]` here will tell a scientist
    the portfolio is covered; the truth on the shipped snapshot is that the
    check couldn't run. The status field is what stops that."""
    async with session() as client:
        report = _payload(await client.call_tool("get_portfolio_coverage_gaps", {}))

        assert isinstance(report, dict), "a bare list gives the agent nothing to reason with"
        assert report["analysis_status"] in ("gaps_found", "insufficient_epitope_data")
        if not report["gaps"]:
            assert report["analysis_status"] == "insufficient_epitope_data"
            assert report["note"]


async def test_literature_context_separates_the_core_range_from_the_envelope():
    """Given one range an agent will use it for everything. HER2's envelope
    spans 1e-13 to 1.5e-7 M and comparing a result against that is
    meaningless, so both ship, each named for what it is."""
    async with session() as client:
        ctx = _payload(await client.call_tool("get_target_literature_context", {"target_id": "comp-her2-human"}))

        assert ctx["expected_kd_low_m"] is not None
        assert ctx["envelope_kd_low_m"] <= ctx["expected_kd_low_m"]
        assert ctx["envelope_kd_high_m"] >= ctx["expected_kd_high_m"]
        assert "interquartile" in ctx["expected_kd_basis"]


def test_mcp_config_launches_without_client_side_variable_substitution():
    """The install story fails here or nowhere else, and it failed twice.

    First `"command": "python"` - absent on Debian/Ubuntu, and a
    dependency-less system interpreter almost everywhere else. Then
    `"command": "${CLAUDE_PLUGIN_ROOT}/scripts/mcp_server.sh"`, which only
    gets substituted for *plugin*-installed servers; a project-scoped
    `.mcp.json` passes it through literally and the spawn ENOENTs.

    So the config must not depend on the client expanding anything. `bash`
    is on PATH everywhere, and the shell resolves the path at runtime -
    `$CLAUDE_PLUGIN_ROOT` when a plugin exports it, `$PWD` otherwise.
    """
    import json
    import os
    from pathlib import Path

    root = Path(__file__).resolve().parent.parent
    server = json.loads((root / ".mcp.json").read_text())["mcpServers"]["target-intel"]

    # The command itself must be resolvable from PATH with no substitution.
    assert server["command"] == "bash"
    assert "${" not in server["command"], "client-side substitution is not reliable here"

    launcher = root / "scripts" / "mcp_server.sh"
    assert str(launcher.relative_to(root)) in " ".join(server["args"])
    assert launcher.exists(), "launcher referenced by .mcp.json is missing"
    assert os.access(launcher, os.X_OK), "launcher is not executable"
