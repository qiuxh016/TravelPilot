from types import SimpleNamespace

import pytest
from travelpilot_core.agents.loop import AgentAction

from tests.evaluation import advisor_multiturn_live_eval as evaluation


@pytest.mark.asyncio
async def test_live_multiturn_runner_makes_twenty_bounded_contextual_decisions(monkeypatch):
    calls = []
    monkeypatch.setattr(evaluation.settings, "llm_api_key", "test-key")
    monkeypatch.setattr(evaluation.settings, "llm_model", "test-model")
    monkeypatch.setattr(evaluation.settings, "llm_base_url", "https://example.test/v1")

    async def fake_decision(observation, schemas, **kwargs):
        calls.append((observation, schemas, kwargs["token_budget"]))
        if observation["turn_number"] == 1:
            action = AgentAction(kind="ASK_USER", question="请补充偏好")
        else:
            tool = next(iter(schemas))
            args = {"keyword": "成都景点"} if tool == "search_places" else (
                {"points": []} if tool == "get_distance_matrix" else {}
            )
            action = AgentAction(kind="CALL_TOOL", tool=tool, arguments=args)
        return action, SimpleNamespace(prompt_tokens=20, completion_tokens=9, total_tokens=29)

    monkeypatch.setattr(evaluation, "decide_next_action", fake_decision)
    report = await evaluation.run_live()

    assert report["requests"] == 20
    assert report["requested_output_token_ceiling"] == 2560
    assert len(calls) == 20
    assert all(cap == 128 for _, _, cap in calls)
    assert all("expected_action" not in observation for observation, _, _ in calls)
    assert all(observation["tool_observations_are_local_fixtures"] is True
               for observation, _, _ in calls)
    assert all(turn["allowlist_valid"] for group in report["groups"].values()
               for case in group for turn in case["turns"])
    second_turn_observations = [observation for observation, _, _ in calls
                                if observation["turn_number"] == 2]
    assert all(any(item.get("role") == "assistant" for item in observation["conversation_history"])
               for observation in second_turn_observations)
    assert "production run_constrained_loop" in report["arm_definitions"]["B3_VERIFIED_RECOVERY_LOOP"]
    assert "end_to_end_task_completion_evaluated" in report["groups"]["B2_AGENT_LOOP"][0]


@pytest.mark.asyncio
async def test_b3_arm_runs_selected_action_through_production_loop(monkeypatch):
    seen = []

    async def fake_decision(observation, schemas, **kwargs):
        seen.append(observation)
        return AgentAction(kind="ASK_USER", question="补充信息"), SimpleNamespace(
            prompt_tokens=1, completion_tokens=1, total_tokens=2,
        )

    monkeypatch.setattr(evaluation, "decide_next_action", fake_decision)
    dataset = evaluation.json.loads(evaluation.DATASET.read_text(encoding="utf-8"))
    case = dataset["cases"][0]
    turn = case["turns"][0]
    history = [{"role": "user", "content": turn["user"]}]

    b3_row = await evaluation._decide(
        case=case,
        turn_index=0,
        turn=turn,
        variant="B2_AGENT_LOOP",
        history=history,
    )
    b3_row = await evaluation._decide(
        case=case,
        turn_index=0,
        turn=turn,
        variant="B3_VERIFIED_RECOVERY_LOOP",
        history=history,
    )

    assert "execution_contract" not in seen[0]
    assert seen[1]["execution_contract"]
    assert b3_row["execution"]["loop_status"] == "NEEDS_USER_INPUT"
    assert b3_row["execution"]["tool_calls"] == []


