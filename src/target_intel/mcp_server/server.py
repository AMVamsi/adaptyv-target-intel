"""
MCP server for the Target Intelligence layer.

This exposes the *interpretation* capability (literature-grounded verdicts
on experiment results) as MCP tools, plus thin read wrappers over the
Foundry API resources this project touches (targets, experiments,
results). It deliberately does NOT reimplement Adaptyv's own excellent
public MCP server (mcp.adaptyvbio.com) for driving the full experiment
lifecycle (create/submit/quote/confirm) - this server is additive,
meant to sit next to that one (or next to an internal lab-assistant MCP
server) and answer a question neither of those answer: "does this result
make sense given what's known about this target?"

Run with:
    target-intel-mcp                                  # mock mode (default)
    python -m target_intel.mcp_server.server          # equivalent
    FOUNDRY_API_TOKEN=... MOCK=0 LITERATURE_MODE=live target-intel-mcp

Point Claude Desktop/Code's MCP config at that command (stdio transport);
see the README for a copy-pasteable `claude mcp add` line.

Every tool below is also a plain Python function: the FastMCP `@tool`
decorator registers it and hands the original callable back, so the CLI,
the tests and the dashboard can call these directly without standing up a
server. That is deliberate - one implementation, three entry points.
"""

from __future__ import annotations

import os

from ..engine import TargetIntelligenceEngine
from ..interpretation.coverage import MULTI_EPITOPE_THRESHOLD

try:  # mcp >= 2.0 - what a fresh `pip install mcp` resolves to today
    from mcp.server.mcpserver import MCPServer as _Server
except ImportError:  # mcp 1.x - still widely pinned in existing environments
    from mcp.server.fastmcp import FastMCP as _Server  # type: ignore[assignment]

# The two classes were renamed, not redesigned: same constructor kwargs,
# same `@tool(description=...)` decorator returning the original function,
# same `run(transport=...)`. Everything below is written once against that
# shared surface, so this server runs unmodified on either SDK generation
# rather than forcing a version pin on whoever installs it.

_MOCK = os.environ.get("MOCK", "1") != "0"
_LITERATURE_MODE = os.environ.get("LITERATURE_MODE", "snapshot")

server = _Server(
    name="adaptyv-target-intel",
    instructions=(
        "Tools for grounding Adaptyv Foundry protein-binding results in the "
        "existing literature: what's already known about a target's "
        "validated binders/epitopes/affinity range, and whether a given "
        "experimental result is consistent with that, novel, or "
        "statistically implausible (possible assay artifact). Use "
        "get_target_literature_context before interpret_experiment_result "
        "if you want to inspect the literature evidence directly."
    ),
)

_engine: TargetIntelligenceEngine | None = None


def _get_engine() -> TargetIntelligenceEngine:
    global _engine
    if _engine is None:
        _engine = TargetIntelligenceEngine(mock=_MOCK, literature_mode=_LITERATURE_MODE)
    return _engine


@server.tool(description="List target antigens in the Foundry catalog, optionally filtered by a search substring.")
def list_targets(search: str | None = None) -> list[dict]:
    engine = _get_engine()
    return [t.model_dump() for t in engine.client.list_targets(search=search)]


@server.tool(description="List experiments, optionally filtered by status (draft, done, in_production, etc).")
def list_experiments(status: str | None = None) -> list[dict]:
    engine = _get_engine()
    return [
        {
            "experiment_id": e.experiment_id,
            "experiment_code": e.experiment_code,
            "name": e.name,
            "status": e.status.value,
            "results_status": e.results_status.value,
            "target_id": e.experiment_spec.target_id,
            "experiment_type": e.experiment_spec.experiment_type.value,
        }
        for e in engine.client.list_experiments(status=status)
    ]


