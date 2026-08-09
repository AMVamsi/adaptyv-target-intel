"""
Fits the temperature calibrator once, from the bundled golden set plus the
4 production demo-corpus targets - shared by the runtime engine and the
`evals/run_evals.py` CLI, so there is exactly one place that defines
"what the golden set is" and "how the calibrator gets fit".
"""

from __future__ import annotations

import json
from pathlib import Path

from .calibration import TemperatureCalibrator, fit_and_evaluate
from .corpus import DEMO_CORPUS, DemoAbstract
from .relation_extraction import TargetLiteratureClaim, build_target_claim

DATA_DIR = Path(__file__).parent / "data"

# Ground truth for the 4 production demo-corpus targets (rich vs sparse
# literature), independently labeled for calibration purposes.
PRODUCTION_LABELS = {
    "P04626": 1,  # HER2
    "P00533": 1,  # EGFR
    "Q9NZQ7": 1,  # PD-L1
    "Q9HC97": 0,  # GPR35
}


def load_golden_scenarios() -> list[tuple[str, int, list[DemoAbstract]]]:
    raw = json.loads((DATA_DIR / "golden_set.json").read_text())
    scenarios = []
    for item in raw:
        abstracts = [
            DemoAbstract(pmid_placeholder=f"{item['target_hint']}_{i}", target_hint=item["target_hint"], text=t)
            for i, t in enumerate(item["abstracts"])
        ]
        scenarios.append((item["target_hint"], item["label"], abstracts))
    return scenarios


def golden_set_labels() -> dict[str, int]:
    """The full {target_hint: label} map the calibrator is fit against -
    the 4 production demo-corpus targets plus every bundled scenario. One
    definition, shared by the engine, the eval harness and the dashboard,
    so a label can't be counted one way in one place and another way
    somewhere else."""
    labels = dict(PRODUCTION_LABELS)
    labels.update({hint: label for hint, label, _ in load_golden_scenarios()})
    return labels


def fit_calibrator_on_golden_set(corpus: str = "snapshot") -> tuple[TemperatureCalibrator, dict]:
    """Returns the fitted calibrator plus a dict of {target_hint: claim} for
    every labeled example, so callers can inspect raw claims if needed.

    `corpus` selects which text the four labeled production targets are
    scored against. This matters more than it looks: a temperature fit on
    one corpus is only valid for scores drawn from that corpus, and real
    PubMed abstracts score far lower on the same ensemble than the tidy
    hand-written ones. Fitting on demo text and then serving real text
    produced a confidence of 0.00 for HER2 - a target with four independent
    quantitative sources and agreeing evidence. The number wasn't wrong so
    much as meaningless, because it was answering a question about a
    different distribution.

    So the fit follows the corpus being served, and the labels stay the
    same because they encode a fact about the targets, not about the text:
    HER2, EGFR and PD-L1 have reliable published binder priors; GPR35 does
    not.
    """
    raw_scores: list[float] = []
    labels: list[int] = []
    claims: dict[str, TargetLiteratureClaim] = {}

    if corpus == "snapshot":
        from .snapshot import load_snapshot

        production_corpus: list = list(load_snapshot())
        if not production_corpus:  # snapshot absent - fall back rather than fail
            production_corpus = list(DEMO_CORPUS)
    else:
        production_corpus = list(DEMO_CORPUS)

    for target_hint, label in PRODUCTION_LABELS.items():
        claim = build_target_claim(target_hint, production_corpus)
        claims[target_hint] = claim
        raw_scores.append(claim.raw_confidence)
        labels.append(label)

    for target_hint, label, abstracts in load_golden_scenarios():
        claim = build_target_claim(target_hint, abstracts)
        claims[target_hint] = claim
        raw_scores.append(claim.raw_confidence)
        labels.append(label)

    calibrator, _result = fit_and_evaluate(raw_scores, labels, n_bins=5)
    return calibrator, claims
