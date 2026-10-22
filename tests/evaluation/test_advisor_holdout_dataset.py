import asyncio
import copy
import json
from pathlib import Path

import pytest

from tests.evaluation import advisor_multiturn_live_eval as live_eval
from tests.evaluation.advisor_multiturn_contracts import (
    load_holdout_references,
    validate_dataset,
    validate_holdout_separation,
)

CASES = Path(__file__).parent / "cases"
HOLDOUT_PATH = CASES / "advisor_multiturn_holdout_v1.json"
GROUNDED_HOLDOUT_V2_PATH = CASES / "advisor_multiturn_holdout_v2.json"
GROUNDED_HOLDOUT_V5_PATH = CASES / "advisor_multiturn_holdout_v5.json"


def _holdout():
    return json.loads(HOLDOUT_PATH.read_text(encoding="utf-8"))


def test_holdout_is_frozen_action_contract_with_case_and_prompt_separation():
    dataset = _holdout()
    validated = validate_dataset(dataset)
    references = load_holdout_references(dataset, cases_directory=CASES)
    separation = validate_holdout_separation(dataset, references)

    assert validated["case_count"] == 10
    assert validated["turn_count"] == 20
    assert len(validated["family_counts"]) >= 7
    assert separation["case_id_overlap_count"] == 0
    assert separation["exact_normalized_prompt_overlap_count"] == 0
    assert set(separation["reference_dataset_sha256"]) == set(
        dataset["holdout_protocol"]["reference_datasets"]
    )
    assert "semantic independence is not established" in separation["interpretation"]


def test_grounded_holdout_v2_is_frozen_disjoint_and_supplies_tool_context():
    dataset = json.loads(GROUNDED_HOLDOUT_V2_PATH.read_text(encoding="utf-8"))
    validated = validate_dataset(dataset)
    references = load_holdout_references(dataset, cases_directory=CASES)
    separation = validate_holdout_separation(dataset, references)
    cases = {case["id"]: case for case in dataset["cases"]}

    assert dataset["dataset_id"] == "advisor_multiturn_holdout_v2"
    assert dataset["status"] == "FROZEN_NOT_SCORED"
    assert validated["case_count"] == 5
    assert validated["turn_count"] == 10
    assert validated["case_file_sha256"] == (
        "7671b0f0db74624eeb37381342767eba56d366f0d2e4e769a4456ee0babf4433"
    )
    assert separation["exact_normalized_prompt_overlap_count"] == 0
    schedule = cases["holdout-v2-schedule-with-coordinates-12"]["initial_state"]
    route = cases["holdout-v2-route-selected-stops-13"]["initial_state"]
    assert len(schedule["saved_place_details"]) == schedule["saved_places"]
    assert {place["id"] for place in route["known_places"]} >= {
        route["selected_origin_id"],
        "destination-panda",
    }


def test_grounded_holdout_v5_is_frozen_disjoint_and_all_route_evidence_is_complete():
    from tests.evaluation.advisor_unseen_multiturn_eval import _validate_fixture_evidence

    dataset = json.loads(GROUNDED_HOLDOUT_V5_PATH.read_text(encoding="utf-8"))
    validated = validate_dataset(dataset)
    references = load_holdout_references(dataset, cases_directory=CASES)
    separation = validate_holdout_separation(dataset, references)

    assert dataset["dataset_id"] == "advisor_multiturn_holdout_v5"
    assert dataset["status"] == "FROZEN_NOT_LIVE_SCORED"
    assert validated["case_count"] == 20
    assert validated["turn_count"] == 40
    assert separation["exact_normalized_prompt_overlap_count"] == 0
    assert separation["case_id_overlap_count"] == 0
    _validate_fixture_evidence(dataset)

    for case in dataset["cases"]:
        if any("get_trip_route" in turn["allowed_tools"] for turn in case["turns"]):
            state = case["initial_state"]
            places = {place["id"]: place for place in state["known_places"]}
            assert len(places) >= 2
            for key in ("selected_origin_id", "selected_destination_id"):
                if key in state:
                    assert state[key] in places


