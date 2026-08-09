"""
The confidence score has to declare whether it is a probability or just an
ordering signal.

Temperature is fit on the bundled demo corpus. Live PubMed abstracts score
systematically lower on the same ensemble (real phrasing rarely trips the
epitope pattern), so pushing live raw scores through that fit yields a
number that looks like a calibrated probability and isn't one. These tests
pin the guard that says so - a silent regression here would be the exact
kind of quiet overclaim the rest of this codebase is built to avoid.
"""

from __future__ import annotations

from target_intel.interpretation import CalibrationStatus, build_prior
from target_intel.interpretation.verdict import interpret_result
from target_intel.literature.corpus import DEMO_CORPUS
from target_intel.literature.relation_extraction import build_target_claim
from target_intel.sdk.models import BindingStrength, ResultRecord

HER2 = "P04626"


def _result(kd: float) -> ResultRecord:
    return ResultRecord(
        id="r", sequence_id="s", sequence_name="SEQ-1", experiment_id="e",
        target_name="HER2 / ERBB2", kd=kd, binding_strength=BindingStrength.STRONG,
    )


def _prior(status: CalibrationStatus):
    claim = build_target_claim(HER2, DEMO_CORPUS)
    return build_prior(claim, 0.82, status)


def test_demo_mode_priors_are_marked_calibrated():
    prior = _prior(CalibrationStatus.CALIBRATED)
    assert prior.calibration_status is CalibrationStatus.CALIBRATED
    assert prior.confidence_is_trustworthy


def test_live_mode_priors_are_marked_uncalibrated():
    prior = _prior(CalibrationStatus.UNCALIBRATED_LIVE)
    assert not prior.confidence_is_trustworthy


def test_default_is_calibrated_so_the_flag_is_opt_in():
    claim = build_target_claim(HER2, DEMO_CORPUS)
    assert build_prior(claim, 0.82).calibration_status is CalibrationStatus.CALIBRATED


def test_uncalibrated_verdicts_carry_the_caveat_in_the_rationale():
    """A field nobody reads is not a disclosure - it has to be in the prose."""
    verdict = interpret_result(_result(1.2e-9), _prior(CalibrationStatus.UNCALIBRATED_LIVE))
    assert "not as a probability" in verdict.rationale


def test_calibrated_verdicts_are_not_polluted_with_the_caveat():
    verdict = interpret_result(_result(1.2e-9), _prior(CalibrationStatus.CALIBRATED))
    assert "not as a probability" not in verdict.rationale


def test_the_caveat_does_not_change_the_verdict_label():
    """The disclosure annotates the result; it must not alter the science."""
    calibrated = interpret_result(_result(1.2e-9), _prior(CalibrationStatus.CALIBRATED))
    live = interpret_result(_result(1.2e-9), _prior(CalibrationStatus.UNCALIBRATED_LIVE))
    assert calibrated.label == live.label
    assert calibrated.flag_for_review == live.flag_for_review


def test_engine_stamps_status_from_its_literature_mode():
    from target_intel.engine import TargetIntelligenceEngine

    demo = TargetIntelligenceEngine(mock=True, literature_mode="demo")
    assert demo.calibration_status is CalibrationStatus.CALIBRATED
    assert demo.get_target_context("comp-her2-human").prior.confidence_is_trustworthy
    demo.close()

    # Constructed only - no context fetched, so no network call is made.
    live = TargetIntelligenceEngine(mock=True, literature_mode="live")
    assert live.calibration_status is CalibrationStatus.UNCALIBRATED_LIVE
    live.close()
