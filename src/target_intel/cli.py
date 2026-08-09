"""
Command-line interface: `target-intel <command>`.

Everything here runs in mock mode by default (no API key needed).
"""

from __future__ import annotations

import argparse
import json
import sys
import textwrap

from .engine import TargetIntelligenceEngine


def _print_json(obj) -> None:
    if hasattr(obj, "model_dump"):
        obj = obj.model_dump()
    print(json.dumps(obj, indent=2, default=str))


def _wrap(text: str, width: int = 76, indent: str = "    ") -> str:
    return "\n".join(textwrap.wrap(text, width=width, initial_indent=indent, subsequent_indent=indent))


def cmd_list_targets(args, engine: TargetIntelligenceEngine) -> None:
    targets = engine.client.list_targets(search=args.search)
    _print_json([t.model_dump() for t in targets])


def cmd_list_experiments(args, engine: TargetIntelligenceEngine) -> None:
    experiments = engine.client.list_experiments(status=args.status)
    _print_json([
        {"experiment_id": e.experiment_id, "name": e.name, "status": e.status.value,
         "results_status": e.results_status.value, "target_id": e.experiment_spec.target_id}
        for e in experiments
    ])


def cmd_context(args, engine: TargetIntelligenceEngine) -> None:
    ctx = engine.get_target_context(args.target_id)
    _print_json({
        "target": ctx.target.model_dump(),
        "ncbi": ctx.ncbi,
        "literature_density": ctx.prior.density.value,
        "evidence_level": ctx.prior.evidence_level.value,
        "calibrated_confidence": round(ctx.prior.calibrated_confidence, 4),
        "calibration_status": ctx.prior.calibration_status.value,
        "known_binders": ctx.prior.known_binders,
        "known_epitopes": ctx.prior.known_epitopes,
        "expected_kd_range_m": [ctx.prior.low_m, ctx.prior.high_m],
        "caveat_ceiling_m": ctx.prior.caveat_ceiling_m,
        "n_abstracts_reviewed": ctx.prior.n_abstracts,
    })


def cmd_interpret(args, engine: TargetIntelligenceEngine) -> None:
    result = engine.interpret_experiment(args.experiment_id)

    if args.json:
        _print_json({
            "experiment_id": result.experiment_id,
            "target_name": result.target.name,
            "literature_density": result.prior.density.value,
            "evidence_level": result.prior.evidence_level.value,
            "calibration_status": result.prior.calibration_status.value,
            "flagged_count": result.flagged_count,
            "verdicts": [
                {"label": v.label.value, "flag_for_review": v.flag_for_review,
                 "confidence": round(v.confidence, 4), "rationale": v.rationale, "citations": v.citations}
                for v in result.verdicts
            ],
        })
        return

    # Human-readable by default. The point of this tool is that a scientist
    # reads the verdict and acts on it - a wall of JSON buries the one line
    # that actually needed attention.
    prior = result.prior
    print(f"\n{result.target.name}  ({result.experiment_id})")
    print(
        f"  literature: {prior.density.value} | evidence: {prior.evidence_level.value} "
        f"| confidence: {prior.calibrated_confidence:.2f} ({prior.calibration_status.value})"
    )
    print(f"  {result.flagged_count} of {len(result.verdicts)} result(s) flagged for review\n")

    for v in result.verdicts:
        marker = "  [FLAG]" if v.flag_for_review else ""
        name = v.rationale.split(":")[0]
        print(f"{name}  {v.label.value}{marker}")
        print(_wrap(v.rationale.split(":", 1)[1].strip()))
        if v.citations:
            print(f"    citations: {', '.join(v.citations)}")
        print()


def cmd_kg(args, engine: TargetIntelligenceEngine) -> None:
    ctx = engine.get_target_context(args.target_id)
    print(ctx.knowledge_graph.to_cypher())


def cmd_coverage(args, engine: TargetIntelligenceEngine) -> None:
    from .mcp_server.server import get_portfolio_coverage_gaps

    # Served from the MCP tool rather than the engine directly, so the CLI
    # and an agent get the same answer - including the same explanation of
    # an empty result. Printing a bare `[]` here reads as "no gaps, all
    # clear", which is the opposite of what an empty list means on the
    # shipped snapshot.
    report = get_portfolio_coverage_gaps()
    if args.json:
        _print_json(report)
        return

    if report["gaps"]:
        for gap in report["gaps"]:
            print(f"\n{gap['target_name']}  ({', '.join(gap['known_epitopes'])})")
            print(_wrap(gap["note"]))
        print()
        return

    print(f"\nPortfolio epitope-coverage gaps: none reportable  [{report['analysis_status']}]")
    print(_wrap(report["note"]))
    print()