@server.tool(
    description=(
        "Get the literature-grounded profile for a Foundry target: known "
        "binders, known epitopes, the expected affinity (KD) range for "
        "validated binders, a calibrated confidence score for how much to "
        "trust that prior, and supporting citations. Use this to sanity-"
        "check a result manually, or before calling interpret_experiment_result."
    )
)
def get_target_literature_context(target_id: str) -> dict:
    engine = _get_engine()
    ctx = engine.get_target_context(target_id)
    return {
        "target_id": target_id,
        "target_name": ctx.target.name,
        # Which protein, exactly. A verdict about "HER2" is only as
        # trustworthy as the answer to that, so the canonical NCBI gene and
        # RefSeq protein accession travel with every literature context.
        "ncbi": ctx.ncbi,
        "uniprot_hint": ctx.target.uniprot_hint,
        "literature_density": ctx.prior.density.value,
        "evidence_level": ctx.prior.evidence_level.value,
        "calibrated_confidence": round(ctx.prior.calibrated_confidence, 4),
        # "calibrated" whenever the fit matches the corpus being served
        # (snapshot or demo); "uncalibrated_live" means the confidence above
        # is an ordering signal, not a probability.
        "calibration_status": ctx.prior.calibration_status.value,
        "known_binders": ctx.prior.known_binders,
        "known_epitopes": ctx.prior.known_epitopes,
        # Two ranges, because they answer different questions and an agent
        # given only one will use it for both. `expected_*` is the robust
        # log-space IQR - the range a result is actually judged against.
        # `envelope_*` is every value extracted, which on real literature
        # spans orders of magnitude legitimately and is far too wide to
        # compare a result to. Quoting the envelope as "the expected range"
        # is the specific mistake this split exists to prevent.
        "expected_kd_low_m": ctx.prior.low_m,
        "expected_kd_high_m": ctx.prior.high_m,
        "expected_kd_basis": "log-space interquartile range of extracted affinities",
        "envelope_kd_low_m": ctx.prior.envelope_low_m,
        "envelope_kd_high_m": ctx.prior.envelope_high_m,
        "n_quantitative_sources": ctx.prior.n_quantitative_sources,
        "caveat_ceiling_m": ctx.prior.caveat_ceiling_m,
        "n_abstracts_reviewed": ctx.prior.n_abstracts,
        "citations_pmids": ctx.prior.pmids,
        "conflicting_source_ranges": [
            {"pmid": pmid, "low_m": lo, "high_m": hi} for pmid, lo, hi in ctx.prior.conflicting_source_ranges
        ],
    }


@server.tool(
    description=(
        "Interpret every result in a completed experiment against the "
        "target's literature context. Returns one verdict per sequence: "
        "'consistent_with_literature' (expected hit), 'novel_candidate' "
        "(binding found but little precedent - worth a second look), "
        "'outside_known_range_flag_artifact' (numbers implausible vs. "
        "literature - check for an assay artifact before reporting to a "
        "customer), 'literature_conflict' (independent sources disagree "
        "with each other, often due to different binding formats - "
        "declining to call this consistent or an artifact until that's "
        "resolved), 'weaker_than_typical', 'qualitative_literature_only', "
        "or 'no_binding'. Each verdict includes a rationale and citations."
    )
)
def interpret_experiment_result(experiment_id: str) -> dict:
    engine = _get_engine()
    result = engine.interpret_experiment(experiment_id)
    return {
        "experiment_id": result.experiment_id,
        "target_name": result.target.name,
        "literature_density": result.prior.density.value,
        "evidence_level": result.prior.evidence_level.value,
        "calibration_status": result.prior.calibration_status.value,
        "flagged_count": result.flagged_count,
        "verdicts": [
            {
                "label": v.label.value,
                "flag_for_review": v.flag_for_review,
                "confidence": round(v.confidence, 4),
                "rationale": v.rationale,
                "citations_pmids": v.citations,
            }
            for v in result.verdicts
        ],
    }


