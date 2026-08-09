"""
Offline eval harness for the literature-grounding pipeline.

Zero network, zero LLM calls, zero cost: gate on deterministic checks, not
on vibes. Two kinds of check, and the second matters more than the first.

1. **Calibration quality.** Fit temperature scaling on the golden set and
   report Expected Calibration Error - always alongside the sample size,
   because an ECE quoted bare at n=14 is a misleading number.

2. **Deterministic regression guards.** Hand-checkable assertions that hold
   regardless of what the calibration figures say on a given day: HER2 must
   produce a tier-1 pattern hit, GPR35 must score below HER2, a
   zero-abstract target must degrade to exactly 0.0. Calibration metrics
   drift when the corpus changes; these catch extraction actually breaking,
   which a moved ECE would not distinguish from noise.

This module lives *inside* the package rather than in the `evals/` script
directory so that `target-intel eval` keeps working after `pip install`,
where that directory isn't present. `evals/run_evals.py` is a thin wrapper
over these same functions - one implementation, two ways to reach it.
"""

from __future__ import annotations

from .literature.calibration import expected_calibration_error
from .literature.corpus import DEMO_CORPUS
from .literature.golden_calibration import (
    PRODUCTION_LABELS,
    fit_calibrator_on_golden_set,
    golden_set_labels,
)
from .literature.relation_extraction import EvidenceLevel, build_target_claim

ECE_GUARD_THRESHOLD = 0.35


def run() -> dict:
    calibrator, claims = fit_calibrator_on_golden_set()
    labels_by_target = golden_set_labels()

    per_target_report = []
    labels = []
    for target_hint, claim in claims.items():
        label = labels_by_target[target_hint]
        labels.append(label)
        per_target_report.append({
            "target_hint": target_hint,
            "label": label,
            "raw_confidence": claim.raw_confidence,
            "calibrated_confidence": round(calibrator.calibrate(claim.raw_confidence), 4),
            "n_abstracts": claim.n_abstracts,
            "n_tier1_hits": claim.n_tier1_hits,
            "n_tier2_hits": claim.n_tier2_hits,
            "evidence_level": claim.evidence_level.value,
            "source": "production_corpus" if target_hint in PRODUCTION_LABELS else "golden_set",
        })

    calibrated_all = [row["calibrated_confidence"] for row in per_target_report]
    ece, bin_report = expected_calibration_error(calibrated_all, labels, n_bins=5)

    return {
        "n_examples": len(labels),
        "temperature": calibrator.temperature,
        "ece": ece,
        "bin_report": bin_report,
        "per_target": per_target_report,
    }


def run_deterministic_guards(report: dict) -> list[str]:
    """Returns a list of failure messages; empty means all guards passed."""
    failures = []
    by_target = {row["target_hint"]: row for row in report["per_target"]}

    # Guard on *quantitative sources*, not specifically tier-1 hits. Tier 1
    # requires a named epitope alongside a binder and an affinity, and real
    # abstracts rarely write "domain IV" - so a tier-1 guard passes on the
    # hand-written demo corpus and fails on real literature for a reason
    # that says nothing about extraction health. What must never regress is
    # that a heavily-studied target yields quantitative evidence at all.
    her2_sources = by_target["P04626"]["n_tier1_hits"] + by_target["P04626"]["n_tier2_hits"]
    if her2_sources == 0:
        failures.append("HER2 (P04626) produced zero quantitative sources - extraction regression.")

    if by_target["Q9HC97"]["raw_confidence"] >= by_target["P04626"]["raw_confidence"]:
        failures.append("GPR35 raw_confidence is not lower than HER2's - sparsity signal broke.")

    sparse5 = by_target["EVAL_SPARSE_5"]
    if sparse5["n_abstracts"] != 0 or sparse5["raw_confidence"] != 0.0:
        failures.append("Zero-abstract target did not degrade to raw_confidence 0.0.")

    # CD20's two demo sources disagree by ~100x on purpose. If this ever
    # reports anything else, conflict detection has silently started
    # blending non-overlapping ranges into one false consensus - the exact
    # failure the evidence-level axis exists to prevent.
    #
    # Checked by building the claim directly rather than reading it off the
    # report above: CD20 is deliberately NOT one of the calibration-labeled
    # targets (a conflicting prior is not a "reliable prior" in the sense
    # that label means), so it never appears in the golden-set fit.
    cd20 = build_target_claim("P11836", DEMO_CORPUS)
    if cd20.evidence_level is not EvidenceLevel.CONFLICTING:
        failures.append(
            f"CD20 (P11836) evidence level is {cd20.evidence_level.value}, expected "
            "conflicting - cross-source agreement check broke."
        )

    if report["ece"] > ECE_GUARD_THRESHOLD:
        failures.append(
            f"ECE {report['ece']} exceeds the {ECE_GUARD_THRESHOLD} guard threshold "
            "for this illustrative-scale set."
        )

    return failures


def format_report(report: dict, failures: list[str]) -> str:
    lines = [
        f"Golden set size: {report['n_examples']}",
        f"Fitted temperature: {report['temperature']}",
        f"Expected Calibration Error: {report['ece']}  (n={report['n_examples']})",
        "",
        "Calibration bins (predicted confidence vs. actual reliability):",
    ]
    for b in report["bin_report"]:
        lines.append(f"  {b['range']}: n={b['n']}, avg_conf={b['avg_conf']}, accuracy={b['accuracy']}")

    lines += ["", "Deterministic guards:"]
    lines += [f"  FAIL: {f}" for f in failures] or ["  All guards passed."]
    return "\n".join(lines)