@pytest.mark.asyncio
async def test_live_multiturn_runner_accepts_frozen_additional_batch_with_same_budget(monkeypatch):
    calls = []
    monkeypatch.setattr(evaluation.settings, "llm_api_key", "test-key")
    monkeypatch.setattr(evaluation.settings, "llm_model", "test-model")
    monkeypatch.setattr(evaluation.settings, "llm_base_url", "https://example.test/v1")

    async def fake_decision(observation, schemas, **kwargs):
        calls.append(kwargs["token_budget"])
        if not schemas:
            action = AgentAction(kind="ASK_USER", question="请补充缺少的信息")
            return action, SimpleNamespace(prompt_tokens=20, completion_tokens=9, total_tokens=29)
        tool = next(iter(schemas))
        action = AgentAction(kind="CALL_TOOL", tool=tool, arguments={
            "keyword": "测试地点", "points": [], "origin": "A", "destination": "B",
        })
        return action, SimpleNamespace(prompt_tokens=20, completion_tokens=9, total_tokens=29)

    monkeypatch.setattr(evaluation, "decide_next_action", fake_decision)
    report = await evaluation.run_live(case_ids=evaluation.ADDITIONAL_CASE_IDS)

    assert report["selected_case_ids"] == list(evaluation.ADDITIONAL_CASE_IDS)
    assert report["requests"] == 20
    assert report["requested_output_token_ceiling"] == 2560
    assert calls == [128] * 20


@pytest.mark.asyncio
async def test_live_multiturn_runner_rejects_budget_over_approved_per_request_cap(monkeypatch):
    monkeypatch.setattr(evaluation.settings, "llm_api_key", "test-key")
    monkeypatch.setattr(evaluation.settings, "llm_model", "test-model")
    monkeypatch.setattr(evaluation.settings, "llm_base_url", "https://example.test/v1")
    with pytest.raises(ValueError, match="token cap"):
        await evaluation.run_live(case_ids=evaluation.ADDITIONAL_CASE_IDS, token_cap=129)


def test_live_multiturn_selection_is_frozen_and_has_no_write_tools():
    dataset = evaluation.json.loads(evaluation.DATASET.read_text(encoding="utf-8"))
    selected = [case for case in dataset["cases"] if case["id"] in evaluation.CASE_IDS]
    assert len(selected) == 5
    assert all(len([turn for turn in case["turns"]
                    if turn["action_owner"] == "AGENT_DECISION"]) >= 2 for case in selected)
    assert not any("commit" in tool or "delete" in tool for tool in evaluation.TOOL_SCHEMAS)


def test_schedule_draft_tool_is_hidden_until_matching_route_evidence_exists():
    case = {
        "initial_state": {"saved_places": 3},
    }
    turn = {"allowed_tools": ["get_distance_matrix", "build_schedule_draft"]}

    before_route = evaluation._allowed_tool_schemas(case, turn, [])
    assert list(before_route) == ["get_distance_matrix"]

    valid_matrix = evaluation._fixture_observation("get_distance_matrix", case)
    after_route = evaluation._allowed_tool_schemas(case, turn, [{
        "role": "tool_observation",
        "tool": "get_distance_matrix",
        "result": valid_matrix,
    }])
    assert list(after_route) == ["build_schedule_draft"]

    malformed_matrix = {**valid_matrix, "durations_min": [[0, 10], [10, 0]]}
    after_malformed_route = evaluation._allowed_tool_schemas(case, turn, [{
        "role": "tool_observation",
        "tool": "get_distance_matrix",
        "result": malformed_matrix,
    }])
    assert list(after_malformed_route) == ["get_distance_matrix"]

    one_stop = evaluation._allowed_tool_schemas(
        {"initial_state": {"saved_places": 1}}, turn, [],
    )
    assert list(one_stop) == ["build_schedule_draft"]


def test_schedule_draft_allows_explicit_route_failure_for_warned_fallback():
    case = {"initial_state": {"saved_places": 3, "route_provider": "timeout"}}
    turn = {"allowed_tools": ["get_distance_matrix", "build_schedule_draft"]}
    failure = evaluation._fixture_observation("get_distance_matrix", case)

    schemas = evaluation._allowed_tool_schemas(case, turn, [{
        "role": "tool_observation",
        "tool": "get_distance_matrix",
        "result": failure,
    }])

    assert set(schemas) == {"get_distance_matrix", "build_schedule_draft"}


