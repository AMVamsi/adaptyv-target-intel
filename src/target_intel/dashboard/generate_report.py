"""
Runs the Target Intelligence engine across the whole mock portfolio (every
target, every completed experiment) and writes a single JSON report the
dashboard renders. This is the "surface insights nobody has time to dig
out by hand" artifact from the JD - a batch view across a portfolio,
not a one-off lookup.

Usage:
    python -m target_intel.dashboard.generate_report
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from ..engine import TargetIntelligenceEngine

OUTPUT_PATH = Path(__file__).parent / "portfolio_report.json"


def run() -> dict:
    engine = TargetIntelligenceEngine(mock=True)

    targets_report = []
    for target in engine.client.list_targets():
        ctx = engine.get_target_context(target.target_id)
        targets_report.append({
            "target_id": target.target_id,
            "name": target.name,
            "uniprot_hint": target.uniprot_hint,
            "ncbi": ctx.ncbi,
            "literature_density": ctx.prior.density.value,
            "evidence_level": ctx.prior.evidence_level.value,
            "calibrated_confidence": round(ctx.prior.calibrated_confidence, 4),
            "known_binders": ctx.prior.known_binders,
            "known_epitopes": ctx.prior.known_epitopes,
            "expected_kd_low_m": ctx.prior.low_m,
            "expected_kd_high_m": ctx.prior.high_m,
            "n_abstracts": ctx.prior.n_abstracts,
        })

    # Served through the MCP tool rather than the engine, so the dashboard,
    # the CLI and an agent all describe an empty result the same way. Rendering
    # a bare "no gaps flagged" would tell a reader the portfolio is covered,
    # when what actually happened is that epitope extraction found too little
    # to assess it - see invariant 12.
    from ..mcp_server.server import get_portfolio_coverage_gaps

    coverage = get_portfolio_coverage_gaps()
    coverage_notes = [
        {"target_id": n["target_id"], "target_name": n["target_name"], "note": n["note"]}
        for n in coverage["gaps"]
    ]

    experiments_report = []
    for exp in engine.client.list_experiments():
        entry = {
            "experiment_id": exp.experiment_id,
            "experiment_code": exp.experiment_code,
            "name": exp.name,
            "status": exp.status.value,
            "target_id": exp.experiment_spec.target_id,
            "experiment_type": exp.experiment_spec.experiment_type.value,
            "flagged_count": None,
            "verdicts": [],
        }
        if exp.results_status.value != "none":
            interpretation = engine.interpret_experiment(exp.experiment_id)
            entry["flagged_count"] = interpretation.flagged_count
            entry["target_name"] = interpretation.target.name
            entry["verdicts"] = [
                {
                    "sequence_hint": v.rationale.split(":")[0],
                    "label": v.label.value,
                    "flag_for_review": v.flag_for_review,
                    "confidence": round(v.confidence, 4),
                    "rationale": v.rationale,
                }
                for v in interpretation.verdicts
            ]
        experiments_report.append(entry)

    # Calibration summary, reusing the same golden-set fit the engine itself uses.
    from ..literature.calibration import expected_calibration_error
    from ..literature.golden_calibration import (
        PRODUCTION_LABELS,
        fit_calibrator_on_golden_set,
        load_golden_scenarios,
    )

    labels_by_target = dict(PRODUCTION_LABELS)
    labels_by_target.update({t: label for t, label, _ in load_golden_scenarios()})
    calibrator, claims = fit_calibrator_on_golden_set()
    calibrated = [calibrator.calibrate(c.raw_confidence) for c in claims.values()]
    labels = [labels_by_target[t] for t in claims]
    ece, bin_report = expected_calibration_error(calibrated, labels, n_bins=5)

    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "targets": targets_report,
        "experiments": experiments_report,
        "coverage_notes": coverage_notes,
        "coverage_status": coverage["analysis_status"],
        "coverage_note": coverage["note"],
        "calibration": {
            "temperature": calibrator.temperature,
            "ece": ece,
            "n_examples": len(labels),
            "bin_report": bin_report,
        },
    }
    engine.close()
    return report


if __name__ == "__main__":
    report = run()
    OUTPUT_PATH.write_text(json.dumps(report, indent=2, default=str))
    print(f"Wrote {OUTPUT_PATH}")
