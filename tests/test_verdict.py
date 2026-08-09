from target_intel.interpretation.prior_model import LiteratureDensity, build_prior
from target_intel.interpretation.verdict import VerdictLabel, interpret_result
from target_intel.literature.corpus import DEMO_CORPUS
from target_intel.literature.relation_extraction import build_target_claim
from target_intel.sdk.models import BindingStrength, ResultRecord


def _her2_prior():
    claim = build_target_claim("P04626", DEMO_CORPUS)
    return build_prior(claim, calibrated_confidence=0.8)


def _sparse_prior():
    claim = build_target_claim("Q9HC97", DEMO_CORPUS)
    return build_prior(claim, calibrated_confidence=0.1)


def _make_result(sequence_name, kd, binding_strength):
    return ResultRecord(
        id="r1", sequence_id="s1", sequence_name=sequence_name,
        target_id="comp-her2-human", target_name="HER2 / ERBB2",
        experiment_id="e1", kd=kd, kd_units="M", binding_strength=binding_strength,
    )


def test_no_binding_result_is_not_flagged():
    prior = _her2_prior()
    result = _make_result("X1", None, BindingStrength.NONE)
    verdict = interpret_result(result, prior)
    assert verdict.label == VerdictLabel.NO_BINDING
    assert verdict.flag_for_review is False


def test_kd_within_known_range_is_consistent_not_flagged():
    prior = _her2_prior()
    # Density now counts quantitative sources, not confidence: the demo
    # HER2 corpus yields exactly two.
    assert prior.density == LiteratureDensity.MODERATE
    result = _make_result("X2", 1.0e-9, BindingStrength.STRONG)  # within 1e-10..1e-8
    verdict = interpret_result(result, prior)
    assert verdict.label == VerdictLabel.CONSISTENT_WITH_LITERATURE
    assert verdict.flag_for_review is False
    assert verdict.citations  # should carry provenance


def test_consistent_rationale_never_claims_a_kd_is_inside_a_range_it_is_outside():
    """The tolerance band means `consistent` covers values just outside the
    core, so the text has to distinguish the two cases. Printing "falls
    within the range (3.16e-13 M-1.34e-09 M)" next to a 1.70e-09 M result
    is a contradiction a scientist catches on the first read, and it costs
    the whole output its credibility."""
    prior = _her2_prior()

    inside = interpret_result(_make_result("IN", 1.0e-9, BindingStrength.STRONG), prior)
    assert inside.label == VerdictLabel.CONSISTENT_WITH_LITERATURE
    assert "falls within the range" in inside.rationale

    # Above the core high, still inside the 5x band -> same label, different claim.
    outside = interpret_result(_make_result("OUT", prior.high_m * 2, BindingStrength.STRONG), prior)
    assert outside.label == VerdictLabel.CONSISTENT_WITH_LITERATURE
    assert outside.flag_for_review is False
    assert "falls within the range" not in outside.rationale
    assert "sits just outside the range" in outside.rationale
    assert "2.0x weaker than its upper bound" in outside.rationale


def test_kd_far_tighter_than_known_range_is_flagged():
    prior = _her2_prior()
    result = _make_result("X3", 1.0e-13, BindingStrength.STRONG)  # 1000x tighter than 1e-10
    verdict = interpret_result(result, prior)
    assert verdict.label == VerdictLabel.OUTSIDE_KNOWN_RANGE_FLAG
    assert verdict.flag_for_review is True


def test_sparse_literature_binding_is_novel_candidate_soft_flag():
    prior = _sparse_prior()
    assert prior.density == LiteratureDensity.SPARSE
    result = ResultRecord(
        id="r2", sequence_id="s2", sequence_name="X4", target_id="comp-gpr35-human",
        target_name="GPR35", experiment_id="e2", kd=1.5e-8, kd_units="M",
        binding_strength=BindingStrength.MODERATE,
    )
    verdict = interpret_result(result, prior)
    assert verdict.label == VerdictLabel.NOVEL_CANDIDATE
    assert verdict.flag_for_review is True  # soft flag: worth a look, not an alarm


def test_weak_binding_below_known_range_is_not_treated_as_artifact():
    prior = _her2_prior()
    result = _make_result("X5", 5.0e-7, BindingStrength.MODERATE)  # 50x weaker than 1e-8 high end
    verdict = interpret_result(result, prior)
    assert verdict.label == VerdictLabel.WEAKER_THAN_TYPICAL
    assert verdict.flag_for_review is False
