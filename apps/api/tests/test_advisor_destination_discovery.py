from datetime import UTC, date, datetime, timedelta
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi import HTTPException
from travelpilot_api.advisor import turn_service
from travelpilot_api.advisor.destination_discovery import (
    _verified_result,
    run_destination_discovery,
)
from travelpilot_api.advisor.destination_service import (
    destination_evidence_callbacks,
    execute_destination_stage,
    start_destination_recommendation_from_chat,
)
from travelpilot_api.advisor.interpretation import _DESTINATION_CITIES
from travelpilot_api.advisor.state import pending_agent_question, resume_pending_task
from travelpilot_api.advisor.tools import rank_map_places
from travelpilot_api.advisor.turn_service import (
    _resume_destination_recommendation,
    execute_advisor_turn,
)
from travelpilot_api.api.v1 import advisor as advisor_routes
from travelpilot_api.schemas.advisor import AdvisorDestinationDiscoveryRequest
from travelpilot_core.agents.extraction import Extraction
from travelpilot_core.agents.loop import AgentAction
from travelpilot_core.ports.maps import Place
from travelpilot_infra.db.models.advisor import AdvisorMessageModel, AdvisorRunEventModel
from travelpilot_infra.providers.maps.amap import AmapProvider


class Maps:
    def __init__(self):
        self.calls = []

    async def search_place(self, keyword, city):
        self.calls.append((city, keyword))
        return [SimpleNamespace(
            provider="amap", provider_id=f"{city}-{keyword}", name=f"{city}{keyword}",
            category="景点", address="测试地址", latitude=30.0, longitude=104.0,
        )]


@pytest.mark.asyncio
async def test_destination_search_preserves_map_rating_as_labeled_evidence():
    class RatedMaps:
        async def search_place(self, keyword, city):
            return [
                Place("amap", "low", "低分地点", "地址", "景点", 30.0, 104.0,
                      raw_metadata={"biz_ext": {"rating": "3.8"}}),
                Place("amap", "high", "高分地点", "地址", "景点", 30.1, 104.1,
                      raw_metadata={"biz_ext": {"rating": "4.9"}}),
                Place("amap", "unrated", "无评分地点", "地址", "景点", 30.2, 104.2,
                      raw_metadata={}),
            ]

    result = await run_destination_discovery(
        maps=RatedMaps(), queries=[("历史文化", "历史古迹")],
        approved_cities=["泉州"], user_goal="探索泉州历史文化",
    )

    places = result.searches[0]["places"]
    ranked_places = rank_map_places(places)
    assert [place["provider_id"] for place in ranked_places] == ["high", "low", "unrated"]
    assert [place["popularity_score"] for place in places] == [3.8, 4.9, None]


@pytest.mark.asyncio
async def test_amap_response_rating_reaches_verified_destination_evidence(monkeypatch):
    provider = AmapProvider("local-test-key")

    async def fake_get(path, **_params):
        assert path == "/place/text"
        return {"pois": [{
            "id": "amap-poi-1", "name": "泉州古街", "location": "118.58,24.91",
            "type": "风景名胜", "address": "泉州本地fixture", "citycode": "350500",
            "biz_ext": {"rating": "4.8"},
        }]}

    monkeypatch.setattr(provider, "_get", fake_get)
    result = await run_destination_discovery(
        maps=provider, queries=[("历史文化", "历史古迹")],
        approved_cities=["泉州"], user_goal="只看泉州历史文化",
    )

    place = result.searches[0]["places"][0]
    assert place["provider"] == "amap"
    assert place["provider_id"] == "amap-poi-1"
    assert place["popularity_score"] == 4.8


@pytest.mark.asyncio
async def test_destination_discovery_records_shared_stage_lifecycle():
    checkpoints = []
    expected = SimpleNamespace(
        loop=SimpleNamespace(status="NEEDS_USER_INPUT"),
        runtime=SimpleNamespace(calls=[{}]),
        searches=[{"places": [{"provider_id": "p1"}]}],
        decision_source="MODEL",
        decision_summary="需要先了解用户偏好",
    )

    async def checkpoint(value):
        checkpoints.append(value)

    async def runner(**_kwargs):
        return expected

    result, execution = await execute_destination_stage(
        runner=runner,
        runner_kwargs={"user_goal": "推荐适合我的目的地"},
        checkpoint=checkpoint,
    )

    assert result is expected
    assert execution["statuses"] == {"SEARCH_WEATHER": "NEEDS_USER_INPUT"}
    assert [item["state"] for item in checkpoints] == ["STAGE_STARTED", "NEEDS_USER_INPUT"]
    assert all(item["tool"] == "SEARCH_WEATHER" for item in checkpoints)


@pytest.mark.asyncio
async def test_destination_evidence_callbacks_are_scoped_to_task_and_query(monkeypatch):
    owner_id, session_id = uuid4(), uuid4()
    loaded, persisted = [], []
    snapshot = {
        "status": "VERIFIED", "retrieved_at": datetime.now(UTC).isoformat(),
        "places": [{
            "provider": "amap", "provider_id": "xian-food-1", "name": "西安地方菜",
            "latitude": 34.25, "longitude": 108.95,
        }],
    }

    async def load(_db, **kwargs):
        loaded.append(kwargs)
        return snapshot if kwargs["scope_ref"] == "matching-scope" else None

    def writer(**kwargs):
        async def save(value):
            persisted.append((kwargs, value))

        return save

    monkeypatch.setattr(
        "travelpilot_api.advisor.destination_service.load_search_evidence_checkpoint", load,
    )
    monkeypatch.setattr(
        "travelpilot_api.advisor.destination_service.search_evidence_checkpoint_writer", writer,
    )
    monkeypatch.setattr(
        "travelpilot_api.advisor.destination_service.search_scope_ref",
        lambda city, keyword, revision, source: (
            "matching-scope" if (city, keyword, revision, source)
            == ("西安", "美食", 7, "Maps") else "other-scope"
        ),
    )

    cached, save = await destination_evidence_callbacks(
        db=object(), owner_id=owner_id, session_id=session_id, maps=Maps(),
        task_id="destination:request-1", context_revision=7,
        queries=[("地方美食", "美食"), ("自然与公园", "公园")],
    )
    assert len(loaded) == 12
    assert all(item["task_id"] == "destination:request-1" for item in loaded)
    assert cached == {("西安", 0): snapshot}

    output = {
        "city": "西安", "interest_index": 0, "status": "VERIFIED",
        "retrieved_at": snapshot["retrieved_at"], "places": snapshot["places"],
    }
    await save(output)
    assert len(persisted) == 1
    assert persisted[0][0]["task_id"] == "destination:request-1"
    assert persisted[0][1] == output


