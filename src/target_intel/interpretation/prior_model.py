"""
Turns a target's literature claim (from relation_extraction.py) plus its
calibrated confidence (from calibration.py) into an ExpectedAffinityPrior -
the thing the verdict engine actually compares a fresh experimental result
against.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from ..literature.relation_extraction import EvidenceLevel, TargetLiteratureClaim, typical_range_m


class LiteratureDensity(str, Enum):
    RICH = "rich"
    MODERATE = "moderate"
    SPARSE = "sparse"


class CalibrationStatus(str, Enum):
    """Whether the confidence attached to this prior is one the calibrator
    was actually fit to interpret.

    The temperature is fit on whichever corpus is being served - see
    `fit_calibrator_on_golden_set`. Demo abstracts are written in tidy
    prose; real PubMed abstracts are not, and they score systematically
    lower on the same ensemble, because the epitope pattern rarely fires on
    real phrasing. Pushing raw scores through a temperature fit on a
    different distribution produces a number that looks like a probability
    and isn't one: fitting on demo text and serving snapshot text reported
    HER2 at 0.00, a target with agreeing quantitative sources.

    `snapshot` and `demo` are each fit ahead of time and so are calibrated.
    Live mode can't be - its text changes per query - so rather than
    silently emit the number, it labels itself. A confidence a system can't
    stand behind should say so, not round itself off.
    """

    CALIBRATED = "calibrated"                    # snapshot/demo: fit on the corpus being served
    UNCALIBRATED_LIVE = "uncalibrated_live"      # live mode: out of any fit distribution




@dataclass
class ExpectedAffinityPrior:
    target_hint: str
    low_m: float | None
    high_m: float | None
    caveat_ceiling_m: float | None
    density: LiteratureDensity
    calibrated_confidence: float
    known_binders: list[str]
    known_epitopes: list[str]
    n_abstracts: int
    pmids: list[str]
    evidence_level: EvidenceLevel
    conflicting_source_ranges: list[tuple[str, float, float]]
    calibration_status: CalibrationStatus = CalibrationStatus.CALIBRATED
    # Full min/max span of everything extracted. Reported for transparency,
    # never used for a verdict: on real literature it is too wide to mean
    # anything. `low_m`/`high_m` above carry the robust core.
    envelope_low_m: float | None = None
    envelope_high_m: float | None = None
    n_quantitative_sources: int = 0

    @property
    def confidence_is_trustworthy(self) -> bool:
        return self.calibration_status is CalibrationStatus.CALIBRATED


def build_prior(
    claim: TargetLiteratureClaim,
    calibrated_confidence: float,
    calibration_status: CalibrationStatus = CalibrationStatus.CALIBRATED,
) -> ExpectedAffinityPrior:
    # Density counts sources that actually yielded quantitative evidence,
    # rather than reading it off the calibrated confidence. Confidence is a
    # calibrated score whose temperature was fit on one corpus; real PubMed
    # scores sit far lower on the same ensemble, so deriving density from it
    # labelled every real target "sparse" - including HER2, with 19 papers
    # and 4 extracted affinity sources behind it. Counting evidence is both
    # more honest and independent of whether the calibrator is in-domain.
    n_sources = claim.n_tier1_hits + claim.n_tier2_hits
    if n_sources == 0:
        density = LiteratureDensity.SPARSE
    elif n_sources < 3:
        density = LiteratureDensity.MODERATE
    else:
        density = LiteratureDensity.RICH

    typical_low, typical_high = typical_range_m(claim.affinity_values_m)

    return ExpectedAffinityPrior(
        target_hint=claim.target_hint,
        low_m=typical_low,
        high_m=typical_high,
        envelope_low_m=claim.affinity_low_m,
        envelope_high_m=claim.affinity_high_m,
        n_quantitative_sources=n_sources,
        caveat_ceiling_m=claim.caveat_ceiling_m,
        density=density,
        calibrated_confidence=calibrated_confidence,
        known_binders=sorted(claim.known_binders),
        known_epitopes=sorted(claim.known_epitopes),
        n_abstracts=claim.n_abstracts,
        pmids=sorted({p.pmid for p in claim.provenance}),
        evidence_level=claim.evidence_level,
        conflicting_source_ranges=claim.conflicting_source_ranges,
        calibration_status=calibration_status,
    )