@server.tool(
    description=(
        "Portfolio-level gap analysis: flags targets where the literature "
        "documents multiple independent epitope classes, meaning a "
        "standard affinity/screening assay alone can't confirm which "
        "epitope(s) a campaign's candidates actually engage. Recommends "
        "epitope-binning/competition assays where relevant. Scoped "
        "honestly - this does not claim to know which epitope any "
        "specific sequence hit, only that the target has enough known "
        "epitope diversity that this is worth checking. IMPORTANT: read "
        "`analysis_status` before reporting the result. An empty `gaps` "
        "list means 'no gaps could be assessed' when the status is "
        "`insufficient_epitope_data` - it does NOT mean the portfolio is "
        "fully covered."
    )
)
def get_portfolio_coverage_gaps() -> dict:
    """Returns the gap list *and* enough context to interpret an empty one.

    An agent handed a bare `[]` has exactly two readings available - "no
    gaps, all clear" and "the analysis found nothing to work with" - and
    they lead to opposite advice. On the shipped snapshot the true answer
    is the second: epitope recall on real abstracts is the weakest part of
    the extractor, so it finds one epitope across five targets, below the
    two-class threshold this analysis needs. A tool that returns `[]` there
    is inviting the assistant to tell a scientist their coverage is fine.

    So the empty case is typed rather than left to inference.
    """
    engine = _get_engine()
    notes = engine.portfolio_coverage_report()
    gaps = [
        {
            "target_id": n.target_id,
            "target_name": n.target_name,
            "known_epitopes": n.known_epitopes,
            "note": n.note,
        }
        for n in notes
    ]

    targets = engine.client.list_targets()
    n_with_epitopes = sum(
        1 for t in targets if engine.get_target_context(t.target_id).prior.known_epitopes
    )

    if gaps:
        status = "gaps_found"
        note = f"{len(gaps)} of {len(targets)} targets show multi-epitope literature."
    else:
        status = "insufficient_epitope_data"
        note = (
            f"No gaps could be assessed. This analysis needs at least "
            f"{MULTI_EPITOPE_THRESHOLD} independently-named epitope classes per target, "
            f"and epitope extraction found any epitope at all for only {n_with_epitopes} "
            f"of {len(targets)} targets - the epitope pattern matches 'domain I-V' / "
            "'IgV domain' phrasing that real abstracts often don't use. Report this as "
            "'the check could not run', not as 'no coverage gaps exist'."
        )

    return {
        "gaps": gaps,
        "analysis_status": status,
        "n_targets_assessed": len(targets),
        "n_targets_with_any_epitope": n_with_epitopes,
        "epitope_classes_required": MULTI_EPITOPE_THRESHOLD,
        "note": note,
    }


@server.tool(
    description=(
        "Export the provenance-tagged knowledge graph (target -> epitope "
        "-> binder, with KD ranges and confidence) for a target as loadable "
        "Neo4j Cypher statements."
    )
)
def export_target_knowledge_graph_cypher(target_id: str) -> str:
    engine = _get_engine()
    ctx = engine.get_target_context(target_id)
    return ctx.knowledge_graph.to_cypher()


@server.tool(
    description=(
        "Report the confidence-calibration quality of the literature-"
        "grounding layer itself: the fitted temperature, Expected "
        "Calibration Error (ECE) on the golden set, and per-bin accuracy. "
        "Use this to answer 'how much should I trust these confidence "
        "scores' rather than any single verdict."
    )
)
def get_calibration_report() -> dict:
    return _get_engine().calibration_report()


TOOL_NAMES = [
    "list_targets",
    "list_experiments",
    "get_target_literature_context",
    "interpret_experiment_result",
    "get_portfolio_coverage_gaps",
    "export_target_knowledge_graph_cypher",
    "get_calibration_report",
]


@server.custom_route("/health", methods=["GET"])
async def health(_request):
    """Liveness/readiness probe for the HTTP transports.

    Registered as a plain HTTP route rather than an MCP tool because the
    thing that polls it - Docker, a load balancer, Adaptyv's own
    orchestration - speaks HTTP, not MCP, and must be able to check the
    service without completing an MCP handshake first.

    Returns 200 for `ok` and 503 for `degraded`, so an orchestrator can act
    on the status code alone without parsing the body.
    """
    from starlette.responses import JSONResponse

    from .health import get_health_status

    payload = get_health_status(_get_engine(), TOOL_NAMES)
    return JSONResponse(payload, status_code=200 if payload["status"] == "ok" else 503)


def main() -> None:
    """Entrypoint for the `target-intel-mcp` console script.

    Transport is chosen by env var so the same image serves both a local
    Claude Code install (stdio) and a deployed container (streamable-http
    behind a health check) with no code change:

        MCP_TRANSPORT=stdio            # default - Claude Code / Desktop
        MCP_TRANSPORT=streamable-http  # containerised, /health available
    """
    transport = os.environ.get("MCP_TRANSPORT", "stdio")
    if transport != "stdio":
        server.settings.host = os.environ.get("MCP_HOST", "127.0.0.1")
        server.settings.port = int(os.environ.get("MCP_PORT", "8002"))
    server.run(transport=transport)


if __name__ == "__main__":
    main()
