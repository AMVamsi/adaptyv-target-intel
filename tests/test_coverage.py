from target_intel.engine import TargetIntelligenceEngine
from target_intel.interpretation.coverage import epitope_diversity_note
from target_intel.interpretation.prior_model import build_prior
from target_intel.literature.corpus import DEMO_CORPUS
from target_intel.literature.relation_extraction import build_target_claim


def test_her2_has_epitope_diversity_note():
    claim = build_target_claim("P04626", DEMO_CORPUS)
    prior = build_prior(claim, calibrated_confidence=0.8)
    assert len(prior.known_epitopes) >= 2  # domain II (pertuzumab) + domain IV (trastuzumab)
    note = epitope_diversity_note("comp-her2-human", "HER2 / ERBB2", prior)
    assert note is not None
    assert "epitope-binning" in note.note or "competition assay" in note.note


def test_gpr35_has_no_epitope_diversity_note():
    claim = build_target_claim("Q9HC97", DEMO_CORPUS)
    prior = build_prior(claim, calibrated_confidence=0.1)
    assert len(prior.known_epitopes) < 2
    note = epitope_diversity_note("comp-gpr35-human", "GPR35", prior)
    assert note is None


def test_portfolio_coverage_report_flags_her2():
    engine = TargetIntelligenceEngine(mock=True, literature_mode="demo")
    notes = engine.portfolio_coverage_report()
    target_ids = {n.target_id for n in notes}
    assert "comp-her2-human" in target_ids
    # Targets with <2 known epitopes shouldn't appear at all.
    assert "comp-gpr35-human" not in target_ids


def test_empty_coverage_result_says_why_rather_than_reading_as_all_clear():
    """A bare `[]` has two readings - "no gaps" and "the check couldn't
    run" - and they lead a scientist to opposite actions. On the real
    snapshot the true answer is the second: epitope recall is the weakest
    part of the extractor, so it finds one epitope across five targets.
    The tool has to type that, not leave it to the caller to infer."""
    from target_intel.mcp_server.server import get_portfolio_coverage_gaps

    report = get_portfolio_coverage_gaps()

    assert report["gaps"] == []
    assert report["analysis_status"] == "insufficient_epitope_data"
    assert report["n_targets_with_any_epitope"] < report["n_targets_assessed"]
    # The note has to be actionable on its own - an agent quoting only this
    # field must not be able to tell anyone their coverage is fine.
    assert "not as 'no coverage gaps exist'" in report["note"]
