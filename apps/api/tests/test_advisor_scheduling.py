from datetime import UTC, date, datetime, time
from types import SimpleNamespace
from uuid import uuid4

import pytest
from travelpilot_api.advisor.route_evidence import gather_route_evidence
from travelpilot_api.advisor.scheduling import (
    _requested_day_start_times,
    _requested_meal_breaks,
    _schedule_conflicts,
    propose_existing_trip_schedule,
)
from travelpilot_api.advisor.task_contract import build_agent_task_context
from travelpilot_api.advisor.tools import distance_tool_runtime
from travelpilot_core.agents.loop import AgentAction
from travelpilot_core.engines.optimizer.itinerary import Candidate, Visit
from travelpilot_infra.providers.maps.errors import MapsProviderError
from travelpilot_infra.providers.maps.fake import FakeMapsProvider


def _visits(second_start: time) -> tuple[list[Visit], list[str]]:
    first_id, second_id = uuid4(), uuid4()
    first = Candidate(first_id, "预约活动", 30.0, 104.0, 60, 3, 0.5)
    second = Candidate(second_id, "后续地点", 30.1, 104.1, 60, 3, 0.5)
    visits = [
        Visit(first, date(2026, 11, 14), time(15, 0), time(16, 0)),
        Visit(
            second,
            date(2026, 11, 14),
            second_start,
            time(second_start.hour + 1, second_start.minute),
        ),
    ]
    return visits, [str(first_id), str(second_id)]


def _schedule_candidates(*, fixed_schedule=None):
    poi_ids = [str(uuid4()), str(uuid4())]
    candidates = [
        {
            "id": str(uuid4()), "existing_poi_id": poi_id,
            "in_current_plan": True, "name": f"地点 {index + 1}",
            "latitude": 30.66 + index / 100, "longitude": 104.06 + index / 100,
            "duration_min": 60, "confidence": 0.8, "category": "ATTRACTION",
            "planning_category": "ATTRACTION",
        }
        for index, poi_id in enumerate(poi_ids)
    ]
    if fixed_schedule:
        for candidate in candidates:
            candidate["fixed_schedule"] = fixed_schedule
    context = {
        "revision": 1,
        "current_plan": {
            "stops": [{"poi_id": poi_id, "status": "FIXED" if fixed_schedule else "PLANNED"}
                      for poi_id in poi_ids],
        },
    }
    return candidates, context


def test_fixed_visit_followed_by_insufficient_route_time_is_reported():
    visits, candidate_ids = _visits(time(16, 20))

    conflicts = _schedule_conflicts(visits, candidate_ids, [[0, 20], [20, 0]])

    assert len(conflicts) == 1
    assert "预约活动 至 后续地点 时间不足" in conflicts[0]


def test_fixed_visit_with_route_and_transfer_buffer_is_accepted():
    visits, candidate_ids = _visits(time(16, 35))

    assert _schedule_conflicts(visits, candidate_ids, [[0, 20], [20, 0]]) == []


def test_explicit_free_morning_becomes_a_day_start_constraint():
    start = date(2026, 10, 22)
    assert _requested_day_start_times(
        "请让第二天上午留空，午餐留出一小时", start, date(2026, 10, 23),
    ) == {date(2026, 10, 23): 12 * 60}
    assert _requested_day_start_times(
        "请让第二天上午留空", start, start,
    ) == {}


@pytest.mark.parametrize(("goal", "duration"), [
    ("每天午餐留出一小时", 60),
    ("午饭预留90分钟", 90),
    ("晚餐留半小时", 30),
    ("午餐留一个半小时", 90),
])
def test_explicit_meal_break_duration_becomes_editable_default_windows(goal, duration):
    breaks = _requested_meal_breaks(
        goal, date(2026, 11, 2), date(2026, 11, 3),
    )

    assert len(breaks) == 2
    assert all(item["duration_min"] == duration for item in breaks)
    assert all(item["time_is_proposed_default"] is True for item in breaks)
    assert all(item["start_time"] == ("18:00" if "晚" in goal else "12:00") for item in breaks)


def test_meal_break_requires_explicit_reservation_and_duration():
    assert _requested_meal_breaks(
        "请推荐午餐", date(2026, 11, 2), date(2026, 11, 2),
    ) == []
    assert _requested_meal_breaks(
        "午餐不需要预留时间", date(2026, 11, 2), date(2026, 11, 2),
    ) == []
    assert _requested_meal_breaks(
        "午餐留出两天", date(2026, 11, 2), date(2026, 11, 2),
    ) == []


