from datetime import date
from types import SimpleNamespace
from uuid import uuid4

import httpx
import pytest
import travelpilot_api.api.v1.advisor as advisor_api
import travelpilot_core.agents.extraction as extraction_module
from travelpilot_api.api.v1.advisor import (
    _allowed_interest_queries,
    _destination_queries,
    _extract_advisor_intent,
    _hard_limit_conflicts,
    _planning_state,
    advisor_runtime_status,
    recommend_destinations,
)
from travelpilot_api.schemas.advisor import AdvisorSessionContextUpdate, AdvisorSessionCreate
from travelpilot_api.settings import settings
from travelpilot_core.agents.extraction import extract_places, fallback_extract
from travelpilot_core.ports.maps import Place
from travelpilot_core.skills import (
    available_skills,
    check_skill_applicability,
    load_skill,
    select_skill,
    select_skill_decision,
)
from travelpilot_infra.providers.maps.errors import MapsProviderError


class RateLimitRedis:
    def __init__(self):
        self.calls = []

    async def eval(self, _script, _key_count, key, _ttl):
        self.calls.append(key)
        return 1


@pytest.mark.asyncio
async def test_runtime_status_exposes_configuration_without_credentials(monkeypatch):
    monkeypatch.setattr(settings, "llm_api_key", "secret-test-key")
    monkeypatch.setattr(settings, "llm_model", "safe-model-label")
    monkeypatch.setattr(settings, "llm_max_tokens_per_turn", 1200)

    status = await advisor_runtime_status(_user=object())

    assert status.llm_status == "CONFIGURED"
    assert status.model_name == "safe-model-label"
    assert status.output_token_limit == 1200
    assert "secret-test-key" not in status.model_dump_json()


def test_fallback_extracts_explicit_interest_searches():
    result = fallback_extract("我想吃川菜，也想逛博物馆")

    assert result.search_queries == ["川菜", "博物馆"]
    assert not result.places


@pytest.mark.asyncio
async def test_model_extraction_failure_preserves_explicit_place_with_local_fallback(monkeypatch):
    captured = {}

    async def fail_model(*_args, **kwargs):
        captured.update(kwargs)
        raise RuntimeError("TEXT_EXTRACTION_FAILED")

    monkeypatch.setattr(advisor_api, "extract_places", fail_model)
    monkeypatch.setattr(settings, "llm_api_key", "configured-test-key")
    monkeypatch.setattr(settings, "llm_model", "test-model")
    monkeypatch.setattr(settings, "llm_max_tokens_per_turn", 1200)

    result, used_fallback = await _extract_advisor_intent("我想去宽窄巷子")

    assert used_fallback is True
    assert [place.name for place in result.places] == ["宽窄巷子"]
    assert captured["token_budget"] == 800