@pytest.mark.asyncio
async def test_destination_discovery_fallback_stays_within_twelve_approved_searches():
    maps = Maps()

    result = await run_destination_discovery(
        maps=maps,
        queries=[("自然与公园", "公园"), ("地方美食", "美食")],
        user_goal="探索旅行目的地",
    )

    assert result.loop.status == "SUCCEEDED"
    assert len(maps.calls) == 12
    assert maps.calls[:2] == [("成都", "公园"), ("成都", "美食")]
    assert len(result.searches) == 12
    assert result.runtime.execution_metrics["logical_steps"] == 12


@pytest.mark.asyncio
async def test_destination_agent_selects_from_allowlist_and_adapts_after_observation(monkeypatch):
    maps = Maps()
    selected = []
    actions = iter([
        AgentAction(
            kind="CALL_TOOL", tool="search_destination_places",
            arguments={"city": "西安", "interest_index": 1}, summary="先比较西安美食",
        ),
        AgentAction(kind="FINISH", summary="已有地图证据，先展示候选"),
    ])

    async def decide(observation, schemas, **_kwargs):
        assert set(schemas) == {"search_destination_places"}
        if selected:
            assert observation["observed_searches"] == [{
                "city": "西安", "interest": "地方美食", "status": "VERIFIED", "result_count": 1,
            }]
        action = next(actions)
        if action.kind == "CALL_TOOL":
            selected.append((action.arguments["city"], action.arguments["interest_index"]))
        return action, SimpleNamespace(prompt_tokens=5, completion_tokens=4, total_tokens=9)

    monkeypatch.setattr("travelpilot_api.advisor.destination_discovery.decide_next_action", decide)
    result = await run_destination_discovery(
        maps=maps,
        queries=[("自然与公园", "公园"), ("地方美食", "美食")],
        user_goal="想吃当地特色",
        api_key="configured", model="test-model", base_url="https://model.invalid/v1",
        token_budget=128,
    )

    assert selected == [("西安", 1)]
    assert maps.calls == [("西安", "美食")]
    assert result.loop.status == "SUCCEEDED"
    assert result.decision_source == "MODEL"
    assert result.runtime.execution_metrics["output_tokens"] == 8


@pytest.mark.asyncio
async def test_destination_agent_can_search_user_selected_city_only(monkeypatch):
    maps = Maps()
    observed_scopes = []
    decision_number = 0

    async def decide(observation, schemas, **_kwargs):
        nonlocal decision_number
        decision_number += 1
        observed_scopes.append(observation["approved_cities"])
        if decision_number > 1:
            return AgentAction(kind="FINISH", summary="候选已核验"), SimpleNamespace(
                prompt_tokens=2, completion_tokens=1, total_tokens=3,
            )
        return AgentAction(
            kind="CALL_TOOL", tool="search_destination_places",
            arguments={"city": "泉州", "interest_index": 0}, summary="先核验泉州候选",
        ), SimpleNamespace(prompt_tokens=5, completion_tokens=4, total_tokens=9)

    monkeypatch.setattr("travelpilot_api.advisor.destination_discovery.decide_next_action", decide)
    result = await run_destination_discovery(
        maps=maps,
        queries=[("历史文化", "历史古迹"), ("地方美食", "美食")],
        approved_cities=["泉州"],
        user_goal="探索泉州的旅行地点",
        api_key="configured", model="test-model", base_url="https://model.invalid/v1",
        token_budget=128,
    )

    assert observed_scopes and all(scope == ["泉州"] for scope in observed_scopes)
    assert maps.calls == [("泉州", "历史古迹")]
    assert result.searches[0]["city"] == "泉州"


@pytest.mark.asyncio
async def test_destination_agent_cannot_escape_user_selected_city_scope(monkeypatch):
    maps = Maps()

    async def decide(_observation, _schemas, **_kwargs):
        return AgentAction(
            kind="CALL_TOOL", tool="search_destination_places",
            arguments={"city": "西安", "interest_index": 0}, summary="尝试越出用户指定城市",
        ), SimpleNamespace(prompt_tokens=5, completion_tokens=4, total_tokens=9)

    monkeypatch.setattr("travelpilot_api.advisor.destination_discovery.decide_next_action", decide)
    result = await run_destination_discovery(
        maps=maps,
        queries=[("地方美食", "美食")],
        approved_cities=["泉州"],
        user_goal="探索泉州的美食",
        api_key="configured", model="test-model", base_url="https://model.invalid/v1",
        token_budget=128,
    )

    assert maps.calls == [("泉州", "美食")]
    assert result.runtime.calls[0]["input_data"] == {"city": "泉州", "interest_index": 0}
    assert result.decision_source == "RULE_RECOVERY"


@pytest.mark.asyncio
async def test_destination_discovery_reuses_fresh_verified_observation(monkeypatch):
    maps = Maps()
    saved = []
    retrieved_at = datetime.now(UTC).isoformat()
    cached = {
        "status": "VERIFIED",
        "retrieved_at": retrieved_at,
        "places": [{
            "provider": "amap", "provider_id": "xian-food-1", "name": "西安地方菜",
            "category": "餐饮", "address": "碑林区", "latitude": 34.25,
            "longitude": 108.95,
        }],
    }
    actions = iter([
        AgentAction(kind="CALL_TOOL", tool="search_destination_places",
                    arguments={"city": "西安", "interest_index": 0}),
        AgentAction(kind="FINISH", summary="已取得有效候选"),
    ])

    async def decide(_observation, _schemas, **_kwargs):
        return next(actions), SimpleNamespace(prompt_tokens=2, completion_tokens=2, total_tokens=4)

    async def save_observation(value):
        saved.append(value)

    monkeypatch.setattr("travelpilot_api.advisor.destination_discovery.decide_next_action", decide)
    result = await run_destination_discovery(
        maps=maps,
        queries=[("地方美食", "美食")],
        user_goal="想了解西安美食",
        api_key="configured", model="test-model", token_budget=128,
        cached_searches={("西安", 0): cached},
        search_observation_callback=save_observation,
    )

    assert maps.calls == []
    assert result.loop.status == "SUCCEEDED"
    assert result.searches[0]["places"][0]["name"] == "西安地方菜"
    assert len(saved) == 1
    assert saved[0]["retrieved_at"] == retrieved_at


