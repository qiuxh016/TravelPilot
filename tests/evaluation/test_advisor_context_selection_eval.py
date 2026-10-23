import json
from pathlib import Path

from tests.evaluation.advisor_context_selection_eval import (
    ARMS,
    _context_items,
    build_observation,
    validate_inputs,
)

ROOT = Path(__file__).resolve().parents[2]
DATASET = ROOT / "tests/evaluation/cases/advisor_context_quality_v1.json"


def test_context_arms_only_select_authorized_applicable_fresh_items():
    dataset = json.loads(DATASET.read_text(encoding="utf-8"))
    case = next(case for case in dataset["cases"] if case["id"].endswith("memory-scope"))

    assert _context_items(case, "RECENT_HISTORY") == []
    structured = _context_items(case, "STRUCTURED_CONTEXT")
    selective = _context_items(case, "SELECTIVE_CONTEXT")
    assert len(structured) == 2
    assert len(selective) == 1
    assert all("applicable" not in item and "authorized" not in item for item in structured + selective)
    assert "辣椒敏感" in selective[0]["content"]


def test_context_arms_keep_trip_state_and_tool_authority_equal():
    dataset = json.loads(DATASET.read_text(encoding="utf-8"))
    case = dataset["cases"][0]
    turn = case["turns"][0]
    observations = [
        build_observation(case=case, turn=turn, turn_index=0, history=[], arm=arm)
        for arm in ARMS
    ]

    for observation in observations[1:]:
        assert observation["trip_context"] == observations[0]["trip_context"]
        assert observation["available_tools"] == observations[0]["available_tools"]
        assert observation["policy"] == observations[0]["policy"]
    assert "context_items" not in observations[0]["trip_context"]


def test_turn_history_matches_production_bounds_and_keeps_fixture_observations_separate():
    dataset = json.loads(DATASET.read_text(encoding="utf-8"))
    case = dataset["cases"][0]
    history = [
        {"role": "system", "content": "internal"},
        {"role": "user", "content": "  第一个请求  "},
        {"role": "assistant", "content": "x" * 450, "action": {"secret": "excluded"}},
        {"role": "tool_observation", "tool": "get_distance_matrix", "result": {"status": "SIMULATED"}},
    ]
    observations = [
        build_observation(case=case, turn=case["turns"][1], turn_index=1, history=history, arm=arm)
        for arm in ARMS
    ]

    assert observations[0]["conversation_history"] == [
        {"role": "user", "content": "第一个请求"},
        {"role": "assistant", "content": "x" * 400},
    ]
    assert observations[0]["tool_observations"] == [
        {"tool": "get_distance_matrix", "result": {"status": "SIMULATED"}},
    ]
    assert all(observation["tool_observations"] == observations[0]["tool_observations"] for observation in observations)


def test_case_facts_match_first_turn_action_and_repair_outdated_assumptions():
    dataset = json.loads(DATASET.read_text(encoding="utf-8"))
    cases = {case["family"]: case for case in dataset["cases"]}
    meal = cases["meal-category-switch"]
    assert "已经选作周五午餐" in meal["turns"][0]["user"]
    assert "已由用户确认" in meal["initial_state"]["context_items"][0]["content"]

    weather = cases["weather-date-correction"]
    assert "trip_dates" not in weather["initial_state"]
    assert weather["turns"][0]["expected_action"] == "ASK_USER"

    approval = cases["schedule-approval-boundary"]
    assert approval["turns"][1]["expected_action"] == "ASK_USER"


def test_context_dataset_passes_frozen_overlap_and_arm_preflight():
    _, report = validate_inputs(DATASET)

    assert report["case_count"] == 20
    assert report["decision_count"] == 40
    assert report["planned_requests"] == 120
    assert report["planned_output_token_ceiling_at_256"] == 30720
    assert report["exact_normalized_prompt_overlap_count"] == 0
    assert report["live_model_calls"] == 0
    assert report["max_serialized_planner_input_chars"] <= report["planner_input_truncation_boundary_chars"]