def test_additional_live_batch_has_five_two_turn_frozen_cases():
    dataset = evaluation.json.loads(evaluation.DATASET.read_text(encoding="utf-8"))
    by_id = {case["id"]: case for case in dataset["cases"]}
    selected = [by_id[case_id] for case_id in evaluation.ADDITIONAL_CASE_IDS]

    assert len(selected) == 5
    assert len({case["family"] for case in selected}) == 5
    assert all(len([turn for turn in case["turns"]
                    if turn["action_owner"] == "AGENT_DECISION"]) >= 2
               for case in selected)
    assert all(
        turn["expected_action"] == "ASK_USER"
        or turn["expected_action"] in evaluation.ASK_OR_DISCLOSE_ACTIONS
        or turn["expected_action"] in evaluation.EXPECTED_TOOL
        for case in selected
        for turn in case["turns"]
        if turn["action_owner"] == "AGENT_DECISION"
    )
    assert "memory-current-request-wins-15" not in evaluation.ADDITIONAL_CASE_IDS


def test_abstract_domain_transitions_are_not_scored_as_model_actions():
    assert evaluation._expected_match(
        {"expected_action": "FOLLOW_CURRENT_REQUEST"}, {"kind": "ASK_USER"},
    ) is None
    assert evaluation._expected_match(
        {"expected_action": "UPDATE_TASK_CONSTRAINTS"}, {"kind": "CALL_TOOL", "tool": "build_schedule_draft"},
    ) is None


def test_safe_finish_can_satisfy_ask_or_disclose_contract():
    assert evaluation._expected_match(
        {"expected_action": "ASK_USER_OR_LABEL_ASSUMPTION"},
        {"kind": "FINISH", "summary": "Forecast data is unavailable; this is only a fixture."},
    ) is True


def test_adjudicated_action_sets_accept_multiple_safe_choices():
    turn = {
        "expected_action": "ASK_USER_OR_LABEL_UNVERIFIED",
        "acceptable_actions": ["ASK_USER_OR_LABEL_UNVERIFIED", "FINISH"],
    }
    assert evaluation._expected_match(turn, {"kind": "ASK_USER", "question": "请人工核实"}) is True
    assert evaluation._expected_match(turn, {"kind": "FINISH", "summary": "当前没有可靠营业信息"}) is True
    assert evaluation._expected_match(turn, {"kind": "CALL_TOOL", "tool": "search_places"}) is False


@pytest.mark.asyncio
async def test_b3_recovery_stops_after_local_route_fixture_timeout():
    case = {"initial_state": {"route_provider": "timeout"}}
    action = AgentAction(
        kind="CALL_TOOL", tool="get_trip_route",
        arguments={"origin_place_id": "a", "destination_place_id": "b", "mode": "DRIVING"},
    )

    result = await evaluation._run_verified_loop(action, case, ["get_trip_route"])

    assert result["loop_status"] == "SUCCEEDED"
    assert result["safe_stop"] is True
    assert result["tool_calls"] == ["get_trip_route"]
    assert any(item["state"] == "FAILED" for item in result["checkpoints"])


@pytest.mark.asyncio
async def test_b3_route_uses_production_route_evidence_verifier():
    case = {"initial_state": {"known_places": [
        {"id": "a", "name": "起点", "latitude": 24.45, "longitude": 118.08},
        {"id": "b", "name": "终点", "latitude": 24.44, "longitude": 118.10},
    ]}}
    action = AgentAction(
        kind="CALL_TOOL", tool="get_trip_route",
        arguments={"origin_place_id": "a", "destination_place_id": "b", "mode": "DRIVING"},
    )

    result = await evaluation._run_verified_loop(action, case, ["get_trip_route"])

    assert result["loop_status"] == "SUCCEEDED"
    assert result["verified_tool_count"] == 1
    assert result["safe_stop"] is False
    assert result["observations"]["get_trip_route"]["status"] == "SIMULATED"