def test_destination_evidence_verifier_rejects_expired_or_unzoned_snapshots():
    good = {
        "city": "成都", "interest_index": 0, "status": "NO_RESULTS", "places": [],
        "retrieved_at": datetime.now(UTC).isoformat(),
    }
    assert _verified_result(good, "成都", 0)
    expired = {**good, "retrieved_at": (datetime.now(UTC) - timedelta(minutes=11)).isoformat()}
    assert not _verified_result(expired, "成都", 0)
    unzoned = {**good, "retrieved_at": "2026-10-19T08:00:00"}
    assert not _verified_result(unzoned, "成都", 0)


@pytest.mark.asyncio
async def test_destination_agent_can_ask_before_searching(monkeypatch):
    maps = Maps()

    async def decide(_observation, _schemas, **_kwargs):
        return AgentAction(kind="ASK_USER", question="你更想看自然风景还是历史街区？"), SimpleNamespace(
            prompt_tokens=5, completion_tokens=8, total_tokens=13,
        )

    monkeypatch.setattr("travelpilot_api.advisor.destination_discovery.decide_next_action", decide)
    result = await run_destination_discovery(
        maps=maps,
        queries=[("自然与公园", "公园"), ("地方美食", "美食")],
        user_goal="帮我想想去哪玩",
        api_key="configured", model="test-model", base_url="https://model.invalid/v1",
        token_budget=128,
    )

    assert result.loop.status == "NEEDS_USER_INPUT"
    assert result.loop.pending_question == "你更想看自然风景还是历史街区？"
    assert maps.calls == []
    assert result.runtime.execution_metrics["output_tokens"] == 8


@pytest.mark.asyncio
async def test_destination_loop_recovers_approved_search_after_interruption(monkeypatch):
    maps = Maps()
    saved = {}
    calls_to_model = 0
    interrupt_once = True

    async def decide(_observation, _schemas, **_kwargs):
        nonlocal calls_to_model
        calls_to_model += 1
        if calls_to_model == 1:
            return AgentAction(
                kind="CALL_TOOL", tool="search_destination_places",
                arguments={"city": "西安", "interest_index": 1},
            ), SimpleNamespace(prompt_tokens=2, completion_tokens=2, total_tokens=4)
        return AgentAction(kind="FINISH", summary="候选充分"), SimpleNamespace(
            prompt_tokens=2, completion_tokens=2, total_tokens=4,
        )

    async def save(**decision):
        saved.update(decision)

    async def load(*, observation_ref, decision_number, approved_actions):
        if (
            observation_ref == saved.get("observation_ref")
            and decision_number == saved.get("decision_number")
            and approved_actions.get(saved.get("action_ref")) == saved.get("tool")
        ):
            return {"tool": saved["tool"], "action_ref": saved["action_ref"]}
        return None

    async def checkpoint(state):
        nonlocal interrupt_once
        if interrupt_once and state["state"] == "ACTING":
            interrupt_once = False
            raise RuntimeError("simulated process interruption")

    monkeypatch.setattr("travelpilot_api.advisor.destination_discovery.decide_next_action", decide)
    options = {
        "maps": maps,
        "queries": [("自然与公园", "公园"), ("地方美食", "美食")],
        "user_goal": "想找适合吃美食的城市",
        "api_key": "configured", "model": "test-model", "token_budget": 128,
        "checkpoint": checkpoint,
        "load_decision_checkpoint": load,
        "save_decision_checkpoint": save,
    }
    with pytest.raises(RuntimeError, match="simulated process interruption"):
        await run_destination_discovery(**options)

    result = await run_destination_discovery(**options)
    assert result.loop.status == "SUCCEEDED"
    assert result.decision_source == "MODEL"
    assert calls_to_model == 2
    assert maps.calls[0] == ("西安", "美食")
    assert saved["tool"] == "search_destination_places"


def test_destination_verifier_rejects_fake_or_invalid_coordinate_evidence():
    good = {
        "city": "成都", "interest_index": 0, "status": "VERIFIED",
        "retrieved_at": datetime.now(UTC).isoformat(),
        "places": [{
            "provider": "amap", "provider_id": "p1", "name": "公园",
            "latitude": 30.0, "longitude": 104.0,
        }],
    }
    assert _verified_result(good, "成都", 0)
    fake = {**good, "places": [{**good["places"][0], "provider": "fake"}]}
    assert not _verified_result(fake, "成都", 0)
    invalid = {**good, "places": [{**good["places"][0], "latitude": 120.0}]}
    assert not _verified_result(invalid, "成都", 0)
    boolean_coordinate = {**good, "places": [{**good["places"][0], "latitude": True}]}
    assert not _verified_result(boolean_coordinate, "成都", 0)
    unbounded_id = {**good, "places": [{**good["places"][0], "provider_id": "p" * 121}]}
    assert not _verified_result(unbounded_id, "成都", 0)
    verified_without_places = {**good, "places": []}
    assert not _verified_result(verified_without_places, "成都", 0)
    no_results_with_places = {**good, "status": "NO_RESULTS"}
    assert not _verified_result(no_results_with_places, "成都", 0)
    boolean_interest_index = {**good, "interest_index": False}
    assert not _verified_result(boolean_interest_index, "成都", 0)
    fractional_interest_index = {**good, "interest_index": 0.0}
    assert not _verified_result(fractional_interest_index, "成都", 0)


