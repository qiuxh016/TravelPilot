from datetime import UTC, date, datetime
from types import SimpleNamespace
from uuid import uuid4

import pytest
from travelpilot_api.advisor import turn_service
from travelpilot_api.advisor.orchestration_state import stage_observation_ref
from travelpilot_api.advisor.route_validation import ItineraryRouteValidation
from travelpilot_core.agents.loop import LoopResult
from travelpilot_core.agents.runtime import AgentRuntime
from travelpilot_infra.db.models.advisor import AdvisorMessageModel


class Database:
    def __init__(self):
        self.rows = []

    def add(self, row):
        self.rows.append(row)

    async def scalars(self, _statement):
        return SimpleNamespace(all=list)

    async def commit(self):
        return None

    async def refresh(self, _row):
        return None

    async def rollback(self):
        return None


class Maps:
    async def search_place(self, _keyword, _city):
        return []


def _runtime():
    async def trace(_entry):
        return None

    return AgentRuntime([], trace, token_budget=128)


def test_missing_meal_preference_prompts_before_revising_a_selected_itinerary():
    question = turn_service._missing_meal_preference_question(
        "帮我把已收藏的地点安排成两日行程，节奏轻松。", 3,
    )

    assert question is not None
    assert "午餐" in question


@pytest.mark.parametrize("message", [
    "安排两日游并留出午餐时间。",
    "帮我找几个午餐候选。",
])
def test_meal_preference_is_not_asked_when_user_already_specified_it(message):
    assert turn_service._missing_meal_preference_question(message, 3) is None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("selected_order", "expected_calls", "failed_stage", "unsupported_route_mode"),
    [
        (["ROUTE", "SEARCH_WEATHER"], ["ROUTE", "WEATHER"], None, False),
        (["SEARCH_WEATHER", "ROUTE"], ["WEATHER", "ROUTE"], None, False),
        (["ROUTE", "SEARCH_WEATHER"], ["ROUTE", "WEATHER"], "ROUTE", False),
        (["SEARCH_WEATHER", "ROUTE"], ["WEATHER", "ROUTE"], "WEATHER", False),
        # An unsupported rail/flight mode changes only the route-stage outcome:
        # it must not bypass adaptive ordering for the other authorized stages.
        (["ROUTE", "SEARCH_WEATHER"], ["WEATHER"], None, True),
    ],
)
async def test_weather_and_saved_route_execute_in_selected_order(
    monkeypatch, selected_order, expected_calls, failed_stage, unsupported_route_mode,
):
    owner_id, session_id, trip_id = uuid4(), uuid4(), uuid4()
    row = SimpleNamespace(
        id=session_id, owner_id=owner_id, status="ACTIVE", mode="existing",
        trip_id=trip_id, destination="成都", start_date=date(2026, 11, 1),
        end_date=date(2026, 11, 2), budget=None, pace_level=3, preference_text="",
        context={"revision": 0, "planning_inputs": {"hard_constraints": []}},
        candidates=[
            {
                "id": "place-a", "provider": "fake", "provider_id": "a",
                "name": "人民公园", "address": "成都", "category": "公园",
                "planning_category": "ATTRACTION", "latitude": 30.66,
                "longitude": 104.06, "city_code": "510100",
                "duration_min": 60, "opening_hours": None,
                "verification_status": "SIMULATED",
            },
            {
                "id": "place-b", "provider": "fake", "provider_id": "b",
                "name": "武侯祠", "address": "成都", "category": "博物馆",
                "planning_category": "ATTRACTION", "latitude": 30.65,
                "longitude": 104.04, "city_code": "510100",
                "duration_min": 60, "opening_hours": None,
                "verification_status": "SIMULATED",
            },
        ],
        updated_at=None,
    )
    user = SimpleNamespace(id=owner_id)
    db = Database()
    calls = []
    decision_observation = {}
    decision_observation_ref = None

    async def no_refresh(*_args):
        return None

    def no_memory_refresh(context, _memories):
        return context, False

    async def no_memories(*_args):
        return []

    async def prior_stage_checkpoint(*_args, **_kwargs):
        return {
            "next_transition": 4,
            "has_previous_attempt": True,
            "interrupted_stage": "ROUTE",
            "completed_stages": ["SEARCH_WEATHER"],
            "selection_history": [{
                "eligible_stages": ["ROUTE", "SEARCH_WEATHER"],
                "order": ["SEARCH_WEATHER", "ROUTE"],
                "decision_source": "MODEL",
            }],
        }

    async def choose_stage_order(**kwargs):
        decision_observation.update(kwargs["observation"])
        return {
            "order": selected_order,
            "decision_source": "MODEL",
            "decision_summary": "根据当前可并行阶段选择执行顺序",
            "fallback_reason": None,
        }

    async def resolve_stage_order(_db, *, choose, observation_ref, **_kwargs):
        nonlocal decision_observation_ref
        decision_observation_ref = observation_ref
        return await choose()

    async def search_weather(**kwargs):
        assert kwargs["requests"] == []
        assert kwargs["weather_requested"] is True
        route_usage = 25 if calls and calls[0] == "ROUTE" else 0
        assert kwargs["token_budget"] == max(
            0, turn_service.settings.llm_max_tokens_per_turn - route_usage,
        )
        calls.append("WEATHER")
        runtime = kwargs["runtime"]
        return SimpleNamespace(
            results=[], weather=None,
            loop=LoopResult(
                "FAILED" if failed_stage == "WEATHER" else "SUCCEEDED", {}, [],
                "WEATHER_UNAVAILABLE" if failed_stage == "WEATHER" else None,
            ),
            runtime=runtime, decision_source="RULE",
            decision_summary="天气阶段已执行",
            pending_question=None,
            trace={
                "decision_source": "RULE", "decision_summary": "天气阶段已执行",
                "status": "FAILED" if failed_stage == "WEATHER" else "SUCCEEDED",
                "transitions": [],
                "execution_metrics": runtime.execution_metrics,
            },
        )

    async def route_query(**_kwargs):
        calls.append("ROUTE")
        runtime = _runtime()
        runtime.output_tokens_used = 25
        loop = LoopResult(
            "FAILED" if failed_stage == "ROUTE" else "SUCCEEDED", {}, [],
            "ROUTE_UNAVAILABLE" if failed_stage == "ROUTE" else None,
        )
        return SimpleNamespace(
            route=None, loop=loop, runtime=runtime,
            trace={
                "decision_source": "RULE", "decision_summary": "路线阶段已执行",
                "status": "FAILED" if failed_stage == "ROUTE" else "SUCCEEDED",
                "transitions": [],
                "execution_metrics": runtime.execution_metrics,
            },
        )

    async def read_session(_db, session_row):
        return session_row

    def no_checkpoint_writer(**_kwargs):
        async def write_checkpoint(_state):
            return None
        return write_checkpoint

    monkeypatch.setattr(turn_service, "refresh_trip_snapshot", no_refresh)
    monkeypatch.setattr(turn_service, "sync_personal_memory_snapshot", no_memory_refresh)
    monkeypatch.setattr(turn_service, "_active_personal_memories", no_memories)
    monkeypatch.setattr(turn_service, "_memory_suggestions_enabled", lambda *_args: _false())
    monkeypatch.setattr(turn_service, "read_session", read_session)
    monkeypatch.setattr(turn_service, "checkpoint_writer", no_checkpoint_writer)
    monkeypatch.setattr(turn_service, "load_turn_stage_checkpoint", prior_stage_checkpoint)
    monkeypatch.setattr(
        turn_service, "requests_unsupported_route_mode",
        lambda _goal: unsupported_route_mode,
    )
    if unsupported_route_mode:
        monkeypatch.setattr(
            turn_service, "accepts_driving_route_confirmation",
            lambda *_args: False,
        )
    monkeypatch.setattr(turn_service, "choose_stage_order", choose_stage_order)
    monkeypatch.setattr(turn_service, "resolve_stage_order", resolve_stage_order)
    monkeypatch.setattr(turn_service, "run_advisor_search_loop", search_weather)
    monkeypatch.setattr(turn_service, "run_advisor_route_query", route_query)
    monkeypatch.setattr(turn_service, "can_compose_grounded_reply", lambda **_kwargs: False)
    monkeypatch.setattr(turn_service.settings, "llm_api_key", "")
    monkeypatch.setattr(turn_service.settings, "llm_model", "")
    monkeypatch.setattr(turn_service.settings, "maps_provider", "fake")

    user_message = "查旅行天气和两个已保存地点之间的路线"
    if unsupported_route_mode:
        user_message += "；如果跨城请帮我查成都到西安的高铁"
    await turn_service.execute_advisor_turn(
        db, row, user_message, Maps(), object(),
        "stage-recovery-test-0001", user,
    )

    assert calls == expected_calls
    assistant = next(
        item for item in reversed(db.rows)
        if isinstance(item, AdvisorMessageModel) and item.role == "assistant"
    )
    assert assistant.payload["turn_orchestration"]["order"] == selected_order
    assert assistant.payload["agent_trace"]["turn_orchestration"]["order"] == selected_order
    assert "智能回答暂时不可用" not in assistant.content
    assert "未配置智能回答模型" not in assistant.content
    assert assistant.payload["agent_trace"]["recovery"] == {
        "status": "SAME_TASK_SAFE_RETRY", "interrupted_stage": "ROUTE",
        "completed_stages": ["SEARCH_WEATHER"],
    }
    decisions = assistant.payload["agent_trace"]["turn_orchestration"]["decision_history"]
    assert decisions[0]["decision_source"] == "RECOVERED"
    assert decisions[0]["decision_summary"].startswith("上次尝试")
    assert decisions[1]["decision_source"] == "MODEL"
    assert decision_observation["trip_facts"]["saved_places"] == 2
    assert decision_observation_ref == stage_observation_ref(decision_observation)
    changed_facts = {
        **decision_observation,
        "trip_facts": {**decision_observation["trip_facts"], "saved_places": 3},
    }
    assert stage_observation_ref(changed_facts) != decision_observation_ref


