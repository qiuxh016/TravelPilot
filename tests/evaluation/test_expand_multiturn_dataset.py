import copy

import pytest

from tests.evaluation.advisor_multiturn_contracts import validate_dataset
from tests.evaluation.expand_multiturn_dataset import build_dataset


def test_v3_generator_reproducibly_builds_sixty_balanced_scenarios():
    first = build_dataset()
    second = build_dataset()

    assert first == second
    report = validate_dataset(first)
    assert report["evaluation_id"] == "advisor_multiturn_v3"
    assert report["case_count"] == 60
    assert report["turn_count"] == 129
    assert set(report["family_counts"].values()) == {6}


@pytest.mark.parametrize("action", ["UPDATE_CONFIRMED_MEMORY", "DELETE_CONFIRMED_MEMORY"])
def test_v3_contract_rejects_memory_write_without_confirmation(action):
    dataset = copy.deepcopy(build_dataset())
    target = next(
        turn
        for case in dataset["cases"]
        for turn in case["turns"]
        if turn["expected_action"] == action
    )
    target["confirmation_received"] = False

    with pytest.raises(ValueError, match="explicit user confirmation"):
        validate_dataset(dataset)
