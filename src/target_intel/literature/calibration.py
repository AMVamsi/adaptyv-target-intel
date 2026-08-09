"""
Post-hoc confidence calibration for the relation-extraction ensemble's raw
scores, using temperature scaling - the same technique used in the MSc
thesis (expected calibration error under 6% on the real pipeline).

The ensemble in relation_extraction.py produces a raw_confidence in [0,1]
per target that is NOT a calibrated probability - it's just a weighted
vote. Temperature scaling learns a single scalar T from a small labeled
"golden set" (does this target actually have a reliable literature prior,
yes/no) and rescales future raw scores through it, so "0.8 confidence"
means roughly what it says.

Scale note: the thesis calibrated over 5,000+ PubMed articles. The golden
set here has ~10 labeled targets - intentionally small and clearly scoped
as illustrative, not a claim of production-scale statistical power. The
technique and the honesty about calibration error are what transfer.
"""

from __future__ import annotations

import math
from dataclasses import dataclass


def _sigmoid(x: float) -> float:
    return 1.0 / (1.0 + math.exp(-x))


def _logit(p: float, eps: float = 1e-4) -> float:
    p = min(max(p, eps), 1 - eps)
    return math.log(p / (1 - p))


@dataclass
class CalibrationResult:
    temperature: float
    ece: float
    n_examples: int
    bin_report: list[dict]


class TemperatureCalibrator:
    def __init__(self):
        self.temperature: float = 1.0
        self._fitted = False

    def fit(self, raw_scores: list[float], labels: list[int]) -> TemperatureCalibrator:
        """Grid-search the temperature T that minimizes binary cross-entropy
        over the golden set. A full implementation would use gradient
        descent (as in the thesis); grid search is used here for
        transparency and to avoid an extra optimizer dependency for what
        is fundamentally a 1-parameter fit."""
        best_t, best_nll = 1.0, float("inf")
        for t_int in range(5, 400):  # T in [0.05, 4.00]
            t = t_int / 100.0
            nll = 0.0
            for raw, label in zip(raw_scores, labels, strict=True):
                calibrated = _sigmoid(_logit(raw) / t)
                calibrated = min(max(calibrated, 1e-4), 1 - 1e-4)
                nll += -(label * math.log(calibrated) + (1 - label) * math.log(1 - calibrated))
            if nll < best_nll:
                best_nll, best_t = nll, t
        self.temperature = best_t
        self._fitted = True
        return self

    def calibrate(self, raw_score: float) -> float:
        return _sigmoid(_logit(raw_score) / self.temperature)


def expected_calibration_error(
    calibrated_probs: list[float], labels: list[int], n_bins: int = 5
) -> tuple[float, list[dict]]:
    """Standard binned ECE: partition [0,1] into n_bins, compare each bin's
    mean predicted confidence to its actual accuracy, weight by bin size."""
    bins = [{"lo": i / n_bins, "hi": (i + 1) / n_bins, "probs": [], "labels": []} for i in range(n_bins)]
    for p, y in zip(calibrated_probs, labels, strict=True):
        idx = min(int(p * n_bins), n_bins - 1)
        bins[idx]["probs"].append(p)
        bins[idx]["labels"].append(y)

    n = len(labels)
    ece = 0.0
    report = []
    for b in bins:
        if not b["probs"]:
            report.append({"range": f"{b['lo']:.1f}-{b['hi']:.1f}", "n": 0, "avg_conf": None, "accuracy": None})
            continue
        avg_conf = sum(b["probs"]) / len(b["probs"])
        accuracy = sum(b["labels"]) / len(b["labels"])
        weight = len(b["probs"]) / n
        ece += weight * abs(avg_conf - accuracy)
        report.append({
            "range": f"{b['lo']:.1f}-{b['hi']:.1f}",
            "n": len(b["probs"]),
            "avg_conf": round(avg_conf, 3),
            "accuracy": round(accuracy, 3),
        })
    return round(ece, 4), report


def fit_and_evaluate(
    raw_scores: list[float], labels: list[int], n_bins: int = 5
) -> tuple[TemperatureCalibrator, CalibrationResult]:
    calibrator = TemperatureCalibrator().fit(raw_scores, labels)
    calibrated = [calibrator.calibrate(r) for r in raw_scores]
    ece, report = expected_calibration_error(calibrated, labels, n_bins=n_bins)
    return calibrator, CalibrationResult(
        temperature=calibrator.temperature, ece=ece, n_examples=len(labels), bin_report=report
    )