@pytest.mark.asyncio
async def test_clarified_destination_task_reenters_map_loop_and_preserves_task_history(monkeypatch):
    pending = pending_agent_question({
        "agent_trace": {"status": "NEEDS_USER_INPUT"},
        "pending_task_type": "DISCOVERY",
        "pending_task_kind": "DESTINATION_RECOMMENDATION",
        "pending_task": "帮我找适合旅行的目的地",
        "pending_question": "你更喜欢历史街区还是海滨？",
        "pending_task_id": "destination-task-1",
        "pending_turn_number": 1,
        "pending_answers": [],
        "context_revision": 0,
    })
    resumed, _ = resume_pending_task(pending, "我喜欢历史街区")
    row = SimpleNamespace(
        id=uuid4(), owner_id=uuid4(), preference_text="",
        context={"revision": 0, "planning_inputs": {"hard_constraints": []}},
        updated_at=None,
    )
    user = SimpleNamespace(id=row.owner_id)

    class Database:
        def __init__(self):
            self.rows = []

        def add(self, item):
            self.rows.append(item)

        async def commit(self):
            pass

        async def refresh(self, _row):
            pass

    async def read_session(_db, session_row):
        return session_row

    monkeypatch.setattr(turn_service, "read_session", read_session)
    monkeypatch.setattr(turn_service.settings, "llm_api_key", "")
    monkeypatch.setattr(turn_service.settings, "llm_model", "")
    monkeypatch.setattr(turn_service.settings, "maps_provider", "amap")
    maps, db = Maps(), Database()

    result = await _resume_destination_recommendation(
        db=db, row=row, user=user, maps=maps, answer="我喜欢历史街区",
        pending=pending, resumed=resumed,
    )

    assert result is row
    assert len(maps.calls) == 6
    assert {keyword for _city, keyword in maps.calls} == {"历史古迹"}
    message = next(item for item in db.rows if isinstance(item, AdvisorMessageModel))
    assert message.payload["recommendations"]
    assert message.payload["agent_task"]["task_id"] == "destination-task-1"
    assert message.payload["agent_task"]["answers"] == ["我喜欢历史街区"]
    assert message.payload["agent_task"]["turn_number"] == 2
    assert message.payload["agent_task"]["status"] == "COMPLETED"
    assert "pending_agent_question" not in row.context
    assert not any(hasattr(item, "trip_id") or hasattr(item, "draft") for item in db.rows)
    assert any(isinstance(item, AdvisorRunEventModel) for item in db.rows)


@pytest.mark.asyncio
async def test_destination_continuation_uses_confirmed_memory_but_current_negation_wins(monkeypatch):
    pending = pending_agent_question({
        "agent_trace": {"status": "NEEDS_USER_INPUT"},
        "pending_task_type": "DISCOVERY",
        "pending_task_kind": "DESTINATION_RECOMMENDATION",
        "pending_task": "帮我找适合旅行的目的地",
        "pending_question": "你更喜欢什么类型的目的地？",
        "pending_task_id": "destination-memory-1",
        "pending_turn_number": 1,
        "pending_answers": [],
        "context_revision": 4,
    })
    answer = "不想看历史古迹，想去海边"
    resumed, _ = resume_pending_task(pending, answer)
    row = SimpleNamespace(
        id=uuid4(), owner_id=uuid4(), preference_text="",
        context={
            "revision": 4,
            "planning_inputs": {"hard_constraints": []},
            "personal_memories": [
                {"text": "喜欢历史文化", "constraint_type": "PREFERENCE"},
                {"text": "喜欢海滨休闲", "constraint_type": "PREFERENCE"},
                {"text": "避免夜生活", "constraint_type": "HARD_LIMIT"},
            ],
        },
        updated_at=None,
    )
    user = SimpleNamespace(id=row.owner_id)

    class Database:
        def __init__(self):
            self.rows = []

        def add(self, item):
            self.rows.append(item)

        async def commit(self):
            pass

        async def refresh(self, _row):
            pass

    async def read_session(_db, session_row):
        return session_row

    monkeypatch.setattr(turn_service, "read_session", read_session)
    monkeypatch.setattr(turn_service.settings, "llm_api_key", "")
    monkeypatch.setattr(turn_service.settings, "llm_model", "")
    monkeypatch.setattr(turn_service.settings, "maps_provider", "amap")
    maps, db = Maps(), Database()

    result = await _resume_destination_recommendation(
        db=db, row=row, user=user, maps=maps, answer=answer,
        pending=pending, resumed=resumed,
    )

    assert result is row
    assert len(maps.calls) == 6
    assert {keyword for _city, keyword in maps.calls} == {"海滨"}
    assert row.preference_text == answer
    assert "喜欢历史文化" not in row.preference_text
    assert "喜欢海滨休闲" not in row.preference_text


@pytest.mark.asyncio
async def test_destination_continuation_refuses_simulated_maps(monkeypatch):
    pending = pending_agent_question({
        "agent_trace": {"status": "NEEDS_USER_INPUT"},
        "pending_task_type": "DISCOVERY",
        "pending_task_kind": "DESTINATION_RECOMMENDATION",
        "pending_task": "帮我找适合旅行的目的地",
        "pending_question": "喜欢什么风格？",
        "pending_task_id": "destination-task-2",
        "context_revision": 0,
    })
    row = SimpleNamespace(
        id=uuid4(), owner_id=uuid4(), preference_text="自然", context={"revision": 0},
    )

    class Maps:
        async def search_place(self, *_args):
            raise AssertionError("destination recommendation must not use fake maps")

    monkeypatch.setattr(turn_service.settings, "maps_provider", "fake")
    with pytest.raises(HTTPException) as error:
        await _resume_destination_recommendation(
            db=object(), row=row, user=SimpleNamespace(id=row.owner_id), maps=Maps(),
            answer="喜欢历史街区", pending=pending, resumed=pending,
        )
    assert error.value.status_code == 503
    assert row.preference_text == "自然"


