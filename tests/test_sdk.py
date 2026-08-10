import pytest

from target_intel.sdk import AdaptyvClient
from target_intel.sdk.models import Experiment
from target_intel.sdk.transport import FoundryAPIError


def test_list_targets_returns_all_four():
    client = AdaptyvClient(mock=True)
    targets = client.list_targets()
    assert len(targets) == 5
    assert {t.target_id for t in targets} == {
        "comp-her2-human", "comp-egfr-human", "comp-pdl1-human", "comp-gpr35-human", "comp-cd20-human",
    }


def test_search_filters_targets():
    client = AdaptyvClient(mock=True)
    targets = client.list_targets(search="EGFR")
    assert len(targets) == 1
    assert targets[0].name == "EGFR"


def test_get_target_not_found_raises():
    client = AdaptyvClient(mock=True)
    try:
        client.get_target("does-not-exist")
        pytest.fail("expected FoundryAPIError")
    except FoundryAPIError as e:
        assert e.status_code == 404


def test_get_experiment_and_results_shapes():
    client = AdaptyvClient(mock=True)
    exp = client.get_experiment("019d4a2b-3c5e-7890-a001-000000000001")
    assert exp.status.value == "done"
    assert exp.experiment_spec.target_id == "comp-her2-human"

    results = client.get_results(exp.experiment_id)
    assert len(results) == 4
    assert {r.sequence_name for r in results} == {"VHH-01", "VHH-02", "VHH-03", "VHH-04"}


def test_list_experiments_filters_by_status():
    client = AdaptyvClient(mock=True)
    done = client.list_experiments(status="done")
    assert all(e.status.value == "done" for e in done)
    in_prod = client.list_experiments(status="in_production")
    assert len(in_prod) == 1
    assert in_prod[0].experiment_spec.target_id == "comp-pdl1-human"


def test_live_mode_requires_token():
    try:
        AdaptyvClient(mock=False)
        pytest.fail("expected ValueError without a token")
    except ValueError:
        pass


def test_models_parse_a_real_foundry_response_not_just_the_fixtures():
    """The live path's one honest test.

    `mock=True/False` claims to return identically-shaped objects, but until
    a real response was captured only the mock side had ever been exercised.
    It did not parse: the live API returns `id`/`code` where the fixtures say
    `experiment_id`/`experiment_code`, and nests costs under
    `costs.breakdown` where the fixtures are flat. A typed client that only
    parses its own fixtures is a typed client for the fixtures.
    """
    import json
    from pathlib import Path

    raw = json.loads((Path(__file__).parent / "data" / "foundry_experiment_response.json").read_text())
    exp = Experiment.model_validate(raw)

    # Live key names must map onto the canonical field names.
    assert exp.experiment_id == "019d4a2b-3c5e-7890-abcd-1234567890ab"
    assert exp.experiment_code == "ABS-001-042"
    assert exp.experiment_spec.method == "bli"
    assert exp.experiment_spec.n_replicates == 1

    # Nested cost object must flatten, and the parts must still sum.
    assert exp.costs is not None
    assert exp.costs.assay_cost_cents == 198_000
    assert exp.costs.material_cost_cents == 34_980
    assert exp.costs.total_cents == 232_980
    assert exp.costs.assay_cost_cents + exp.costs.material_cost_cents == exp.costs.total_cents
    assert exp.costs.estimate is True
    assert exp.costs.pricing_version == "v1_2026-01-20"


def test_fixture_shape_still_parses_after_adding_live_aliases():
    """Adding aliases must not break the mock transport - that would trade
    one broken half of the parity claim for the other."""
    client = AdaptyvClient(mock=True)
    experiments = client.list_experiments()
    assert experiments
    assert all(e.experiment_id for e in experiments)
    assert any(e.costs and e.costs.total_cents for e in experiments)
    client.close()


def test_scope_boundary_matches_the_api_s_own_data_model():
    """Why literature grounding applies to two of the five assay types.

    Adaptyv's API takes five `experiment_type` values. The two that measure
    binding *against a target* — screening and affinity — carry a
    `target_id`. The three that measure a protein on its own — expression,
    thermostability, fluorescence — have no target field at all, because
    there is nothing to bind to.

    That is exactly where this project stops: no target means no target
    literature, so there is no prior to compare against. The boundary was
    not chosen for convenience, it is the one the API itself encodes, and
    `engine.interpret_experiment` raises naming those three types. These
    specs are the real request bodies from the public API reference.
    """
    from target_intel.sdk.models import ExperimentSpec

    target_bound = ["screening", "affinity"]
    standalone = ["expression", "thermostability", "fluorescence"]

    for kind in target_bound:
        spec = ExperimentSpec.model_validate(
            {"experiment_type": kind, "target_id": "comp-her2-human",
             "sequences": {"VHH-01": "EVQLVESGGG"}, "n_replicates": 1}
        )
        assert spec.target_id is not None, f"{kind} must carry a target"

    for kind in standalone:
        spec = ExperimentSpec.model_validate(
            {"experiment_type": kind, "sequences": {"VHH-01": "EVQLVESGGG"}, "n_replicates": 1}
        )
        assert spec.target_id is None, f"{kind} has no target in the real API"


def test_targetless_experiment_is_refused_with_a_reason_not_a_crash():
    """A thermostability run reaching this engine is a caller mistake, and
    the error has to say which mistake - otherwise it reads as a bug in the
    literature layer rather than an out-of-scope request."""
    import pytest as _pytest

    from target_intel.engine import TargetIntelligenceEngine

    engine = TargetIntelligenceEngine(mock=True, literature_mode="demo")
    engine.client.get_experiment = lambda _id: type(
        "E", (), {"experiment_spec": type("S", (), {"target_id": None})()}
    )()
    with _pytest.raises(ValueError, match="thermostability/fluorescence/expression"):
        engine.interpret_experiment("whatever")
    engine.close()