@pytest.mark.asyncio
async def test_itinerary_route_action_updates_right_side_evidence_and_keeps_meal_selection(monkeypatch):
    owner_id, session_id, trip_id = uuid4(), uuid4(), uuid4()
    stops = [
        {"candidate_id": "place-a", "name": "客家博物馆", "planning_category": "ATTRACTION",
         "day": "2026-11-02", "start_time": "09:00", "end_time": "11:00"},
        {"candidate_id": "place-b", "name": "东山书院", "planning_category": "ATTRACTION",
         "day": "2026-11-02", "start_time": "12:00", "end_time": "13:00"},
    ]
    context = {
        "revision": 4,
        "planning_inputs": {"origin": "梅州站"},
        "selected_candidate_ids": ["place-a", "lunch", "place-b"],
        "current_plan": {"version": 3, "stops": stops},
    }
    row = SimpleNamespace(
        id=session_id, owner_id=owner_id, status="ACTIVE", mode="existing", trip_id=trip_id,
        destination="梅州", start_date=date(2026, 11, 2), end_date=date(2026, 11, 2),
        budget=None, pace_level=3, preference_text="", context=context,
        candidates=[
            {"id": "place-a", "name": "客家博物馆", "planning_category": "ATTRACTION",
             "latitude": 24.30, "longitude": 116.10, "duration_min": 60},
            {"id": "lunch", "name": "已选午餐店", "planning_category": "FOOD",
             "latitude": 24.29, "longitude": 116.09, "duration_min": 60},
            {"id": "place-b", "name": "东山书院", "planning_category": "ATTRACTION",
             "latitude": 24.31, "longitude": 116.11, "duration_min": 60},
        ],
        updated_at=None,
        draft=None,
    )
    db = Database()

    async def no_refresh(*_args):
        return context

    def no_memory_refresh(current, _memories):
        return current, False

    async def no_memories(*_args):
        return []

    async def read_session(_db, session_row):
        return session_row

    def no_checkpoint_writer(**_kwargs):
        async def write_checkpoint(_state):
            return None
        return write_checkpoint

    async def validate(**kwargs):
        kwargs["context"]["agent_route_evidence"] = {
            "candidate_ids": ["place-a", "place-b"],
            "context_revision": 4,
            "durations_min": [[0, 21], [21, 0]],
            "status": "VERIFIED",
            "source": "AMap Web Service",
            "retrieved_at": datetime.now(UTC).isoformat(),
        }
        return ItineraryRouteValidation(
            stops=stops,
            candidate_ids=["place-a", "place-b"],
            matrix=[[0, 21], [21, 0]],
            status="VERIFIED",
            source="AMap Web Service",
            retrieved_at=datetime.now(UTC).isoformat(),
        )

    monkeypatch.setattr(turn_service, "refresh_trip_snapshot", no_refresh)
    monkeypatch.setattr(turn_service, "sync_personal_memory_snapshot", no_memory_refresh)
    monkeypatch.setattr(turn_service, "_active_personal_memories", no_memories)
    monkeypatch.setattr(turn_service, "_memory_suggestions_enabled", lambda *_args: _false())
    monkeypatch.setattr(turn_service, "read_session", read_session)
    monkeypatch.setattr(turn_service, "checkpoint_writer", no_checkpoint_writer)
    monkeypatch.setattr(turn_service, "validate_active_itinerary_routes", validate)
    monkeypatch.setattr(turn_service.settings, "llm_api_key", "")
    monkeypatch.setattr(turn_service.settings, "llm_model", "")
    monkeypatch.setattr(turn_service.settings, "maps_provider", "fake")

    await turn_service.execute_advisor_turn(
        db, row, "校验路线", Maps(), object(), "route-validation-test-0001",
        SimpleNamespace(id=owner_id),
    )

    assistant = next(
        item for item in reversed(db.rows)
        if isinstance(item, AdvisorMessageModel) and item.role == "assistant"
    )
    assert "客家博物馆 → 东山书院约 21 分钟" in assistant.content
    assert assistant.payload["route_validation"]["status"] == "VERIFIED"
    assert "ROUTES" not in {item["key"] for item in assistant.payload["trip_action_items"]}
    assert context["selected_candidate_ids"] == ["place-a", "lunch", "place-b"]
    assert assistant.payload["agent_task"]["status"] == "COMPLETED"


async def _false():
    return False