@pytest.mark.asyncio
async def test_message_turn_dispatches_destination_answer_back_into_destination_loop(monkeypatch):
    owner_id, session_id = uuid4(), uuid4()
    previous = SimpleNamespace(
        role="assistant",
        payload={
            "agent_trace": {"status": "NEEDS_USER_INPUT"},
            "pending_task_type": "DISCOVERY",
            "pending_task_kind": "DESTINATION_RECOMMENDATION",
            "pending_task": "帮我想想适合旅行的目的地",
            "pending_question": "更喜欢历史街区还是海滨？",
            "pending_task_id": "destination-dispatch-1",
            "pending_answers": [],
            "pending_turn_number": 1,
            "context_revision": 0,
        },
    )
    row = SimpleNamespace(
        id=session_id, owner_id=owner_id, status="ACTIVE", trip_id=None,
        preference_text="", destination="", start_date=None, end_date=None,
        context={"revision": 0, "planning_inputs": {"hard_constraints": []}},
        updated_at=None,
    )
    user = SimpleNamespace(id=owner_id)

    class Database:
        def __init__(self):
            self.rows = []

        def add(self, item):
            self.rows.append(item)

        async def scalars(self, _statement):
            return SimpleNamespace(all=lambda: [previous])

        async def commit(self):
            pass

        async def refresh(self, _row):
            pass

    async def no_refresh(*_args):
        return None

    def no_memory_refresh(context, _memories):
        return context, False

    async def no_memories(*_args):
        return []

    async def read_session(_db, session_row):
        return session_row

    monkeypatch.setattr(turn_service, "refresh_trip_snapshot", no_refresh)
    monkeypatch.setattr(turn_service, "sync_personal_memory_snapshot", no_memory_refresh)
    monkeypatch.setattr(turn_service, "_active_personal_memories", no_memories)
    monkeypatch.setattr(turn_service, "read_session", read_session)
    monkeypatch.setattr(turn_service.settings, "llm_api_key", "")
    monkeypatch.setattr(turn_service.settings, "llm_model", "")
    monkeypatch.setattr(turn_service.settings, "maps_provider", "amap")
    maps, db = Maps(), Database()

    result = await execute_advisor_turn(
        db, row, "我更喜欢历史街区", maps, None, None, user,
    )

    assert result is row
    assert len(maps.calls) == 6
    assert {keyword for _city, keyword in maps.calls} == {"历史古迹"}
    user_message = next(item for item in db.rows if isinstance(item, AdvisorMessageModel) and item.role == "user")
    assistant_message = next(item for item in db.rows if isinstance(item, AdvisorMessageModel) and item.role == "assistant")
    assert user_message.content == "我更喜欢历史街区"
    assert assistant_message.payload["agent_task"]["task_id"] == "destination-dispatch-1"
    assert assistant_message.payload["agent_task"]["answers"] == ["我更喜欢历史街区"]


@pytest.mark.asyncio
async def test_open_city_recommendation_chat_starts_destination_agent(monkeypatch):
    owner_id, session_id = uuid4(), uuid4()
    row = SimpleNamespace(
        id=session_id, owner_id=owner_id, status="ACTIVE", mode="discover",
        trip_id=None, destination="", start_date=None, end_date=None,
        budget=None, pace_level=3, preference_text="",
        candidates=[],
        context={"revision": 0, "planning_inputs": {"hard_constraints": []}},
        updated_at=None,
    )
    user = SimpleNamespace(id=owner_id)

    class Database:
        def __init__(self):
            self.rows = []

        def add(self, item):
            self.rows.append(item)

        async def scalars(self, _statement):
            return SimpleNamespace(all=list)

    async def no_refresh(*_args):
        return None

    def no_memory_refresh(context, _memories):
        return context, False

    async def no_memories(*_args):
        return []

    async def no_model_extraction(*_args, **_kwargs):
        return Extraction(places=[], intent="recommend"), False

    called = {}

    async def start_destination(**kwargs):
        called.update(kwargs)
        return row

    monkeypatch.setattr(turn_service, "refresh_trip_snapshot", no_refresh)
    monkeypatch.setattr(turn_service, "sync_personal_memory_snapshot", no_memory_refresh)
    monkeypatch.setattr(turn_service, "_active_personal_memories", no_memories)
    monkeypatch.setattr(turn_service, "_extract_advisor_intent", no_model_extraction)
    monkeypatch.setattr(turn_service, "start_destination_recommendation_from_chat", start_destination)
    monkeypatch.setattr(turn_service.settings, "llm_api_key", "")
    monkeypatch.setattr(turn_service.settings, "llm_model", "")
    db = Database()

    result = await execute_advisor_turn(
        db, row, "推荐几个适合周末去的城市", Maps(), None, None, user,
    )

    assert result is row
    assert called["user_goal"] == "推荐几个适合周末去的城市"
    assert called["idempotency_key"] is None
    assert called["output_token_budget"] >= 0
    assert row.destination == ""
    assert row.trip_id is None
    assert len([item for item in db.rows if isinstance(item, AdvisorMessageModel) and item.role == "user"]) == 1


@pytest.mark.asyncio
async def test_chat_destination_agent_returns_workbench_payload_without_trip_writes(monkeypatch):
    owner_id, session_id = uuid4(), uuid4()
    row = SimpleNamespace(
        id=session_id, owner_id=owner_id, trip_id=None, mode="discover",
        destination="", start_date=None, end_date=None, preference_text="",
        context={"revision": 0, "planning_inputs": {"hard_constraints": []}},
        updated_at=None,
    )
    user = SimpleNamespace(id=owner_id)
    db = SimpleNamespace(rows=[])
    db.add = db.rows.append

    async def commit():
        return None

    async def refresh(_row):
        return None

    db.commit = commit
    db.refresh = refresh
    decisions = []

    async def no_evidence(**_kwargs):
        return {}, None

    async def choose_destination(observation, schemas, **_kwargs):
        assert set(schemas) == {"search_destination_places"}
        assert observation["approved_cities"] == list(_DESTINATION_CITIES)
        assert observation["approved_interests"] == ["自然与公园"]
        decisions.append(observation)
        if len(decisions) == 1:
            return AgentAction(
                kind="CALL_TOOL", tool="search_destination_places",
                arguments={"city": "杭州", "interest_index": 0},
                summary="先查杭州的自然地点",
            ), SimpleNamespace(prompt_tokens=12, completion_tokens=5, total_tokens=17)
        assert observation["observed_searches"] == [
            {"city": "杭州", "interest": "自然与公园", "status": "VERIFIED", "result_count": 1},
        ]
        return AgentAction(
            kind="FINISH", summary="已获得杭州的地图候选",
        ), SimpleNamespace(prompt_tokens=13, completion_tokens=4, total_tokens=17)

    async def read_session(_db, session_row):
        return session_row

    maps = Maps()

    monkeypatch.setattr("travelpilot_api.advisor.destination_service.settings.maps_provider", "amap")
    monkeypatch.setattr("travelpilot_api.advisor.destination_service.settings.llm_api_key", "test-key")
    monkeypatch.setattr("travelpilot_api.advisor.destination_service.settings.llm_model", "test-model")
    monkeypatch.setattr("travelpilot_api.advisor.destination_service.settings.llm_base_url", "http://model.invalid/v1")
    monkeypatch.setattr("travelpilot_api.advisor.destination_service._prioritized_destination_queries",
                        lambda *_args: [("自然与公园", "公园")])
    monkeypatch.setattr("travelpilot_api.advisor.destination_service.destination_evidence_callbacks", no_evidence)
    monkeypatch.setattr("travelpilot_api.advisor.destination_discovery.decide_next_action", choose_destination)

    result = await start_destination_recommendation_from_chat(
        db=db, row=row, user=user, maps=maps,
        user_goal="帮我推荐适合周末放松的旅行目的地",
        idempotency_key=None, output_token_budget=128,
        session_reader=read_session,
    )

    assistant = next(item for item in db.rows if isinstance(item, AdvisorMessageModel))
    assert result is row
    assert maps.calls == [("杭州", "公园")]
    assert len(decisions) == 2
    assert assistant.payload["event"] == "DESTINATION_RECOMMENDATIONS"
    assert assistant.payload["recommendations"][0]["city"] == "杭州"
    assert assistant.payload["recommendations"][0]["places"][0]["name"] == "杭州公园"
    assert assistant.payload["agent_trace"]["stage_statuses"] == [
        {"stage": "SEARCH_WEATHER", "status": "SUCCEEDED"},
    ]
    assert assistant.payload["agent_trace"]["decision_source"] == "MODEL"
    assert row.trip_id is None
    assert {type(item) for item in db.rows} == {AdvisorMessageModel, AdvisorRunEventModel}