@pytest.mark.asyncio
async def test_b3_accepts_production_verified_local_search_weather_and_schedule_evidence():
    case = {"initial_state": {
        "destination": "测试城",
        "trip_dates": ["2027-04-12", "2027-04-13"],
        "saved_places": 2,
        "saved_place_details": [
            {"id": "p1", "latitude": 30.1, "longitude": 104.1},
            {"id": "p2", "latitude": 30.2, "longitude": 104.2},
        ],
    }}
    actions = [
        AgentAction(kind="CALL_TOOL", tool="search_places", arguments={"keyword": "公园", "city": "测试城"}),
        AgentAction(kind="CALL_TOOL", tool="get_trip_weather", arguments={}),
        AgentAction(kind="CALL_TOOL", tool="get_distance_matrix", arguments={"points": [[30.1, 104.1], [30.2, 104.2]]}),
        AgentAction(kind="CALL_TOOL", tool="build_schedule_draft", arguments={}),
    ]

    for action in actions:
        result = await evaluation._run_verified_loop(action, case, [action.tool])
        assert result["loop_status"] == "SUCCEEDED"
        assert result["verified_tool_count"] == 1
        assert result["safe_stop"] is False


def test_tool_arguments_are_validated_against_allowlisted_read_only_schemas():
    assert evaluation._validate_tool_arguments(
        "get_distance_matrix", {"points": [[30.1, 104.1], [30.2, 104.2]]},
    ) == (True, [])
    valid, errors = evaluation._validate_tool_arguments(
        "get_distance_matrix", {"points": ["saved_places"]},
    )
    assert valid is False
    assert errors
    assert evaluation._validate_tool_arguments("get_trip_weather", {}) == (True, [])
    assert evaluation._validate_tool_arguments("get_trip_weather", {"city": "成都"})[0] is False
    assert evaluation._validate_tool_arguments(
        "get_trip_route", {
            "origin_place_id": "place-a",
            "destination_place_id": "place-b",
            "mode": "DRIVING",
        },
    ) == (True, [])
    assert evaluation._validate_tool_arguments(
        "get_trip_route", {
            "origin_place_id": "place-a",
            "destination_place_id": "place-b",
            "mode": "driving",
        },
    )[0] is False
    assert evaluation._validate_tool_arguments(
        "get_trip_route", {"origin": "A", "destination": "B", "mode": "DRIVING"},
    )[0] is False


def test_route_arguments_must_reference_distinct_places_in_current_context():
    route = {
        "origin_place_id": "place-a",
        "destination_place_id": "place-b",
        "mode": "DRIVING",
    }
    context = {"known_places": [{"id": "place-a"}, {"id": "place-b"}]}
    assert evaluation._validate_tool_arguments("get_trip_route", route, context) == (True, [])
    assert evaluation._validate_tool_arguments(
        "get_trip_route", {**route, "destination_place_id": "unknown"}, context,
    )[1][0]["type"] == "route_endpoint_not_in_known_places"
    assert evaluation._validate_tool_arguments("get_trip_route", route, {})[1][0]["type"] == (
        "missing_route_endpoint_evidence"
    )


def test_distance_matrix_arguments_must_match_saved_place_coordinates():
    context = {"saved_place_details": [
        {"latitude": 30.1, "longitude": 104.1},
        {"latitude": 30.2, "longitude": 104.2},
    ]}
    points = [[30.1, 104.1], [30.2, 104.2]]
    assert evaluation._validate_tool_arguments(
        "get_distance_matrix", {"points": points}, context,
    ) == (True, [])
    assert evaluation._validate_tool_arguments(
        "get_distance_matrix", {"points": [[30.1, 104.1], [31.0, 105.0]]}, context,
    )[1][0]["type"] == "matrix_coordinates_do_not_match_saved_places"
    assert evaluation._validate_tool_arguments(
        "get_distance_matrix", {"points": points}, {},
    )[1][0]["type"] == "matrix_points_not_grounded_in_saved_places"