def test_grounded_holdout_v2_runs_through_action_and_tool_schema_checks(monkeypatch):
    from types import SimpleNamespace

    from travelpilot_core.agents.loop import AgentAction

    monkeypatch.setattr(live_eval.settings, "llm_api_key", "test-key")
    monkeypatch.setattr(live_eval.settings, "llm_model", "test-model")
    monkeypatch.setattr(live_eval.settings, "llm_base_url", "https://example.test/v1")

    async def fake_decision(observation, schemas, **kwargs):
        task = observation["task"]
        state = observation["trip_context"]
        turn = observation["turn_number"]
        if task == "MEAL":
            action = AgentAction(
                kind="CALL_TOOL", tool="search_places",
                arguments={"keyword": "潮汕牛肉火锅", "city": state["destination"]},
            )
        elif task == "SCHEDULE":
            if turn == 1:
                action = AgentAction(kind="ASK_USER", question="请补充旅行日期。")
            else:
                action = AgentAction(
                    kind="CALL_TOOL", tool="get_distance_matrix",
                    arguments={"points": [
                        [point["latitude"], point["longitude"]]
                        for point in state["saved_place_details"]
                    ]},
                )
        elif task == "TRANSPORT":
            if turn == 1 and state.get("route_provider") != "timeout":
                action = AgentAction(kind="ASK_USER", question="请补充目的地。")
            elif turn == 2 and state.get("route_provider") != "timeout":
                action = AgentAction(kind="CALL_TOOL", tool="get_trip_route", arguments={
                    "origin_place_id": state["selected_origin_id"],
                    "destination_place_id": state.get("selected_destination_id") or next(
                        place["id"] for place in state["known_places"]
                        if place["id"] != state["selected_origin_id"]
                    ),
                    "mode": "DRIVING",
                })
            elif turn == 1:
                action = AgentAction(kind="CALL_TOOL", tool="get_trip_route", arguments={
                    "origin_place_id": state["selected_origin_id"],
                    "destination_place_id": state["selected_destination_id"],
                    "mode": "DRIVING",
                })
            else:
                action = AgentAction(kind="FINISH", summary="路线服务不可用，路线未核验。")
        elif task == "WEATHER" and turn == 1:
            action = AgentAction(kind="CALL_TOOL", tool="get_trip_weather", arguments={})
        else:
            action = AgentAction(kind="FINISH", summary="建议已根据当前观察整理。")
        return action, SimpleNamespace(prompt_tokens=10, completion_tokens=4, total_tokens=14)

    monkeypatch.setattr(live_eval, "decide_next_action", fake_decision)
    report = asyncio.run(live_eval.run_live(
        dataset_path=GROUNDED_HOLDOUT_V2_PATH,
        token_cap=32,
    ))

    assert report["selected_case_ids"] == [
        case["id"] for case in json.loads(
            GROUNDED_HOLDOUT_V2_PATH.read_text(encoding="utf-8")
        )["cases"]
    ]
    assert report["requests"] == 20
    for group in report["summary"].values():
        assert group["expected_action_matches"] == 10, [
            (case["case_id"], turn.get("turn_number"), turn["action"], turn["expected_action_match"])
            for case in group["runs"] for turn in case["turns"]
            if turn["expected_action_match"] is not True
        ]
        assert group["complete_task_matches"] == 5
        assert group["invalid_actions"] == 0
        assert group["allowlist_violations"] == 0


def test_holdout_rejects_historical_case_id_and_exact_prompt_overlap():
    dataset = _holdout()
    references = load_holdout_references(dataset, cases_directory=CASES)
    previous = references["advisor_multiturn_v5"]["cases"][0]

    duplicate_id = copy.deepcopy(dataset)
    duplicate_id["cases"][0]["id"] = previous["id"]
    with pytest.raises(ValueError, match="case_ids"):
        validate_holdout_separation(duplicate_id, references)

    duplicate_prompt = copy.deepcopy(dataset)
    duplicate_prompt["cases"][0]["turns"][0]["user"] = previous["turns"][0]["user"]
    with pytest.raises(ValueError, match="normalized_prompts"):
        validate_holdout_separation(duplicate_prompt, references)

    changed_reference = copy.deepcopy(references)
    changed_reference["advisor_multiturn_v5"]["cases"][0]["invariants"].append("late edit")
    with pytest.raises(ValueError, match="reference dataset changed"):
        validate_holdout_separation(dataset, changed_reference)


def test_holdout_rejects_expected_tool_outside_server_allowlist():
    dataset = _holdout()
    weather = next(case for case in dataset["cases"] if case["id"] == "holdout-weather-date-03")
    weather["turns"][1]["allowed_tools"] = ["search_places"]

    with pytest.raises(ValueError, match="expected tool is absent"):
        validate_dataset(dataset)