def cmd_score(args, engine: TargetIntelligenceEngine) -> None:
    from .literature.gold import score_report

    report = score_report()
    if args.json:
        _print_json(report)
        return

    print(
        f"\nExact-span entity scoring  ({report['gold_sentences']} sentences, "
        f"{report['gold_spans']} labeled spans)"
    )
    print(f"  evaluated types: {', '.join(report['types_evaluated'])}")
    print(f"  not evaluated:   {', '.join(report['types_not_evaluated'])}\n")

    m = report["overall"]["micro"]
    print(f"  OVERALL micro   P={m['precision']:.3f}  R={m['recall']:.3f}  F1={m['f1']:.3f}  (n={m['support']})")
    for split, data in report["by_split"].items():
        s = data["micro"]
        print(
            f"    {split:20} P={s['precision']:.3f}  R={s['recall']:.3f}  "
            f"F1={s['f1']:.3f}  (n={s['support']}, {data['n_sentences']} sentences)"
        )

    print("\n  by type:")
    for name, s in report["overall"]["per_type"].items():
        print(f"    {name:16} P={s['precision']:.3f}  R={s['recall']:.3f}  F1={s['f1']:.3f}  (n={s['support']})")

    print(
        "\n  Read demo_corpus as an in-domain ceiling: the gazetteer was written\n"
        "  against that text, so it measures consistency, not generalization.\n"
        "  heldout_realistic is the honest estimate."
    )


def cmd_eval(args, engine: TargetIntelligenceEngine) -> None:
    from .evals import format_report, run, run_deterministic_guards

    report = run()
    failures = run_deterministic_guards(report)
    _print_json(report) if args.json else print(format_report(report, failures))
    if failures:
        print("\nGUARD FAILURES:", failures, file=sys.stderr)
        sys.exit(1)


def main() -> None:
    parser = argparse.ArgumentParser(prog="target-intel")
    parser.add_argument(
        "--live", action="store_true",
        help="Use the real Foundry API instead of mock fixtures (needs FOUNDRY_API_TOKEN)",
    )
    parser.add_argument(
        "--live-literature", action="store_true",
        help=(
            "Query PubMed now instead of using the bundled real-literature "
            "snapshot. Confidence scores are reported as uncalibrated in this "
            "mode - the temperature is fit per corpus, and live text changes "
            "per query so it can't be fit ahead of time."
        ),
    )
    parser.add_argument(
        "--json", action="store_true",
        help="Emit raw JSON instead of the human-readable summary (for piping into jq/CI)",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("list-targets", help="List targets in the catalog")
    p.add_argument("--search", default=None)
    p.set_defaults(func=cmd_list_targets)

    p = sub.add_parser("list-experiments", help="List experiments")
    p.add_argument("--status", default=None)
    p.set_defaults(func=cmd_list_experiments)

    p = sub.add_parser("context", help="Get the literature-grounded context for a target")
    p.add_argument("target_id")
    p.set_defaults(func=cmd_context)

    p = sub.add_parser("interpret", help="Interpret all results in an experiment")
    p.add_argument("experiment_id")
    p.set_defaults(func=cmd_interpret)

    p = sub.add_parser("kg", help="Export a target's knowledge graph as Cypher")
    p.add_argument("target_id")
    p.set_defaults(func=cmd_kg)

    p = sub.add_parser("coverage", help="Portfolio-level epitope-diversity gap analysis")
    p.set_defaults(func=cmd_coverage)

    p = sub.add_parser("score", help="Exact-span entity F1 against the labeled gold set")
    p.set_defaults(func=cmd_score)

    p = sub.add_parser("eval", help="Run the offline calibration/guard eval suite")
    p.set_defaults(func=cmd_eval)

    args = parser.parse_args()
    engine = TargetIntelligenceEngine(
        mock=not args.live,
        literature_mode="live" if args.live_literature else "snapshot",
    )
    try:
        args.func(args, engine)
    finally:
        engine.close()


if __name__ == "__main__":
    main()