@pytest.mark.asyncio
async def test_capacity_infeasibility_returns_explanation_without_schedule_draft():
    poi_ids = [str(uuid4()) for _ in range(4)]
    candidates = [
        {
            "id": str(uuid4()),
            "existing_poi_id": poi_id,
            "in_current_plan": True,
            "name": f"地点 {index}",
            "latitude": 30.66 + index * 0.001,
            "longitude": 104.06 + index * 0.001,
            "duration_min": 120,
            "confidence": 0.8,
            "category": "ATTRACTION",
        }
        for index, poi_id in enumerate(poi_ids)
    ]
    context = {"current_plan": {"stops": [{"poi_id": poi_id} for poi_id in poi_ids]}}

    result = await propose_existing_trip_schedule(
        context=context,
        candidates=candidates,
        user_goal="轻松安排这四个地点",
        destination="成都",
        start_date=date(2026, 11, 2),
        end_date=date(2026, 11, 2),
        pace_level=1,
        maps=FakeMapsProvider(),
        api_key="",
        model="",
        base_url="",
        token_budget=128,
    )

    assert result.suggestions == []
    assert result.error and "无法安置全部地点" in result.error
    assert "每日 3 个地点" in result.error
    assert result.error.endswith("还是暂不调整？")
    assert result.trace["status"] == "NEEDS_USER_INPUT"


@pytest.mark.asyncio
async def test_recovered_route_matrix_is_reused_and_keeps_original_retrieval_time(monkeypatch):
    from travelpilot_api.advisor import scheduling

    candidates = [
        {
            "id": str(uuid4()), "existing_poi_id": str(uuid4()),
            "in_current_plan": True, "name": f"地点 {index}",
            "latitude": 30.6 + index / 100, "longitude": 104.0 + index / 100,
            "duration_min": 60, "confidence": 0.8, "category": "ATTRACTION",
            "planning_category": "ATTRACTION",
        }
        for index in range(2)
    ]
    candidate_ids = [item["id"] for item in candidates]
    poi_ids = {item["existing_poi_id"] for item in candidates}
    context = {
        "revision": 4,
        "selected_candidate_ids": candidate_ids,
        "current_plan": {"stops": [{"poi_id": poi_id, "status": "PLANNED"} for poi_id in poi_ids]},
    }
    retrieved_at = datetime.now(UTC).isoformat()
    loaded = []
    saved = []

    async def load(**scope):
        loaded.append(scope)
        return {
            "candidate_ids": candidate_ids, "context_revision": 4,
            "durations_min": [[0, 9], [11, 0]], "status": "SIMULATED",
            "source": "Local simulated maps", "retrieved_at": retrieved_at,
        }

    async def save(snapshot):
        saved.append(snapshot)

    async def gather(_runtime, _points, **kwargs):
        assert _runtime.token_budget == 64
        assert kwargs["cached_evidence"]["retrieved_at"] == retrieved_at
        return SimpleNamespace(
            matrix=[[0, 9], [11, 0]],
            draft={
                "feasible": True, "candidate_ids": candidate_ids, "warnings": [],
                "schedules": [
                    {"candidate_id": candidate_ids[0], "day": "2026-11-02",
                     "start_time": "09:00", "end_time": "10:00"},
                    {"candidate_id": candidate_ids[1], "day": "2026-11-02",
                     "start_time": "10:30", "end_time": "11:30"},
                ],
            },
            loop=SimpleNamespace(transitions=[], status="SUCCEEDED"),
            decision_source="OBSERVATION", decision_summary="恢复已核验路线",
            evidence_status="SIMULATED", evidence_source="Local simulated maps",
            evidence_retrieved_at=retrieved_at, question=None,
        )

    monkeypatch.setattr(scheduling, "gather_route_evidence", gather)
    result = await propose_existing_trip_schedule(
        context=context, candidates=candidates, user_goal="合理安排时间",
        destination="成都", start_date=date(2026, 11, 2), end_date=date(2026, 11, 2),
        pace_level=3, maps=FakeMapsProvider(), api_key="", model="", base_url="",
        token_budget=64, route_evidence_loader=load, route_evidence_saver=save,
    )

    assert len(loaded) == 1
    assert loaded[0]["candidate_ids"] == candidate_ids
    assert saved == []
    assert context["agent_route_evidence"]["retrieved_at"] == retrieved_at
    assert result.suggestions