@pytest.mark.asyncio
@pytest.mark.parametrize("composition_error", [None, "NOT_CONFIGURED"])
async def test_open_ended_trip_question_uses_trip_context_without_searching(
    monkeypatch, composition_error,
):
    owner_id, session_id, trip_id = uuid4(), uuid4(), uuid4()
    row = SimpleNamespace(
        id=session_id, owner_id=owner_id, status="ACTIVE", mode="existing",
        trip_id=trip_id, destination="西安", start_date=date(2026, 11, 2),
        end_date=date(2026, 11, 4), budget=3000, pace_level=2,
        preference_text="喜欢历史文化，节奏轻松", candidates=[{
            "id": "candidate-1", "name": "陕西历史博物馆",
            "planning_category": "ATTRACTION", "in_current_plan": True,
        }],
        context={
            "revision": 1, "planning_inputs": {"hard_constraints": []},
            "current_plan": {"stops": [{
                "candidate_id": "candidate-1", "name": "陕西历史博物馆",
                "day": "2026-11-02", "start_time": "10:00",
            }]},
            "selected_candidate_ids": ["candidate-1"],
        },
        updated_at=None,
    )
    user = SimpleNamespace(id=owner_id)

    class Database:
        def __init__(self):
            self.rows = []

        def add(self, item):
            self.rows.append(item)

        async def scalars(self, _statement):
            return SimpleNamespace(all=list)

        async def commit(self):
            return None

        async def refresh(self, _row):
            return None

    class NoSearchMaps:
        async def search_place(self, *_args):
            raise AssertionError("open-ended advice should not initiate a POI search")

    async def no_refresh(*_args):
        return None

    def no_memory_refresh(context, _memories):
        return context, False

    async def no_memories(*_args):
        return []

    async def no_model_extraction(*_args, **_kwargs):
        return Extraction(places=[], intent="general"), False

    captured_facts = {}

    async def compose_general_advice(facts, **_kwargs):
        captured_facts.update(facts)
        if composition_error:
            return SimpleNamespace(
                reply=None, total_tokens=0, output_tokens=0,
                error_code=composition_error,
            )
        return SimpleNamespace(
            reply=SimpleNamespace(
                reply="这是基于行程上下文的一般建议：博物馆参观后安排休息，并在出发前核对开放信息。",
                follow_up_question="需要我再检查每天的休息空档吗？",
                memory_suggestion=None,
            ),
            total_tokens=18, output_tokens=10, error_code=None,
        )

    async def read_session(_db, session_row):
        return session_row

    monkeypatch.setattr(turn_service, "refresh_trip_snapshot", no_refresh)
    monkeypatch.setattr(turn_service, "sync_personal_memory_snapshot", no_memory_refresh)
    monkeypatch.setattr(turn_service, "_active_personal_memories", no_memories)
    monkeypatch.setattr(turn_service, "_extract_advisor_intent", no_model_extraction)
    monkeypatch.setattr(turn_service, "compose_advisor_reply_with_diagnostics", compose_general_advice)
    monkeypatch.setattr(turn_service, "read_session", read_session)
    monkeypatch.setattr(turn_service.settings, "llm_api_key", "")
    monkeypatch.setattr(turn_service.settings, "llm_model", "")
    db = Database()

    result = await execute_advisor_turn(
        db, row, "第一次去西安旅行，有什么实用建议？", NoSearchMaps(), None, None, user,
    )

    assistant = next(
        item for item in reversed(db.rows)
        if isinstance(item, AdvisorMessageModel) and item.role == "assistant"
    )
    assert result is row
    assert captured_facts["current_message"] == "第一次去西安旅行，有什么实用建议？"
    assert captured_facts["destination"] == "西安"
    assert captured_facts["current_plan"]["stops"][0]["name"] == "陕西历史博物馆"
    assert "已核对开放信息" not in assistant.content
    if composition_error:
        assert "天气准备" in assistant.content
        assert "未配置智能回答模型" in assistant.content
        assert "请告诉我具体地点名称" not in assistant.content
        assert assistant.payload["response_source"] == "TEMPLATE"
    else:
        assert "一般建议" in assistant.content
        assert assistant.payload["response_source"] == "MODEL"
    assert assistant.payload["agent_task"]["task_type"] == "GENERAL"
    assert row.candidates[0]["name"] == "陕西历史博物馆"


