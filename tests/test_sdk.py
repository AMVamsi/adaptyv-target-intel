import pytest

from target_intel.sdk import AdaptyvClient
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