@pytest.mark.asyncio
async def test_explicitly_selected_wishlist_places_can_be_scheduled_before_plan_confirmation(monkeypatch):
    from travelpilot_api.advisor import scheduling

    candidate_id = str(uuid4())
    poi_id = str(uuid4())
    candidate = {
        "id": candidate_id, "existing_poi_id": poi_id, "in_current_plan": False,
        "name": "人民公园", "latitude": 30.663, "longitude": 104.055,
        "duration_min": 90, "confidence": 0.9, "category": "ATTRACTION",
        "planning_category": "ATTRACTION",
    }

    async def gather(_runtime, _points, **_kwargs):
        return SimpleNamespace(
            matrix=[[0]],
            draft={
                "feasible": True, "candidate_ids": [candidate_id], "warnings": [],
                "schedules": [{
                    "candidate_id": candidate_id, "day": "2026-11-02",
                    "start_time": "09:00", "end_time": "10:30",
                }],
            },
            loop=SimpleNamespace(transitions=[], status="SUCCEEDED"),
            decision_source="OBSERVATION", decision_summary="安排已明确选择的收藏地点",
            evidence_status="VERIFIED", evidence_source="AMap Web Service",
            evidence_retrieved_at=datetime.now(UTC).isoformat(), question=None,
        )

    monkeypatch.setattr(scheduling, "gather_route_evidence", gather)
    result = await propose_existing_trip_schedule(
        context={"selected_candidate_ids": [candidate_id], "current_plan": {"stops": []}},
        candidates=[candidate], user_goal="把收藏的人民公园安排成一日行程",
        destination="成都", start_date=date(2026, 11, 2), end_date=date(2026, 11, 2),
        pace_level=3, maps=FakeMapsProvider(), api_key="", model="", base_url="",
        token_budget=128,
    )

    assert result.error is None
    assert result.candidate_count == 1
    assert result.selected_candidate_ids == [candidate_id]
    assert result.suggestions[0]["candidate_id"] == candidate_id


@pytest.mark.asyncio
async def test_schedule_decision_checkpoint_reuses_approved_draft_action(monkeypatch):
    from travelpilot_api.advisor import route_evidence

    model_calls = []
    decisions = {}

    async def decide(_observation, _schemas, **_kwargs):
        model_calls.append(True)
        return AgentAction(
            kind="CALL_TOOL", tool="build_schedule_draft", arguments={},
            summary="生成排程草案",
        ), SimpleNamespace(prompt_tokens=1, completion_tokens=1, total_tokens=2)

    monkeypatch.setattr(route_evidence, "decide_next_action", decide)
    runtime = distance_tool_runtime(FakeMapsProvider())
    task_context = build_agent_task_context(
        task_type="SCHEDULE", goal="安排当前旅行地点",
        known_facts=["selected_places"], missing_facts=[],
        available_tools=["build_schedule_draft"],
    )

    async def builder(_matrix):
        return {
            "feasible": True,
            "candidate_ids": ["candidate-1"],
            "schedules": [{"candidate_id": "candidate-1"}],
            "warnings": [],
        }

    async def load(**scope):
        saved = decisions.get("checkpoint")
        if saved is None:
            return None
        if (
            scope["observation_ref"] != saved["observation_ref"]
            or scope["decision_number"] != saved["decision_number"]
            or scope["approved_actions"] != saved["approved_actions"]
        ):
            return None
        return {"tool": saved["tool"], "action_ref": saved["action_ref"]}

    async def save(**checkpoint):
        decisions["checkpoint"] = checkpoint

    async def execute_once():
        return await gather_route_evidence(
            runtime,
            [(30.66, 104.06)],
            trip_context={
                "destination": "成都", "start_date": "2026-11-02",
                "end_date": "2026-11-02", "pace_level": 3,
                "has_dates": True, "fixed_stop_count": 0,
            },
            user_goal="安排当前旅行地点",
            api_key="configured-for-test", model="local-test", base_url="http://stub",
            token_budget=128,
            task_context=task_context,
            schedule_builder=builder,
            load_decision_checkpoint=load,
            save_decision_checkpoint=save,
        )

    first = await execute_once()
    second = await execute_once()

    assert first.loop.status == "SUCCEEDED"
    assert second.loop.status == "SUCCEEDED"
    assert first.decision_source == "MODEL"
    assert second.decision_source == "RECOVERED"
    assert model_calls == [True]
    assert second.draft["candidate_ids"] == ["candidate-1"]


