from target_intel.engine import TargetIntelligenceEngine

HER2_EXP = "019d4a2b-3c5e-7890-a001-000000000001"
EGFR_EXP = "019d4a2b-3c5e-7890-a002-000000000002"
GPR35_EXP = "019d4a2b-3c5e-7890-a003-000000000003"


def test_her2_experiment_flags_only_the_implausible_binder():
    """VHH-01/02 sit inside the published range, VHH-03 doesn't bind, and
    VHH-04 at 50 fM is orders tighter than any precedent. Only the last
    should flag - a check that fires on ordinary results is worthless."""
    engine = TargetIntelligenceEngine(mock=True, literature_mode="demo")
    result = engine.interpret_experiment(HER2_EXP)

    by_seq = {v.rationale.split(":")[0]: v for v in result.verdicts}
    assert by_seq["VHH-01"].label.value == "consistent_with_literature"
    assert by_seq["VHH-02"].label.value == "consistent_with_literature"
    assert by_seq["VHH-03"].label.value == "no_binding"
    assert by_seq["VHH-04"].label.value == "outside_known_range_flag_artifact"

    assert result.flagged_count == 1
    assert by_seq["VHH-04"].flag_for_review is True


def test_egfr_experiment_flags_the_ultra_tight_binder_only():
    engine = TargetIntelligenceEngine(mock=True, literature_mode="demo")
    result = engine.interpret_experiment(EGFR_EXP)
    assert result.flagged_count == 1
    flagged = [v for v in result.verdicts if v.flag_for_review]
    assert len(flagged) == 1
    assert "EGB-11" in flagged[0].rationale
    assert flagged[0].label.value == "outside_known_range_flag_artifact"


def test_gpr35_experiment_flags_as_novel_not_artifact():
    engine = TargetIntelligenceEngine(mock=True, literature_mode="demo")
    result = engine.interpret_experiment(GPR35_EXP)
    assert result.flagged_count == 1
    assert result.verdicts[0].label.value == "novel_candidate"


def test_engine_caches_target_context():
    engine = TargetIntelligenceEngine(mock=True, literature_mode="demo")
    ctx1 = engine.get_target_context("comp-her2-human")
    ctx2 = engine.get_target_context("comp-her2-human")
    assert ctx1 is ctx2  # same object - literature pipeline shouldn't rerun


def test_mcp_tools_are_directly_callable():
    from target_intel.mcp_server.server import (
        get_calibration_report,
        get_target_literature_context,
        interpret_experiment_result,
        list_targets,
    )

    targets = list_targets()
    assert len(targets) == 5

    ctx = get_target_literature_context("comp-her2-human")
    assert ctx["literature_density"] in ("rich", "moderate", "sparse")
    assert "trastuzumab" in ctx["known_binders"]

    # The server module reads LITERATURE_MODE at import; under the real
    # snapshot EGFR's sources genuinely disagree, so assert the shape of the
    # response rather than a count that depends on which corpus is loaded.
    interp = interpret_experiment_result(EGFR_EXP)
    assert interp["target_name"] == "EGFR"
    assert interp["flagged_count"] >= 1
    assert all(v["rationale"] for v in interp["verdicts"])

    calib = get_calibration_report()
    assert 0.0 <= calib["ece"] <= 1.0
    assert calib["n_golden_examples"] >= 10