@pytest.mark.asyncio
async def test_destination_question_answer_runs_one_continuous_agent_task(monkeypatch):
    owner_id, session_id = uuid4(), uuid4()
    row = SimpleNamespace(
        id=session_id, owner_id=owner_id, status="ACTIVE", mode="discover", trip_id=None,
        destination="", start_date=None, end_date=None, budget=None, pace_level=3,
        preference_text="", context={"revision": 0, "planning_inputs": {"hard_constraints": []}},
        updated_at=None,
    )
    user = SimpleNamespace(id=owner_id)

    class Database:
        def __init__(self):
            self.rows = []

        def add(self, item):
            self.rows.append(item)

        async def scalars(self, _statement):
            assistants = [item for item in self.rows if getattr(item, "role", None) == "assistant"]
            return SimpleNamespace(all=lambda: assistants[-1:])

        async def scalar(self, _statement):
            return None

        async def commit(self):
            pass

        async def refresh(self, _row):
            pass

    class Redis:
        pass

    db, maps = Database(), Maps()
    decision_count = 0
    checkpoint_writers = []

    def checkpoint_writer_stub(**kwargs):
        checkpoint_writers.append(kwargs)

        async def write(_state):
            return None

        return write

    async def decide(observation, schemas, **_kwargs):
        nonlocal decision_count
        decision_count += 1
        assert set(schemas) == {"search_destination_places"}
        assert observation["approved_cities"] == ["泉州"]
        if decision_count == 1:
            return AgentAction(
                kind="ASK_USER", question="你更喜欢历史文化还是海滨休闲？",
            ), SimpleNamespace(prompt_tokens=5, completion_tokens=6, total_tokens=11)
        if decision_count == 2:
            assert "都可以" in observation["user_goal"]
            assert observation["approved_interests"] == ["自然与公园", "地方美食"]
            return AgentAction(
                kind="ASK_USER", question="如果先选一种方向，你更想看自然风景还是历史街区？",
            ), SimpleNamespace(prompt_tokens=6, completion_tokens=7, total_tokens=13)
        assert "都可以" in observation["user_goal"]
        assert "我更喜欢历史街区" in observation["user_goal"]
        assert "历史街区" in observation["user_goal"]
        if decision_count == 3:
            assert observation["approved_interests"] == ["历史街区"]
            assert observation["approved_cities"] == ["泉州"]
            return AgentAction(
                kind="CALL_TOOL", tool="search_destination_places",
                arguments={"city": "泉州", "interest_index": 0},
                summary="按历史街区偏好先核验泉州候选",
            ), SimpleNamespace(prompt_tokens=7, completion_tokens=5, total_tokens=12)
        assert observation["observed_searches"] == [{
            "city": "泉州", "interest": "历史街区", "status": "VERIFIED", "result_count": 1,
        }]
        return AgentAction(kind="FINISH", summary="已有匹配的地图证据"), SimpleNamespace(
            prompt_tokens=4, completion_tokens=3, total_tokens=7,
        )

    async def owned_session(_db, _session_id, _owner_id, **_kwargs):
        return row

    async def no_rate_limit(*_args, **_kwargs):
        return None

    async def read_session(_db, session_row):
        return session_row

    async def no_refresh(*_args):
        return None

    def no_memory_refresh(context, _memories):
        return context, False

    async def no_memories(*_args):
        return []

    monkeypatch.setattr(advisor_routes, "owned_session", owned_session)
    monkeypatch.setattr(advisor_routes, "enforce_identity_rate_limit", no_rate_limit)
    monkeypatch.setattr(advisor_routes, "read_session", read_session)
    monkeypatch.setattr(advisor_routes, "checkpoint_writer", checkpoint_writer_stub)
    monkeypatch.setattr(turn_service, "read_session", read_session)
    monkeypatch.setattr(turn_service, "refresh_trip_snapshot", no_refresh)
    monkeypatch.setattr(turn_service, "sync_personal_memory_snapshot", no_memory_refresh)
    monkeypatch.setattr(turn_service, "_active_personal_memories", no_memories)
    monkeypatch.setattr(advisor_routes.settings, "maps_provider", "amap")
    monkeypatch.setattr(turn_service.settings, "maps_provider", "amap")
    for settings_module in (advisor_routes.settings, turn_service.settings):
        monkeypatch.setattr(settings_module, "llm_api_key", "configured")
        monkeypatch.setattr(settings_module, "llm_model", "test-model")
        monkeypatch.setattr(settings_module, "llm_base_url", "https://model.invalid/v1")
    monkeypatch.setattr(
        "travelpilot_api.advisor.destination_discovery.decide_next_action", decide,
    )

    await advisor_routes.recommend_destinations(
        session_id=session_id, db=db, user=user, maps=maps, redis=Redis(),
        idempotency_key="destination-case-0001",
        payload=AdvisorDestinationDiscoveryRequest(city="泉州"),
    )
    assert {
        (item["phase"], item["task_id"]) for item in checkpoint_writers
    } == {
        ("destination_discovery", "destination-case-0001"),
        ("turn_stage", "destination:destination-case-0001"),
    }
    first_message = next(
        item for item in reversed(db.rows)
        if isinstance(item, AdvisorMessageModel) and item.role == "assistant"
    )
    pending = pending_agent_question(first_message.payload)
    assert pending is not None
    assert pending.task_kind == "DESTINATION_RECOMMENDATION"
    assert pending.turn_number == 1
    assert maps.calls == []
    assert first_message.payload["agent_trace"]["status"] == "NEEDS_USER_INPUT"
    assert first_message.payload["agent_trace"]["stage_statuses"] == [
        {"stage": "SEARCH_WEATHER", "status": "NEEDS_USER_INPUT"},
    ]
    assert first_message.payload["stage_execution"] == {
        "order": ["SEARCH_WEATHER"],
        "statuses": {"SEARCH_WEATHER": "NEEDS_USER_INPUT"},
    }

    second = await execute_advisor_turn(
        db, row, "都可以", maps, None, None, user,
    )
    second_message = next(
        item for item in reversed(db.rows)
        if isinstance(item, AdvisorMessageModel) and item.role == "assistant"
    )
    pending_second = pending_agent_question(second_message.payload)
    assert pending_second is not None
    assert pending_second.task_id == pending.task_id
    assert pending_second.turn_number == 2
    assert pending_second.answers == ("都可以",)
    assert maps.calls == []

    third = await execute_advisor_turn(
        db, row, "我更喜欢历史街区", maps, None, None, user,
    )
    final_message = next(
        item for item in reversed(db.rows)
        if isinstance(item, AdvisorMessageModel) and item.role == "assistant"
    )

    assert second is row and third is row
    assert maps.calls == [("泉州", "历史古迹")]
    assert decision_count == 3
    assert row.preference_text == "都可以\n我更喜欢历史街区"
    assert final_message.payload["agent_task"] == {
        "task_id": pending.task_id,
        "task_type": "DISCOVERY",
        "status": "COMPLETED",
        "goal": pending.task,
        "context_revision": 0,
        "turn_number": 3,
        "answers": ["都可以", "我更喜欢历史街区"],
        "question": None,
        "action_summary": "按历史街区偏好先核验泉州候选",
        "confirmation_options": [],
    }
    assert final_message.payload["agent_trace"]["stage_statuses"] == [
        {"stage": "SEARCH_WEATHER", "status": "SUCCEEDED"},
    ]
    assert final_message.payload["stage_execution"] == {
        "order": ["SEARCH_WEATHER"],
        "statuses": {"SEARCH_WEATHER": "SUCCEEDED"},
    }
    assert final_message.payload["recommendations"][0]["city"] == "泉州"
    assert "destination_discovery_city" not in row.context
    assert final_message.payload["agent_trace"]["transitions"][-1]["tool"] == "search_destination_places"