def test_rescoring_uses_saved_trip_context_for_route_grounding():
    report = {
        "groups": {"B2_AGENT_LOOP": [{
            "case_id": "route",
            "initial_state": {"known_places": [{"id": "origin"}]},
            "turns": [{
                "expected_action": "GET_TRIP_ROUTE",
                "action": {"kind": "CALL_TOOL", "tool": "get_trip_route", "arguments": {
                    "origin_place_id": "origin",
                    "destination_place_id": "invented",
                    "mode": "DRIVING",
                }},
                "allowlist_valid": True,
                "action_valid": True,
                "output_tokens": 1,
                "prompt_tokens": 2,
            }],
        }]},
    }

    rescored = evaluation.rescore_report(report)

    turn = rescored["groups"]["B2_AGENT_LOOP"][0]["turns"][0]
    assert turn["tool_arguments_valid"] is False
    assert turn["expected_action_match"] is False
    assert turn["argument_validation_errors"][0]["type"] == (
        "route_endpoint_not_in_known_places"
    )


def test_invalid_tool_call_never_receives_a_successful_fixture_observation():
    row = {
        "action_valid": False,
        "action": {"kind": "CALL_TOOL", "tool": "get_trip_route"},
    }

    assert not evaluation._should_append_fixture_observation(row, ["get_trip_route"])
    row["action_valid"] = True
    assert evaluation._should_append_fixture_observation(row, ["get_trip_route"])


def test_rescoring_existing_report_excludes_unrepresentable_action_labels():
    report = {
        "groups": {"B2_AGENT_LOOP": [{"case_id": "memory", "turns": [
            {"expected_action": "FOLLOW_CURRENT_REQUEST", "action": {"kind": "ASK_USER"},
             "allowlist_valid": True, "action_valid": True, "output_tokens": 1, "prompt_tokens": 2},
            {"expected_action": "UPDATE_TASK_CONSTRAINTS", "action": {"kind": "ASK_USER"},
             "allowlist_valid": True, "action_valid": True, "output_tokens": 1, "prompt_tokens": 2},
        ]}]},
    }
    rescored = evaluation.rescore_report(report)
    group = rescored["summary"]["B2_AGENT_LOOP"]

    assert group["decision_count"] == 2
    assert group["scoreable_decision_count"] == 0
    assert group["unscorable_decision_count"] == 2
    assert group["action_sequence_scoreable_count"] == 0
    assert rescored["scoring_revision"]["model_requests_repeated"] is False
    assert len(rescored["scoring_revision"]["rescorer_source_sha256"]) == 64


def test_rescoring_marks_invalid_tool_arguments_as_invalid_and_unmatched():
    report = {
        "groups": {"B3_PROMPT_POLICY_ONLY": [{"case_id": "schedule", "turns": [
            {
                "expected_action": "GET_DISTANCE_MATRIX",
                "action": {
                    "kind": "CALL_TOOL",
                    "tool": "get_distance_matrix",
                    "arguments": {"points": ["saved_places"]},
                },
                "allowlist_valid": True,
                "action_valid": True,
                "output_tokens": 2,
                "prompt_tokens": 3,
            },
        ]}]},
    }

    rescored = evaluation.rescore_report(report)
    turn = rescored["groups"]["B3_PROMPT_POLICY_ONLY"][0]["turns"][0]
    summary = rescored["summary"]["B3_PROMPT_POLICY_ONLY"]

    assert turn["tool_arguments_valid"] is False
    assert turn["expected_action_match"] is False
    assert summary["invalid_actions"] == 1
    assert summary["expected_action_matches"] == 0
    assert rescored["scoring_revision"]["id"] == "adjudicated_acceptable_action_sets_v4"
