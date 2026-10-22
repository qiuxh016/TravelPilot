import json
from pathlib import Path

import pytest

from tests.evaluation.advisor_unseen_multiturn_eval import _validate_fixture_evidence


def test_route_eval_preflight_rejects_ungrounded_endpoints_before_provider_calls():
    dataset = {
        "cases": [{
            "id": "route-without-place-ids",
            "initial_state": {"saved_places": ["Station", "Museum"]},
            "turns": [{"allowed_tools": ["get_trip_route"]}],
        }],
    }

    with pytest.raises(ValueError, match="grounded known_places IDs"):
        _validate_fixture_evidence(dataset)


def test_route_eval_preflight_accepts_two_grounded_endpoints():
    dataset = {
        "cases": [{
            "id": "route-with-grounded-place-ids",
            "initial_state": {"known_places": [{"id": "from"}, {"id": "to"}]},
            "turns": [{"allowed_tools": ["get_trip_route"]}],
        }],
    }

    _validate_fixture_evidence(dataset)


def test_route_eval_preflight_ignores_cases_without_route_capability():
    dataset = {
        "cases": [{
            "id": "meal-only",
            "initial_state": {},
            "turns": [{"allowed_tools": ["search_places"]}],
        }],
    }

    _validate_fixture_evidence(dataset)


def test_v4_frozen_set_is_rejected_without_spending_more_provider_requests():
    dataset_path = Path(__file__).parent / "cases" / "advisor_multiturn_holdout_v4.json"
    dataset = json.loads(dataset_path.read_text(encoding="utf-8"))

    with pytest.raises(ValueError, match="v4-route-clear-endpoints-08.*known_places IDs"):
        _validate_fixture_evidence(dataset)