@pytest.mark.asyncio
async def test_explicit_food_and_attraction_request_searches_both_domains(monkeypatch):
    owner_id, session_id = uuid4(), uuid4()
    row = SimpleNamespace(
        id=session_id, owner_id=owner_id, status="ACTIVE", mode="existing", trip_id=None,
        destination="成都", start_date=None, end_date=None, budget=None, pace_level=3,
        preference_text="", context={"revision": 0, "planning_inputs": {"hard_constraints": []}},
        candidates=[], updated_at=None,
    )
    user = SimpleNamespace(id=owner_id)

    class Database:
        def __init__(self):
            self.rows = []

        def add(self, item):
            self.rows.append(item)

        async def scalars(self, _statement):
            return SimpleNamespace(all=list)

        async def commit(self):
            pass

        async def refresh(self, _row):
            pass

    class CompoundMaps:
        def __init__(self):
            self.calls = []

        async def search_place(self, keyword, city):
            self.calls.append((city, keyword))
            is_food = keyword == "餐饮服务"
            return [Place(
                provider="amap", provider_id=f"{keyword}-1",
                name="成都餐馆" if is_food else "成都博物馆",
                address="成都市测试地址", category="餐饮服务;餐厅" if is_food else "博物馆;展馆",
                latitude=30.0, longitude=104.0, suggested_duration_min=60,
            )]

    async def no_refresh(*_args):
        return None

    def no_memory_refresh(context, _memories):
        return context, False

    async def no_memories(*_args):
        return []

    async def no_model_extraction(*_args, **_kwargs):
        return Extraction(places=[], intent="recommend"), False

    decision_count = 0
    composed_facts = {}

    async def choose_search(observation, schemas, **_kwargs):
        nonlocal decision_count
        decision_count += 1
        assert set(schemas) == {"search_places"}
        if decision_count == 1:
            assert observation["approved_queries"] == ["餐饮服务", "景点"]
            assert observation["observed_searches"] == []
            keyword = "景点"
        else:
            assert observation["observed_searches"] == [{
                "keyword": "景点", "status": "VERIFIED", "result_count": 1,
                "place_names": ["成都博物馆"],
            }]
            keyword = "餐饮服务"
        return AgentAction(
            kind="CALL_TOOL", tool="search_places", arguments={"keyword": keyword},
            summary=f"根据已验证信息继续查询{keyword}",
        ), SimpleNamespace(prompt_tokens=6, completion_tokens=5, total_tokens=11)

    async def read_session(_db, session_row):
        return session_row

    async def compose_from_verified_evidence(facts, **_kwargs):
        composed_facts.update(facts)
        return SimpleNamespace(
            reply=SimpleNamespace(
                reply="我找到了景点和午餐候选；先按你的偏好比较，菜单与价格仍需核实。",
                follow_up_question="你更想吃客家菜还是清淡一些？",
                memory_suggestion=None,
            ),
            total_tokens=18, output_tokens=10, error_code=None,
        )

    monkeypatch.setattr(turn_service, "refresh_trip_snapshot", no_refresh)
    monkeypatch.setattr(turn_service, "sync_personal_memory_snapshot", no_memory_refresh)
    monkeypatch.setattr(turn_service, "_active_personal_memories", no_memories)
    monkeypatch.setattr(turn_service, "_extract_advisor_intent", no_model_extraction)
    monkeypatch.setattr(turn_service, "read_session", read_session)
    monkeypatch.setattr(turn_service, "compose_advisor_reply_with_diagnostics", compose_from_verified_evidence)
    monkeypatch.setattr(turn_service.settings, "llm_api_key", "configured-test-key")
    monkeypatch.setattr(turn_service.settings, "llm_model", "test-model")
    monkeypatch.setattr(turn_service.settings, "llm_base_url", "https://model.invalid/v1")
    monkeypatch.setattr(turn_service.settings, "maps_provider", "amap")
    monkeypatch.setattr("travelpilot_api.advisor.search_loop.decide_next_action", choose_search)
    maps, db = CompoundMaps(), Database()

    result = await execute_advisor_turn(
        db, row, "推荐博物馆和午餐地点", maps, None, None, user,
    )

    assistant_message = next(
        item for item in reversed(db.rows)
        if isinstance(item, AdvisorMessageModel) and item.role == "assistant"
    )
    assert result is row
    assert maps.calls == [("成都", "景点"), ("成都", "餐饮服务")]
    assert {candidate["planning_category"] for candidate in row.candidates} == {"FOOD", "ATTRACTION"}
    assert assistant_message.payload["planning_category"] is None
    assert assistant_message.payload["agent_task"]["task_type"] == "DISCOVERY"
    assert composed_facts["requested_category"] is None
    assert {item["planning_category"] for item in composed_facts["verified_candidates"]} == {"FOOD", "ATTRACTION"}
    assert assistant_message.payload["response_source"] == "MODEL"
    assert "菜单与价格仍需核实" in assistant_message.content
    assert decision_count == 2
    assert assistant_message.payload["agent_trace"]["decision_source"] == "MODEL"
