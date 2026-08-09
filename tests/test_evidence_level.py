from target_intel.literature.corpus import DEMO_CORPUS
from target_intel.literature.relation_extraction import EvidenceLevel, build_target_claim


def test_her2_two_agreeing_sources_is_verified():
    claim = build_target_claim("P04626", DEMO_CORPUS)
    assert claim.evidence_level == EvidenceLevel.VERIFIED
    assert claim.conflicting_source_ranges == []


def test_egfr_two_agreeing_sources_is_verified():
    claim = build_target_claim("P00533", DEMO_CORPUS)
    assert claim.evidence_level == EvidenceLevel.VERIFIED


def test_pdl1_two_agreeing_sources_is_verified():
    claim = build_target_claim("Q9NZQ7", DEMO_CORPUS)
    assert claim.evidence_level == EvidenceLevel.VERIFIED


def test_gpr35_no_quantitative_source_is_missing():
    claim = build_target_claim("Q9HC97", DEMO_CORPUS)
    assert claim.evidence_level == EvidenceLevel.MISSING


def test_cd20_disagreeing_sources_is_conflicting():
    claim = build_target_claim("P11836", DEMO_CORPUS)
    assert claim.evidence_level == EvidenceLevel.CONFLICTING
    assert len(claim.conflicting_source_ranges) == 2
    pmids = {pmid for pmid, _, _ in claim.conflicting_source_ranges}
    assert pmids == {"DEMO0010", "DEMO0011"}


def test_cd20_has_moderate_or_better_density_despite_conflicting_evidence():
    # The whole point of the second axis: there's enough literature to not
    # be dismissed as "sparse" (two independent sources exist) even though
    # that literature doesn't agree with itself (low evidence-level trust).
    from target_intel.literature.golden_calibration import fit_calibrator_on_golden_set

    claim = build_target_claim("P11836", DEMO_CORPUS)
    calibrator, _ = fit_calibrator_on_golden_set()
    calibrated = calibrator.calibrate(claim.raw_confidence)
    assert calibrated >= 0.3  # not classified "sparse" - literature does exist
    assert claim.evidence_level == EvidenceLevel.CONFLICTING  # but still conflicting


def test_single_source_target_is_claimed():
    from target_intel.literature.corpus import DemoAbstract

    single = [DemoAbstract("X1", "SYNTH_SINGLE", "Trastuzumab binds domain IV with an affinity of 2 nanomolar.")]
    claim = build_target_claim("SYNTH_SINGLE", single)
    assert claim.evidence_level == EvidenceLevel.CLAIMED


def test_no_abstracts_is_missing():
    claim = build_target_claim("NOT_A_REAL_TARGET", DEMO_CORPUS)
    assert claim.evidence_level == EvidenceLevel.MISSING


def test_two_agreeing_synthetic_sources_is_verified():
    from target_intel.literature.corpus import DemoAbstract

    abstracts = [
        DemoAbstract("X1", "SYNTH_AGREE", "Trastuzumab binds domain IV with an affinity of 2 nanomolar."),
        DemoAbstract("X2", "SYNTH_AGREE", "A nanobody against domain IV binds with an affinity of 3 nanomolar."),
    ]
    claim = build_target_claim("SYNTH_AGREE", abstracts)
    assert claim.evidence_level == EvidenceLevel.VERIFIED
