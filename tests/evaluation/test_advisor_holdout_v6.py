import json
from pathlib import Path

from tests.evaluation.advisor_multiturn_contracts import (
    load_holdout_references,
    validate_dataset,
    validate_holdout_separation,
)
from tests.evaluation.advisor_multiturn_live_eval import _validate_tool_arguments
from tests.evaluation.advisor_unseen_multiturn_eval import _validate_fixture_evidence

DATASET_PATH = Path(__file__).parent / "cases" / "advisor_multiturn_holdout_v6.json"


def test_v6_holdout_is_frozen_and_exactly_separated_from_references():
    dataset = json.loads(DATASET_PATH.read_text(encoding="utf-8"))
    report = validate_dataset(dataset)
    references = load_holdout_references(dataset, cases_directory=DATASET_PATH.parent)
    separation = validate_holdout_separation(dataset, references)

    assert report["case_count"] == 20
    assert report["turn_count"] == 40
    assert separation["case_id_overlap_count"] == 0
    assert separation["exact_normalized_prompt_overlap_count"] == 0


def test_v6_route_and_schedule_fixtures_are_grounded_before_live_scoring():
    dataset = json.loads(DATASET_PATH.read_text(encoding="utf-8"))
    _validate_fixture_evidence(dataset)
    for case in dataset["cases"]:
        context = case["initial_state"]
        turns = case["turns"]
        if any("get_distance_matrix" in turn["allowed_tools"] for turn in turns):
            places = context.get("saved_place_details")
            assert isinstance(places, list) and len(places) == context["saved_places"]
            points = [[place["latitude"], place["longitude"]] for place in places]
            valid, errors = _validate_tool_arguments(
                "get_distance_matrix", {"points": points}, context,
            )
            assert valid, (case["id"], errors)
        if any("get_trip_route" in turn["allowed_tools"] for turn in turns):
            places = context["known_places"]
            ids = [place["id"] for place in places]
            user_text = " ".join(turn["user"] for turn in turns)
            origin_id = context.get("selected_origin_id") or next(
                place["id"] for place in places
                if place["name"] in user_text and "站" in place["name"]
            )
            destination_id = context.get("selected_destination_id") or next(
                place["id"] for place in places
                if place["name"] in user_text and place["id"] != origin_id
            )
            valid, errors = _validate_tool_arguments("get_trip_route", {
                "origin_place_id": origin_id,
                "destination_place_id": destination_id,
                "mode": "DRIVING",
            }, context)
            assert len(set(ids)) == len(ids)
            assert valid, (case["id"], errors)