@pytest.mark.asyncio
async def test_missing_model_key_uses_local_extraction_without_calling_provider(monkeypatch):
    class UnexpectedClient:
        def __init__(self, **_kwargs):
            raise AssertionError("an unconfigured model must not call the provider")

    monkeypatch.setattr(extraction_module.httpx, "AsyncClient", UnexpectedClient)
    monkeypatch.setattr(settings, "llm_api_key", "")
    monkeypatch.setattr(settings, "llm_model", "configured-label-without-key")

    result, used_fallback = await _extract_advisor_intent("我想去宽窄巷子")

    assert used_fallback is False
    assert [place.name for place in result.places] == ["宽窄巷子"]


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["bad_key", "malformed_json", "timeout"])
async def test_model_extraction_failures_return_a_safe_local_fallback(monkeypatch, failure):
    class Response:
        def raise_for_status(self):
            if failure == "bad_key":
                request = httpx.Request("POST", "https://model.invalid/chat/completions")
                response = httpx.Response(401, request=request)
                raise httpx.HTTPStatusError("unauthorized", request=request, response=response)

        def json(self):
            return {"choices": [{"message": {"content": "not-json"}}]}

    class Client:
        def __init__(self, **_kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return False

        async def post(self, *_args, **_kwargs):
            if failure == "timeout":
                raise httpx.ReadTimeout("upstream timed out")
            return Response()

    monkeypatch.setattr("travelpilot_core.agents.extraction.httpx.AsyncClient", Client)
    with pytest.raises(RuntimeError, match="TEXT_EXTRACTION_FAILED"):
        await extract_places(
            "我想去宽窄巷子", api_key="test-key", model="test-model",
            base_url="https://model.invalid/v1", token_budget=800,
        )
    local = fallback_extract("我想去宽窄巷子")
    assert [place.name for place in local.places] == ["宽窄巷子"]


def test_fallback_does_not_recommend_a_rejected_interest():
    result = fallback_extract("不喜欢博物馆，想找咖啡馆")

    assert "博物馆" not in result.search_queries
    assert "咖啡馆" in result.search_queries


def test_destination_queries_are_bounded_and_remove_explicitly_rejected_interests():
    queries = _destination_queries(
        "喜欢自然、历史和美食，不喜欢历史古迹",
        ["不要博物馆"],
    )

    assert queries == [("自然与公园", "公园"), ("地方美食", "美食")]
    assert len(queries) <= 2


def test_destination_queries_need_an_explicit_supported_preference():
    assert _destination_queries("想去一个舒服的地方", []) == []


@pytest.mark.asyncio
async def test_destination_recommendations_use_live_map_evidence_and_are_bounded(monkeypatch):
    row = SimpleNamespace(
        id=uuid4(), status="ACTIVE", destination="", start_date=date(2026, 11, 7), end_date=date(2026, 11, 8),
        preference_text="喜欢自然和美食", context={"planning_inputs": {"origin": "上海", "hard_constraints": []}},
        updated_at=None,
    )

    async def owned(*_args, **_kwargs):
        return row

    async def read(_db, session_row):
        return session_row

    class Database:
        def __init__(self):
            self.rows = []

        def add(self, item):
            self.rows.append(item)

        async def commit(self):
            pass

        async def refresh(self, _row):
            pass

    class Maps:
        def __init__(self):
            self.calls = []

        async def search_place(self, keyword, city):
            self.calls.append((keyword, city))
            count = 2 if city == "成都" else 1
            return [Place("amap", f"{city}-{keyword}-{index}", f"{city}{keyword}地点{index}", "测试地址", keyword,
                          30.0, 104.0) for index in range(count)]

    monkeypatch.setattr(advisor_api, "_owned_session", owned)
    monkeypatch.setattr(advisor_api, "_read_session", read)
    monkeypatch.setattr(settings, "maps_provider", "amap")
    db, maps = Database(), Maps()

    redis = RateLimitRedis()
    user = SimpleNamespace(id=uuid4())
    await recommend_destinations(uuid4(), db=db, user=user, maps=maps, redis=redis)

    stored = next(item for item in db.rows if isinstance(item, advisor_api.AdvisorMessageModel))
    recommendations = stored.payload["recommendations"]
    assert len(maps.calls) == 12
    assert len(recommendations) == 3
    assert recommendations[0]["city"] == "成都" and recommendations[0]["place_count"] == 4
    assert all(item["provider"] == "amap" and item["places"] for item in recommendations)
    assert all(place["provider"] == "amap" for item in recommendations for place in item["places"])
    assert len(redis.calls) == 1 and "advisor-destinations" in redis.calls[0]


@pytest.mark.asyncio
async def test_destination_recommendations_refuse_simulated_map_provider(monkeypatch):
    row = SimpleNamespace(
        id=uuid4(), status="ACTIVE", destination="", start_date=date(2026, 11, 7), end_date=date(2026, 11, 8),
        preference_text="喜欢自然", context={"planning_inputs": {"origin": "上海", "hard_constraints": []}},
    )

    async def owned(*_args, **_kwargs):
        return row

    class Maps:
        async def search_place(self, *_args):
            raise AssertionError("simulated map mode must not be queried for recommendations")

    monkeypatch.setattr(advisor_api, "_owned_session", owned)
    monkeypatch.setattr(settings, "maps_provider", "fake")
    with pytest.raises(advisor_api.HTTPException) as exc:
        await recommend_destinations(uuid4(), db=object(), user=SimpleNamespace(id=uuid4()), maps=Maps(), redis=RateLimitRedis())
    assert exc.value.status_code == 503


@pytest.mark.asyncio
async def test_destination_recommendations_keep_partial_results_and_report_provider_failures(monkeypatch):
    row = SimpleNamespace(
        id=uuid4(), status="ACTIVE", destination="", start_date=date(2026, 11, 7), end_date=date(2026, 11, 8),
        preference_text="喜欢自然和美食", context={"planning_inputs": {"origin": "上海", "hard_constraints": []}},
        updated_at=None,
    )

    async def owned(*_args, **_kwargs):
        return row

    async def read(_db, session_row):
        return session_row

    class Database:
        def __init__(self):
            self.rows = []

        def add(self, item):
            self.rows.append(item)

        async def commit(self):
            pass

        async def refresh(self, _row):
            pass

    class Maps:
        async def search_place(self, keyword, city):
            if city in {"成都", "杭州"}:
                raise MapsProviderError("simulated upstream error")
            return [Place("amap", f"{city}-{keyword}", f"{city}{keyword}地点", "测试地址", keyword,
                          30.0, 104.0)]

    monkeypatch.setattr(advisor_api, "_owned_session", owned)
    monkeypatch.setattr(advisor_api, "_read_session", read)
    monkeypatch.setattr(settings, "maps_provider", "amap")
    db = Database()

    await recommend_destinations(uuid4(), db=db, user=SimpleNamespace(id=uuid4()), maps=Maps(), redis=RateLimitRedis())

    stored = next(item for item in db.rows if isinstance(item, advisor_api.AdvisorMessageModel))
    assert stored.payload["failed_search_count"] == 4
    assert stored.payload["recommendations"]
    assert "4 项地图搜索暂时失败" in stored.content


def test_discover_session_can_start_without_destination_or_dates():
    session = AdvisorSessionCreate(mode="discover", preference_text="喜欢自然和美食")

    assert session.destination == ""
    assert session.start_date is None and session.end_date is None


def test_context_update_requires_dates_as_a_pair():
    try:
        AdvisorSessionContextUpdate(destination="成都", start_date="2026-11-01")
    except ValueError as exc:
        assert "provided together" in str(exc)
    else:
        raise AssertionError("a partial date range must be rejected")


def test_structured_planning_inputs_are_bounded():
    session = AdvisorSessionCreate(
        mode="discover", origin="成都东站", companions=["儿童"],
        hard_constraints=["不去博物馆"],
    )
    assert session.origin == "成都东站"
    assert session.companions == ["儿童"]
    assert session.hard_constraints == ["不去博物馆"]


def test_planning_state_exposes_missing_slots_and_questions():
    state = _planning_state(SimpleNamespace(
        trip_id=None, context={}, destination="", start_date=None, end_date=None,
        budget=None, pace_level=3, preference_text="",
    ))

    assert state["missing_required"] == ["destination", "start_date", "end_date"]
    assert [item["slot"] for item in state["unresolved_questions"]] == state["missing_required"]


def test_planning_state_tracks_origin_companions_and_limits_with_provenance():
    state = _planning_state(SimpleNamespace(
        trip_id=None,
        context={"planning_inputs": {"origin": "成都东站", "companions": ["儿童"],
                                     "hard_constraints": ["不去博物馆"]},
                 "slot_sources": {"origin": "advisor_form", "companions": "advisor_form",
                                  "hard_constraints": "advisor_form"}},
        destination="成都", start_date=None, end_date=None, budget=None,
        pace_level=3, preference_text="",
    ))
    assert state["slots"]["origin"]["value"] == "成都东站"
    assert state["slots"]["companions"]["source"] == "advisor_form"
    assert state["slots"]["hard_constraints"]["value"] == ["不去博物馆"]


def test_model_search_queries_cannot_override_explicitly_rejected_category():
    queries = _allowed_interest_queries("不喜欢博物馆，但喜欢咖啡", ["博物馆", "咖啡馆", "艺术馆"])

    assert queries == ["咖啡馆"]


def test_curated_skills_load_only_for_matching_tasks():
    assert select_skill("chat", "成都天气不错") is None
    assert select_skill("content_import") is None
    unmatched = select_skill_decision("chat", "成都天气不错")
    assert unmatched.skill is None and unmatched.applicability.reason_code == "NO_MATCH"
    content_skill = select_skill(
        "content_import",
        context={"task": "content_import", "user_content": True},
    )
    assert content_skill is not None and content_skill.name == "攻略导入"
    weather_skill = load_skill("weather-replan")
    assert weather_skill.version == "1.0.0"
    rejected = check_skill_applicability(weather_skill, {
        "task": "weather_replan", "verified_weather": False, "simulated_weather": False,
    })
    assert rejected.reason_code == "MISSING_REQUIREMENT"
    assert rejected.missing_requirements == ("verified_weather",)
    assert select_skill("weather_replan", context={
        "task": "weather_replan", "verified_weather": True, "simulated_weather": False,
    }) is not None
    assert {item["name"] for item in available_skills()} == {"攻略导入", "天气变化重排"}
    try:
        load_skill("../../outside")
    except ValueError:
        pass
    else:
        raise AssertionError("unreviewed skill paths must be rejected")


def test_confirmed_hard_limits_block_known_category_conflicts():
    memory = {"constraint_type": "HARD_LIMIT", "text": "不去博物馆"}
    assert _hard_limit_conflicts({"name": "市立博物馆", "category": "MUSEUM"}, [memory])
    assert not _hard_limit_conflicts({"name": "人民公园", "category": "PARK"}, [memory])


def test_food_safety_hard_limits_are_reported_for_manual_verification():
    memory = {"constraint_type": "HARD_LIMIT", "text": "花生过敏"}
    assert not _hard_limit_conflicts({"name": "川菜馆", "category": "RESTAURANT"}, [memory])