def test_holdout_runner_uses_its_declared_default_selection_and_report_identity(monkeypatch):
    from types import SimpleNamespace

    from travelpilot_core.agents.loop import AgentAction

    calls = []
    monkeypatch.setattr(live_eval.settings, "llm_api_key", "test-key")
    monkeypatch.setattr(live_eval.settings, "llm_model", "test-model")
    monkeypatch.setattr(live_eval.settings, "llm_base_url", "https://example.test/v1")

    async def fake_decision(observation, schemas, **kwargs):
        calls.append((observation, schemas, kwargs["token_budget"]))
        if observation["turn_number"] == 1:
            action = AgentAction(kind="ASK_USER", question="请补充必要信息")
        else:
            tool = next(iter(schemas))
            action = AgentAction(
                kind="CALL_TOOL",
                tool=tool,
                arguments={
                    "keyword": "已授权查询",
                    "origin": "A",
                    "destination": "B",
                    "points": [],
                },
            )
        return action, SimpleNamespace(prompt_tokens=12, completion_tokens=3, total_tokens=15)

    monkeypatch.setattr(live_eval, "decide_next_action", fake_decision)

    report = asyncio.run(
        live_eval.run_live(
            dataset_path=HOLDOUT_PATH,
            token_cap=16,
        )
    )

    assert report["evaluation_id"] == "advisor_multiturn_live_advisor_multiturn_holdout_v1"
    assert report["selected_case_ids"] == _holdout()["holdout_protocol"]["default_case_ids"]
    assert report["requests"] == 20
    assert report["requested_output_token_ceiling"] == 320
    assert report["total_output_token_budget"] == 2560
    assert report["holdout_separation"]["exact_normalized_prompt_overlap_count"] == 0
    assert len(calls) == 20
    assert all(cap == 16 for _observation, _schemas, cap in calls)


def test_holdout_failure_case_carries_synthetic_provider_failure_into_next_decision(monkeypatch):
    from types import SimpleNamespace

    from travelpilot_core.agents.loop import AgentAction

    observations = []
    monkeypatch.setattr(live_eval.settings, "llm_api_key", "test-key")
    monkeypatch.setattr(live_eval.settings, "llm_model", "test-model")
    monkeypatch.setattr(live_eval.settings, "llm_base_url", "https://example.test/v1")

    async def fake_decision(observation, schemas, **kwargs):
        observations.append(observation)
        state = observation["trip_context"]
        if observation["turn_number"] == 1:
            action = AgentAction(
                kind="CALL_TOOL",
                tool="get_trip_route",
                arguments={
                    "origin_place_id": state["selected_origin_id"],
                    "destination_place_id": state["selected_destination_id"],
                    "mode": "DRIVING",
                },
            )
        else:
            action = AgentAction(kind="ASK_USER", question="要不要稍后重试？")
        return action, SimpleNamespace(prompt_tokens=12, completion_tokens=3, total_tokens=15)

    monkeypatch.setattr(live_eval, "decide_next_action", fake_decision)
    report = asyncio.run(
        live_eval.run_live(
            dataset_path=GROUNDED_HOLDOUT_V2_PATH,
            case_ids=("holdout-v2-route-outage-safe-stop-15",),
            token_cap=16,
        )
    )

    second_turn = [item for item in observations if item["turn_number"] == 2]
    assert len(second_turn) == 2
    for observation in second_turn:
        failure = next(
            item["result"]
            for item in observation["conversation_history"]
            if item.get("role") == "tool_observation"
        )
        assert failure["status"] == "FAILED_FIXTURE"
        assert failure["error_code"] == "MAP_TIMEOUT"
    assert report["requests"] == 4


@pytest.mark.asyncio
async def test_live_runner_rejects_case_expansion_past_default_total_token_budget(monkeypatch):
    monkeypatch.setattr(live_eval.settings, "llm_api_key", "test-key")
    monkeypatch.setattr(live_eval.settings, "llm_model", "test-model")
    monkeypatch.setattr(live_eval.settings, "llm_base_url", "https://example.test/v1")

    async def unexpected_request(*_args, **_kwargs):
        raise AssertionError("budget must be checked before any model request")

    monkeypatch.setattr(live_eval, "decide_next_action", unexpected_request)
    selected = live_eval.CASE_IDS + (live_eval.ADDITIONAL_CASE_IDS[1],)

    with pytest.raises(ValueError, match="exceeds total budget"):
        await live_eval.run_live(case_ids=selected)


@pytest.mark.parametrize("budget", [0, -1, 1.5, True])
@pytest.mark.asyncio
async def test_live_runner_requires_positive_integer_total_budget(monkeypatch, budget):
    monkeypatch.setattr(live_eval.settings, "llm_api_key", "test-key")
    monkeypatch.setattr(live_eval.settings, "llm_model", "test-model")
    monkeypatch.setattr(live_eval.settings, "llm_base_url", "https://example.test/v1")

    with pytest.raises(ValueError, match="positive integer"):
        await live_eval.run_live(total_output_token_budget=budget)
