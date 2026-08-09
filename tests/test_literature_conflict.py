"""
The `literature_conflict` verdict.

Tested against constructed priors rather than a fixture experiment. The
behaviour under test is "sources disagree AND the result sits outside the
consensus, so which source you trust changes the answer" - expressing that
through a fixture KD makes the test depend on quartile arithmetic over a
whole corpus, and it then fails for reasons that have nothing to do with
the rule it is meant to pin.

The rule is deliberately narrow. Reserving conflict for the contested zone
matters because on real literature nearly every well-studied target carries
some genuine disagreement - HER2's published affinities run from
avidity-driven sub-picomolar constructs to 151 nM. An earlier version
returned conflict whenever a target had any disagreement at all, which made
it swallow every other verdict and told a scientist nothing.
"""

from __future__ import annotations

from target_intel.interpretation import CalibrationStatus, ExpectedAffinityPrior
from target_intel.interpretation.prior_model import LiteratureDensity
from target_intel.interpretation.verdict import VerdictLabel, interpret_result
from target_intel.literature.relation_extraction import EvidenceLevel
from target_intel.sdk.models import BindingStrength, ResultRecord

# Two sources ~100x apart - the bivalent-IgG vs monovalent-nanobody case.
SOURCE_A = ("PMID_A", 1.0e-8, 5.0e-8)
SOURCE_B = ("PMID_B", 1.0e-10, 1.0e-9)


def _prior(evidence_level: EvidenceLevel, low: float, high: float) -> ExpectedAffinityPrior:
    return ExpectedAffinityPrior(
        target_hint="P11836", low_m=low, high_m=high, caveat_ceiling_m=None,
        density=LiteratureDensity.RICH, calibrated_confidence=0.5,
        known_binders=["antibody"], known_epitopes=[], n_abstracts=2,
        pmids=["PMID_A", "PMID_B"], evidence_level=evidence_level,
        conflicting_source_ranges=(
            [SOURCE_A, SOURCE_B] if evidence_level is EvidenceLevel.CONFLICTING else []
        ),
        calibration_status=CalibrationStatus.CALIBRATED,
        envelope_low_m=1.0e-10, envelope_high_m=5.0e-8, n_quantitative_sources=2,
    )


def _result(kd: float) -> ResultRecord:
    return ResultRecord(
        id="r", sequence_id="s", sequence_name="CDB-01", experiment_id="e",
        target_name="CD20 / MS4A1", kd=kd, binding_strength=BindingStrength.MODERATE,
    )


def test_result_outside_the_consensus_with_disagreeing_sources_is_a_conflict():
    prior = _prior(EvidenceLevel.CONFLICTING, low=1.0e-9, high=5.0e-9)
    verdict = interpret_result(_result(1.0e-6), prior)  # far weaker than the core

    assert verdict.label is VerdictLabel.LITERATURE_CONFLICT
    assert verdict.flag_for_review is True
    assert "disagree" in verdict.rationale.lower()


def test_both_disagreeing_sources_are_cited_not_blended_away():
    prior = _prior(EvidenceLevel.CONFLICTING, low=1.0e-9, high=5.0e-9)
    verdict = interpret_result(_result(1.0e-6), prior)
    assert set(verdict.citations) == {"PMID_A", "PMID_B"}


def test_conflict_never_masquerades_as_consistent_or_artifact():
    """The whole point: don't silently call it an expected hit or an
    artifact when the sources themselves don't agree."""
    prior = _prior(EvidenceLevel.CONFLICTING, low=1.0e-9, high=5.0e-9)
    verdict = interpret_result(_result(1.0e-6), prior)
    assert verdict.label not in (
        VerdictLabel.CONSISTENT_WITH_LITERATURE,
        VerdictLabel.OUTSIDE_KNOWN_RANGE_FLAG,
    )


def test_result_inside_the_consensus_is_not_derailed_by_disagreement():
    """A result squarely inside the consensus core is answerable even though
    outlier sources exist. Flagging those too is what made the verdict
    useless on real data."""
    prior = _prior(EvidenceLevel.CONFLICTING, low=1.0e-9, high=5.0e-9)
    verdict = interpret_result(_result(2.0e-9), prior)

    assert verdict.label is VerdictLabel.CONSISTENT_WITH_LITERATURE
    assert verdict.flag_for_review is False


def test_same_result_without_disagreement_reads_weaker_not_conflicted():
    """Isolates the evidence level as the only cause: identical KD,
    agreeing sources, different label."""
    prior = _prior(EvidenceLevel.VERIFIED, low=1.0e-9, high=5.0e-9)
    verdict = interpret_result(_result(1.0e-6), prior)

    assert verdict.label is VerdictLabel.WEAKER_THAN_TYPICAL


def test_implausibly_tight_result_beats_the_conflict_branch():
    """A value below every reported source is implausible whether or not the
    sources agree; 'they disagree' would be the less useful answer."""
    prior = _prior(EvidenceLevel.CONFLICTING, low=1.0e-9, high=5.0e-9)
    verdict = interpret_result(_result(1.0e-13), prior)  # 1000x below the envelope

    assert verdict.label is VerdictLabel.OUTSIDE_KNOWN_RANGE_FLAG
    assert "tighter" in verdict.rationale