@pytest.mark.asyncio
async def test_model_cannot_skip_multi_place_route_check_before_schedule_draft(monkeypatch):
    from travelpilot_api.advisor import route_evidence

    candidate_ids = ["candidate-1", "candidate-2"]
    points = [(30.66, 104.06), (30.65, 104.05)]
    calls = []

    async def decide(observation, schemas, **_kwargs):
        calls.append((dict(observation), sorted(schemas)))
        # Simulate a model returning a draft action even when that tool is
        # withheld from its first-step schema.
        return AgentAction(
            kind="CALL_TOOL", tool="build_schedule_draft", arguments={},
            summary="try drafting without route evidence",
        ), SimpleNamespace(prompt_tokens=1, completion_tokens=1, total_tokens=2)

    monkeypatch.setattr(route_evidence, "decide_next_action", decide)
    runtime = distance_tool_runtime(FakeMapsProvider())

    async def builder(matrix):
        assert matrix is not None
        return {
            "feasible": True,
            "candidate_ids": candidate_ids,
            "schedules": [
                {"candidate_id": candidate_id, "day": "2026-11-02",
                 "start_time": f"{9 + index}:00", "end_time": f"{10 + index}:00"}
                for index, candidate_id in enumerate(candidate_ids)
            ],
            "warnings": [],
        }

    result = await gather_route_evidence(
        runtime, points,
        trip_context={
            "destination": "成都", "start_date": "2026-11-02",
            "end_date": "2026-11-02", "pace_level": 3,
            "has_dates": True, "fixed_stop_count": 0,
            "candidate_ids": candidate_ids,
        },
        user_goal="安排两个已选地点",
        api_key="configured-for-test", model="local-test", base_url="http://stub",
        token_budget=128,
        task_context=build_agent_task_context(
            task_type="SCHEDULE", goal="安排两个已选地点",
            known_facts=["selected_places"], missing_facts=[],
            available_tools=["get_distance_matrix", "build_schedule_draft"],
        ),
        schedule_builder=builder,
    )

    assert calls[0][1] == ["get_distance_matrix"]
    assert calls[1][1] == ["build_schedule_draft"]
    assert result.matrix is not None
    assert result.draft is not None
    assert [item["tool"] for item in result.loop.transitions] == [
        "get_distance_matrix", "build_schedule_draft",
    ]


@pytest.mark.asyncio
async def test_vertical_slice_builds_a_non_persistent_draft_after_route_verification():
    candidates, context = _schedule_candidates()

    result = await propose_existing_trip_schedule(
        context=context, candidates=candidates, user_goal="合理安排已收藏地点",
        destination="成都", start_date=date(2026, 11, 2), end_date=date(2026, 11, 2),
        pace_level=3, maps=FakeMapsProvider(), api_key="", model="", base_url="",
        token_budget=128,
    )

    assert result.error is None
    assert len(result.suggestions) == 2
    assert result.trace["route_evidence"] == "SIMULATED"
    assert result.trace["status"] == "SUCCEEDED"
    assert "confirmed_plan_id" not in context


@pytest.mark.asyncio
async def test_missing_trip_dates_stops_before_map_or_draft_work():
    class MapsMustNotRun(FakeMapsProvider):
        async def get_distance_matrix(self, _points):
            raise AssertionError("maps should not run when dates are missing")

    candidates, context = _schedule_candidates()
    result = await propose_existing_trip_schedule(
        context=context, candidates=candidates, user_goal="安排地点",
        destination="成都", start_date=None, end_date=None,
        pace_level=3, maps=MapsMustNotRun(), api_key="", model="", base_url="",
        token_budget=128,
    )

    assert result.suggestions == []
    assert result.error and "缺少日期" in result.error
    assert result.runtime is None


@pytest.mark.asyncio
async def test_route_provider_failure_returns_warning_draft_without_formal_write():
    class MapsUnavailable(FakeMapsProvider):
        async def get_distance_matrix(self, _points):
            raise MapsProviderError("fixture outage")

    candidates, context = _schedule_candidates()
    result = await propose_existing_trip_schedule(
        context=context, candidates=candidates, user_goal="安排两个地点",
        destination="成都", start_date=date(2026, 11, 2), end_date=date(2026, 11, 2),
        pace_level=3, maps=MapsUnavailable(), api_key="", model="", base_url="",
        token_budget=128,
    )

    assert result.error is None
    assert len(result.suggestions) == 2
    assert result.warning and "尚未由地图核验" in result.warning
    assert result.trace["route_evidence"] == "UNAVAILABLE"
    assert "confirmed_plan_id" not in context


@pytest.mark.asyncio
async def test_overlapping_fixed_appointments_block_schedule_proposal():
    fixed = {"day": "2026-11-02", "start_time": "09:00"}
    candidates, context = _schedule_candidates(fixed_schedule=fixed)

    result = await propose_existing_trip_schedule(
        context=context, candidates=candidates, user_goal="安排地点",
        destination="成都", start_date=date(2026, 11, 2), end_date=date(2026, 11, 2),
        pace_level=3, maps=FakeMapsProvider(), api_key="", model="", base_url="",
        token_budget=128,
    )

    assert result.suggestions == []
    assert result.error and "固定预约" in result.error and "冲突" in result.error
    assert result.error.endswith("还是暂不调整？")
    assert result.trace["status"] == "NEEDS_USER_INPUT"
    assert "confirmed_plan_id" not in context
