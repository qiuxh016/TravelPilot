from datetime import date
from types import SimpleNamespace
from uuid import uuid4

import httpx
import pytest
import travelpilot_api.advisor.interpretation as advisor_interpretation
import travelpilot_api.api.v1.advisor as advisor_api
import travelpilot_core.agents.extraction as extraction_module
from pydantic import ValidationError
from travelpilot_api.advisor.context import planning_state as _planning_state
from travelpilot_api.advisor.interpretation import (
    _allowed_interest_queries,
    _broad_recommendation_queries,
    _destination_queries,
    _explicit_compound_planning_categories,
    _extract_advisor_intent,
    _prioritized_destination_queries,
    event_replan_prefill,
    event_replan_request_type,
    should_use_deterministic_schedule_extraction,
)
from travelpilot_api.advisor.policies import (
    annotate_profile_memory_conflicts,
    classify_draft_memory_conflicts,
    overridden_profile_memory_texts,
)
from travelpilot_api.advisor.policies import (
    hard_limit_conflicts as _hard_limit_conflicts,
)
from travelpilot_api.api.v1.advisor import (
    advisor_runtime_status,
    recommend_destinations,
)
from travelpilot_api.schemas.advisor import (
    AdvisorDestinationDiscoveryRequest,
    AdvisorSessionContextUpdate,
    AdvisorSessionCreate,
)
from travelpilot_api.schemas.events import EventCreate
from travelpilot_api.settings import settings
from travelpilot_core.agents.extraction import (
    Extraction,
    PlaceMention,
    extract_places,
    fallback_extract,
)
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


@pytest.mark.parametrize(
    ("message", "expected"),
    [
        ("我今天有点累，帮我把后面的安排放松些", "USER_FATIGUE"),
        ("突然下雨了，帮我调整后续行程", "WEATHER_CHANGE"),
        ("路上堵车晚了半小时，重新安排后面的计划", "TIME_DELAY"),
        ("博物馆临时闭馆了，帮我重新安排后面的行程", "POI_CANCELLED"),
        ("我还想临时加上杜甫草堂，帮我安排到后面行程", "USER_ADD"),
        ("我的预约时间变了，帮我调整这段行程", "RESERVATION_CHANGE"),
        ("明天会下雨吗，要带伞吗", None),
        ("今天有点累", None),
    ],
)
def test_event_replan_request_type_requires_an_explicit_change_request(message, expected):
    assert event_replan_request_type(message) == expected


@pytest.mark.parametrize(
    ("message", "event_type", "expected"),
    [
        ("突然下雨了，帮我调整后续行程", "WEATHER_CHANGE", {"condition": "RAIN"}),
        ("天气突变，调整后面安排", "WEATHER_CHANGE", {}),
        ("堵车晚了半小时，重新安排后面的计划", "TIME_DELAY", {"delay_min": 30}),
        ("延误了240分钟，调整行程", "TIME_DELAY", {}),
    ],
)
def test_event_replan_prefill_contains_only_explicit_bounded_facts(message, event_type, expected):
    assert event_replan_prefill(message, event_type) == expected


@pytest.mark.parametrize(
    "payload",
    [
        {"poi_id": "c6c286a7-8a33-4fe1-9a52-37a2c992163a", "opening_hours": {"open": "09:00", "close": "20:00"}},
        {"poi_id": "c6c286a7-8a33-4fe1-9a52-37a2c992163a", "opening_hours": {"open": "09:00", "close": "09:00"}},
        {"poi_id": "c6c286a7-8a33-4fe1-9a52-37a2c992163a"},
    ],
)
def test_user_add_event_requires_explicit_valid_opening_hours(payload):
    from datetime import UTC, datetime

    if payload.get("opening_hours", {}).get("close") == "20:00":
        event = EventCreate(event_type="USER_ADD", effective_at=datetime.now(UTC), payload=payload)
        assert event.payload["opening_hours"]["open"] == "09:00"
    elif "opening_hours" not in payload:
        event = EventCreate(event_type="USER_ADD", effective_at=datetime.now(UTC), payload=payload)
        assert "opening_hours" not in event.payload
    else:
        with pytest.raises(ValidationError):
            EventCreate(event_type="USER_ADD", effective_at=datetime.now(UTC), payload=payload)


@pytest.mark.asyncio
async def test_runtime_status_exposes_configuration_without_credentials(monkeypatch):
    monkeypatch.setattr(settings, "llm_api_key", "secret-test-key")
    monkeypatch.setattr(settings, "llm_model", "safe-model-label")
    monkeypatch.setattr(settings, "llm_max_tokens_per_turn", 1200)
    monkeypatch.setattr(settings, "llm_max_output_tokens_per_session", 12000)

    status = await advisor_runtime_status(_user=object())

    assert status.llm_status == "CONFIGURED"
    assert status.model_name == "safe-model-label"
    assert status.output_token_limit == 1200
    assert status.session_output_token_limit == 12000
    assert "secret-test-key" not in status.model_dump_json()


def test_fallback_extracts_explicit_interest_searches():
    result = fallback_extract("我想吃川菜，也想逛博物馆")

    assert result.search_queries == ["川菜", "博物馆"]
    assert not result.places


@pytest.mark.parametrize(
    ("message", "expected"),
    [
        ("推荐博物馆和午餐地点", ("FOOD", "ATTRACTION")),
        ("帮我找酒店、餐厅和景点", ("FOOD", "LODGING", "ATTRACTION")),
        ("推荐午餐地点", ()),
        ("附近有什么景点", ()),
        ("不想逛景点，推荐午餐", ()),
    ],
)
def test_compound_category_parser_requires_multiple_explicit_domains(message, expected):
    assert _explicit_compound_planning_categories(message) == expected


@pytest.mark.parametrize(
    ("message", "suggested", "expected"),
    [
        ("推荐一些地方，不要美食", [], ["景点"]),
        ("推荐一些地方，不想逛景点", [], ["美食"]),
        ("推荐一些地方，不想逛博物馆", ["历史景点"], ["历史景点", "景点", "美食"]),
        ("推荐一些地方", ["历史景点"], ["历史景点", "景点", "美食"]),
        ("推荐一些地方，不要美食但想看古建筑", ["历史景点"], ["历史景点", "景点"]),
    ],
)
def test_broad_recommendation_defaults_respect_current_turn_negation(message, suggested, expected):
    assert _broad_recommendation_queries(message, suggested) == expected


@pytest.mark.asyncio
async def test_model_extraction_failure_preserves_explicit_place_with_local_fallback(monkeypatch):
    captured = {}

    async def fail_model(*_args, **kwargs):
        captured.update(kwargs)
        raise RuntimeError("TEXT_EXTRACTION_FAILED")

    monkeypatch.setattr(advisor_interpretation, "extract_places", fail_model)
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
async def test_only_confirmed_soft_memories_are_forwarded_as_search_preferences(monkeypatch):
    captured = {}

    async def inspect_model(*_args, **kwargs):
        captured.update(kwargs)
        raise RuntimeError("TEXT_EXTRACTION_FAILED")

    monkeypatch.setattr(advisor_interpretation, "extract_places", inspect_model)
    monkeypatch.setattr(settings, "llm_api_key", "configured-test-key")
    monkeypatch.setattr(settings, "llm_model", "test-model")

    await _extract_advisor_intent(
        "推荐一些地点",
        conversation_context=[
            {"role": "assistant", "content": "要我按日期帮你排一下游览顺序吗？"},
            {"role": "user", "content": "我希望每天轻松一点。"},
        ],
        personal_memories=[
            {"constraint_type": "PREFERENCE", "text": "喜欢历史文化"},
            {"constraint_type": "HARD_LIMIT", "text": "不去酒吧"},
        ],
    )

    assert captured["travel_preferences"] == ["喜欢历史文化"]
    assert captured["conversation_context"][0]["role"] == "assistant"
    assert "current_message names a different category" in captured["additional_instructions"]


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


@pytest.mark.parametrize(
    ("message", "should_schedule"),
    [
        ("我今天有点累，帮我把后面的安排放松些", True),
        ("我今天很累", False),
        ("我不喜欢太赶的旅行", False),
        ("帮我把收藏的地点安排成两日行程", True),
        ("三处都安排，按轻松节奏分两天排并留出午餐时间", True),
        ("请规划一份3天行程", True),
        ("请把午餐安排在十二点", False),
    ],
)
def test_schedule_intent_recognizes_natural_itinerary_phrasing_without_meal_time_false_positive(
    message, should_schedule,
):
    assert advisor_interpretation._is_schedule_recommendation_request(message) is should_schedule


def test_explicit_fatigue_adjustment_changes_pace_but_plain_tiredness_does_not():
    row = SimpleNamespace(
        candidates=[], context={"selected_candidate_ids": [], "revision": 4}, pace_level=3,
    )
    context, changes = advisor_interpretation._apply_existing_adjustments(
        row, "我今天有点累，帮我把后面的安排放松些", Extraction(places=[]),
    )

    assert row.pace_level == 2
    assert context["revision"] == 5
    assert changes == ["已将节奏调整为 2/5"]

    row.pace_level = 3
    context, changes = advisor_interpretation._apply_existing_adjustments(
        row, "我今天很累", Extraction(places=[]),
    )
    assert row.pace_level == 3
    assert context["revision"] == 4
    assert changes == []


def test_fatigue_event_handoff_does_not_mutate_session_pace_before_preview():
    row = SimpleNamespace(
        candidates=[], context={"selected_candidate_ids": [], "revision": 9}, pace_level=3,
    )
    context, changes = advisor_interpretation._apply_existing_adjustments(
        row,
        "我今天有点累，帮我把后面的安排放松些",
        Extraction(places=[]),
        allow_pace_adjustment=False,
    )

    assert row.pace_level == 3
    assert context["revision"] == 9
    assert changes == []
    assert advisor_interpretation.is_fatigue_replan_request(
        "我今天有点累，帮我把后面的安排放松些",
    )
    assert not advisor_interpretation.is_fatigue_replan_request("我今天很累")


def test_explicit_referential_add_uses_only_a_verified_existing_candidate_and_current_evidence():
    candidate = {"id": "candidate-1", "name": "人民公园"}
    row = SimpleNamespace(candidates=[candidate], context={"selected_candidate_ids": []}, pace_level=3)
    extraction = Extraction(
        places=[PlaceMention(
            name="人民公园", confidence=0.9, action="add", evidence="第一个地点",
        )],
    )

    context, messages = advisor_interpretation._apply_existing_adjustments(
        row, "把第一个地点加入行程", extraction,
    )

    assert context["selected_candidate_ids"] == ["candidate-1"]
    assert messages == ["已将人民公园加入草案候选"]


@pytest.mark.parametrize(
    ("message", "expected_ids", "expected_changes"),
    [
        ("不要西湖，保留断桥", [], ["已从草案移除西湖"]),
        ("不要移除西湖", ["candidate-west-lake"], []),
    ],
)
def test_named_place_negation_distinguishes_exclusion_from_negated_removal(
    message, expected_ids, expected_changes,
):
    row = SimpleNamespace(
        candidates=[{"id": "candidate-west-lake", "name": "西湖"}],
        context={"selected_candidate_ids": ["candidate-west-lake"]},
        pace_level=3,
    )

    context, changes = advisor_interpretation._apply_existing_adjustments(
        row, message, Extraction(places=[]),
    )

    assert context["selected_candidate_ids"] == expected_ids
    assert changes == expected_changes


@pytest.mark.parametrize(
    ("message", "expected_action"),
    [
        ("把刚才提到的第一个地点移除", "remove"),
        ("把第二个地点加入行程", "add"),
    ],
)
def test_explicit_ordinal_reference_changes_only_a_conversation_candidate(
    message, expected_action,
):
    candidates = [
        {"id": "candidate-west-lake", "name": "西湖"},
        {"id": "candidate-lingyin", "name": "灵隐寺"},
    ]
    row = SimpleNamespace(
        candidates=candidates,
        context={"selected_candidate_ids": ["candidate-west-lake"]},
        pace_level=3,
    )
    history = [
        {"role": "assistant", "content": "之前的候选有河坊街、灵隐寺和西湖。"},
        {"role": "user", "content": "我在考虑西湖和灵隐寺"},
        {"role": "assistant", "content": "西湖和灵隐寺都在当前行程中。"},
    ]

    context, changes = advisor_interpretation._apply_existing_adjustments(
        row, message, Extraction(places=[]), conversation_context=history,
    )

    if expected_action == "remove":
        assert context["selected_candidate_ids"] == []
        assert changes == ["已从草案移除西湖"]
    else:
        assert context["selected_candidate_ids"] == ["candidate-lingyin", "candidate-west-lake"]
        assert changes == ["已将灵隐寺加入草案候选"]


@pytest.mark.parametrize(
    ("message", "history"),
    [
        (
            "请把我收藏的人民公园、武侯祠和锦里安排成两日游",
            [],
        ),
        (
            "三处都安排，按轻松节奏分两天排并留出午餐时间",
            [{"role": "user", "content": "请把我收藏的人民公园、武侯祠和锦里安排成两日游"}],
        ),
    ],
)
def test_explicit_full_itinerary_selects_named_and_count_referenced_saved_places(message, history):
    candidates = [
        {"id": "candidate-park", "name": "人民公园"},
        {"id": "candidate-temple", "name": "武侯祠"},
        {"id": "candidate-alley", "name": "锦里"},
    ]
    row = SimpleNamespace(
        candidates=candidates,
        context={"selected_candidate_ids": []},
        pace_level=3,
    )

    context, changes = advisor_interpretation._apply_existing_adjustments(
        row, message, Extraction(places=[]), conversation_context=history,
    )

    assert context["selected_candidate_ids"] == [
        "candidate-alley", "candidate-park", "candidate-temple",
    ]
    assert changes == [
        "已将人民公园加入草案候选",
        "已将武侯祠加入草案候选",
        "已将锦里加入草案候选",
    ]


@pytest.mark.parametrize("message", ["那第一个地点呢？", "不要移除第一个地点"])
def test_ordinal_reference_without_unambiguous_current_action_does_not_mutate(message):
    row = SimpleNamespace(
        candidates=[{"id": "candidate-west-lake", "name": "西湖"}],
        context={"selected_candidate_ids": ["candidate-west-lake"]},
        pace_level=3,
    )

    context, changes = advisor_interpretation._apply_existing_adjustments(
        row, message, Extraction(places=[]),
        conversation_context=[{"role": "user", "content": "我在考虑西湖"}],
    )

    assert context["selected_candidate_ids"] == ["candidate-west-lake"]
    assert changes == []


def test_day_ordinal_and_unrelated_do_not_save_phrase_do_not_remove_a_place():
    message = "请让第二天上午留空，午餐留出一小时；只生成待确认草案，不要替我确认保存"
    candidates = [
        {"id": "p1", "name": "人民公园"},
        {"id": "p2", "name": "武侯祠"},
    ]
    row = SimpleNamespace(
        candidates=candidates, context={"selected_candidate_ids": ["p1", "p2"]}, pace_level=3,
    )
    history = [{"role": "assistant", "content": "本轮纳入排程的地点有人民公园、武侯祠。"}]
    extraction = Extraction(places=[PlaceMention(
        name="武侯祠", confidence=0.99, action="remove", evidence=message,
    )])

    context, changes = advisor_interpretation._apply_existing_adjustments(
        row, message, extraction, conversation_context=history,
    )

    assert context["selected_candidate_ids"] == ["p1", "p2"]
    assert changes == []


@pytest.mark.parametrize(
    ("action", "evidence", "message"),
    [
        ("add", None, "把第一个地点加入行程"),
        ("add", "我很喜欢博物馆", "把第一个地点加入行程"),
        ("mention", "第一个地点", "那第一个地点呢？"),
    ],
)
def test_referential_place_does_not_change_selection_without_explicit_current_action(
    action, evidence, message,
):
    candidate = {"id": "candidate-1", "name": "人民公园"}
    row = SimpleNamespace(candidates=[candidate], context={"selected_candidate_ids": []}, pace_level=3)
    extraction = Extraction(places=[PlaceMention(
        name="人民公园", confidence=0.9, action=action, evidence=evidence,
    )])

    context, messages = advisor_interpretation._apply_existing_adjustments(row, message, extraction)

    assert context["selected_candidate_ids"] == []
    assert messages == []


@pytest.mark.parametrize("content", [
    "请让第二天上午留空，午餐留出一小时；只生成待确认草案，不要替我确认保存",
    "把武侯祠安排进行程，不要替我确认保存",
])
def test_extractor_cannot_remove_a_place_from_unrelated_save_negation(content):
    candidate = {"id": "candidate-temple", "name": "武侯祠"}
    row = SimpleNamespace(
        candidates=[candidate], context={"selected_candidate_ids": [candidate["id"]]},
        pace_level=3,
    )
    extraction = Extraction(places=[PlaceMention(
        name="武侯祠", confidence=0.99, action="remove", evidence=content,
    )])

    context, changes = advisor_interpretation._apply_existing_adjustments(
        row, content, extraction,
    )

    assert context["selected_candidate_ids"] == [candidate["id"]]
    assert changes == []


def test_destination_queries_are_bounded_and_remove_explicitly_rejected_interests():
    queries = _destination_queries(
        "喜欢自然、历史和美食，不喜欢历史古迹",
        ["不要博物馆"],
    )

    assert queries == [("自然与公园", "公园"), ("地方美食", "美食")]
    assert len(queries) <= 2


def test_destination_queries_need_an_explicit_supported_preference():
    assert _destination_queries("想去一个舒服的地方", []) == []


def test_current_destination_interest_precedes_older_profile_preferences():
    queries = _prioritized_destination_queries(
        "这次我最想去海边看看",
        "偏好亲子活动，也喜欢博物馆和徒步",
        [],
    )

    assert queries[0] == ("海滨休闲", "海滨")
    assert len(queries) == 2


def test_current_negative_preference_blocks_conflicting_profile_interest():
    queries = _prioritized_destination_queries(
        "这次只想看海，不要博物馆",
        "以前偏好博物馆，也喜欢摄影和自然风景",
        [],
    )

    assert queries[0] == ("海滨休闲", "海滨")
    assert all(label != "博物馆与展览" for label, _query in queries)


def test_current_narrow_scope_does_not_add_other_stored_interests():
    queries = _prioritized_destination_queries(
        "这次只想去海边",
        "长期偏好徒步和博物馆",
        [],
    )

    assert queries == [("海滨休闲", "海滨")]


@pytest.mark.parametrize(
    ("preference", "expected"),
    [
        ("想带孩子去玩", [("亲子活动", "亲子")]),
        ("喜欢博物馆和温泉", [("博物馆与展览", "博物馆"), ("温泉与休闲", "温泉")]),
        ("我喜欢摄影和日落", [("摄影与观景", "观景台")]),
        ("想徒步看看山水", [("徒步与户外", "徒步"), ("自然与公园", "公园")]),
        ("喜欢亲子活动，不想逛商场", [("亲子活动", "亲子")]),
    ],
)
def test_destination_queries_cover_common_travel_interests(preference, expected):
    assert _destination_queries(preference, []) == expected


@pytest.mark.asyncio
async def test_destination_recommendations_start_with_broad_live_results_without_required_profile(monkeypatch):
    row = SimpleNamespace(
        id=uuid4(), status="ACTIVE", destination="", start_date=None, end_date=None,
        preference_text="", context={"planning_inputs": {"origin": "", "hard_constraints": []}},
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

    monkeypatch.setattr(advisor_api, "owned_session", owned)
    monkeypatch.setattr(advisor_api, "read_session", read)
    monkeypatch.setattr(settings, "maps_provider", "amap")
    monkeypatch.setattr(settings, "llm_api_key", "")
    monkeypatch.setattr(settings, "llm_model", "")
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
    assert stored.payload["comparison_limits"] == {
        "season_not_compared": True,
        "travel_from_origin_not_compared": True,
        "ranking_basis": "map_interest_match_count",
    }
    assert stored.payload["agent_trace"]["status"] == "SUCCEEDED"
    assert stored.payload["agent_trace"]["decision_source"] == "RULE"
    assert stored.payload["search_scope_complete"] is True
    assert len(stored.payload["agent_trace"]["transitions"]) == 12
    assert "自然与美食两类初步探索" in stored.content
    assert len(redis.calls) == 1 and "advisor-destinations" in redis.calls[0]


@pytest.mark.asyncio
async def test_destination_recommendations_use_an_explicit_user_city_as_the_allowlist(monkeypatch):
    row = SimpleNamespace(
        id=uuid4(), status="ACTIVE", destination="", start_date=None, end_date=None,
        preference_text="想看历史文化", context={"planning_inputs": {"origin": "", "hard_constraints": []}},
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
            self.calls.append((city, keyword))
            return [Place("amap", f"{city}-{keyword}", f"{city}{keyword}", "测试地址", keyword, 24.9, 118.6)]

    monkeypatch.setattr(advisor_api, "owned_session", owned)
    monkeypatch.setattr(advisor_api, "read_session", read)
    monkeypatch.setattr(settings, "maps_provider", "amap")
    monkeypatch.setattr(settings, "llm_api_key", "")
    monkeypatch.setattr(settings, "llm_model", "")
    db, maps = Database(), Maps()

    await recommend_destinations(
        uuid4(), db=db, user=SimpleNamespace(id=uuid4()), maps=maps,
        redis=RateLimitRedis(), payload=AdvisorDestinationDiscoveryRequest(city="泉州"),
    )

    stored = next(item for item in db.rows if isinstance(item, advisor_api.AdvisorMessageModel))
    assert maps.calls == [("泉州", "历史古迹")]
    assert {item["city"] for item in stored.payload["recommendations"]} == {"泉州"}
    assert stored.payload["search_scope_complete"] is True


@pytest.mark.asyncio
async def test_destination_recommendations_return_prior_response_for_same_idempotency_key(monkeypatch):
    row = SimpleNamespace(id=uuid4(), status="ACTIVE", destination="")
    owner_id = uuid4()

    async def owned(*_args, **_kwargs):
        return row

    async def read(_db, session_row):
        return session_row

    class Database:
        async def scalar(self, statement):
            assert "advisor_messages" in str(statement)
            return uuid4()

    class Maps:
        async def search_place(self, *_args):
            raise AssertionError("completed idempotent request must not search again")

    monkeypatch.setattr(advisor_api, "owned_session", owned)
    monkeypatch.setattr(advisor_api, "read_session", read)
    monkeypatch.setattr(settings, "maps_provider", "amap")
    response = await recommend_destinations(
        uuid4(), db=Database(), user=SimpleNamespace(id=owner_id), maps=Maps(),
        redis=RateLimitRedis(), idempotency_key="dest-retry-123",
    )
    assert response is row


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

    monkeypatch.setattr(advisor_api, "owned_session", owned)
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

    monkeypatch.setattr(advisor_api, "owned_session", owned)
    monkeypatch.setattr(advisor_api, "read_session", read)
    monkeypatch.setattr(settings, "maps_provider", "amap")
    monkeypatch.setattr(settings, "llm_api_key", "")
    monkeypatch.setattr(settings, "llm_model", "")
    db = Database()

    await recommend_destinations(uuid4(), db=db, user=SimpleNamespace(id=uuid4()), maps=Maps(), redis=RateLimitRedis())

    stored = next(item for item in db.rows if isinstance(item, advisor_api.AdvisorMessageModel))
    assert stored.payload["failed_search_count"] == 4
    assert stored.payload["search_scope_complete"] is False
    assert stored.payload["recommendations"]
    assert "4 项地图搜索暂时失败" in stored.content


def test_discover_session_can_start_without_destination_or_dates():
    session = AdvisorSessionCreate(mode="discover", preference_text="喜欢自然和美食")

    assert session.destination == ""
    assert session.start_date is None and session.end_date is None


def test_destination_discovery_city_is_trimmed_and_blank_city_is_rejected():
    assert AdvisorDestinationDiscoveryRequest(city="  泉州  ").city == "泉州"
    with pytest.raises(ValidationError):
        AdvisorDestinationDiscoveryRequest(city="   ")


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


def test_candidate_response_shows_conflicts_from_all_profile_hard_limits_without_mutation():
    candidate = {"id": "museum-1", "name": "市立博物馆", "category": "MUSEUM"}
    memories = [
        {"id": f"pref-{index}", "constraint_type": "PREFERENCE", "text": "喜欢自然"}
        for index in range(8)
    ] + [
        {"id": f"hard-{index}", "constraint_type": "HARD_LIMIT", "text": "不去酒吧"}
        for index in range(5)
    ] + [{"id": "hard-6", "constraint_type": "HARD_LIMIT", "text": "不去博物馆"}]

    annotated = annotate_profile_memory_conflicts([candidate], memories)

    assert annotated[0]["profile_memory_conflicts"]
    assert "不去博物馆" in annotated[0]["profile_memory_conflicts"][0]
    assert candidate == {"id": "museum-1", "name": "市立博物馆", "category": "MUSEUM"}
    assert not annotate_profile_memory_conflicts(
        [candidate], [{"constraint_type": "PREFERENCE", "text": "不喜欢博物馆"}],
    )[0]["profile_memory_conflicts"]


def test_profile_memory_conflicts_are_overridable_separately_from_trip_hard_limits():
    candidate = {"name": "市立博物馆", "category": "MUSEUM"}
    profile_memory = [{"constraint_type": "HARD_LIMIT", "text": "不去博物馆"}]
    trip_constraint = [{"constraint_type": "HARD_LIMIT", "text": "不去博物馆"}]

    profile_conflicts, trip_conflicts = classify_draft_memory_conflicts(
        [candidate], profile_memory, [],
    )
    assert profile_conflicts and not trip_conflicts

    profile_conflicts, trip_conflicts = classify_draft_memory_conflicts(
        [candidate], [], trip_constraint,
    )
    assert not profile_conflicts and trip_conflicts


def test_profile_hard_limit_matches_a_saved_park_candidate():
    conflicts = _hard_limit_conflicts(
        {"name": "人民公园", "category": "公园;绿地"},
        [{"constraint_type": "HARD_LIMIT", "text": "不去公园"}],
    )
    assert conflicts


def test_only_explicitly_overridden_conflicting_profile_limits_are_resolved():
    memories = [
        {"constraint_type": "HARD_LIMIT", "text": "不去公园"},
        {"constraint_type": "HARD_LIMIT", "text": "花生过敏"},
    ]
    selected = [{"name": "人民公园", "category": "公园;绿地"}]

    assert overridden_profile_memory_texts(selected, memories, explicitly_overridden=False) == set()
    assert overridden_profile_memory_texts(selected, memories, explicitly_overridden=True) == {"不去公园"}


def test_food_safety_hard_limits_are_reported_for_manual_verification():
    memory = {"constraint_type": "HARD_LIMIT", "text": "花生过敏"}
    assert not _hard_limit_conflicts({"name": "川菜馆", "category": "RESTAURANT"}, [memory])


@pytest.mark.parametrize("budget", [128, 512])
def test_named_saved_place_schedule_uses_deterministic_extraction(budget):
    candidates = [{"name": name} for name in ("人民公园", "武侯祠", "锦里")]

    assert should_use_deterministic_schedule_extraction(
        content="帮我把收藏的人民公园、武侯祠和锦里安排成两日行程。",
        has_trip=True,
        saved_candidates=candidates,
        output_token_budget=budget,
    )


@pytest.mark.parametrize(
    ("content", "has_trip", "budget", "expected"),
    [
        ("帮我把人民公园安排成两日行程", False, 128, False),
        ("帮我把人民公园安排成两日行程", True, 512, True),
        ("把第一个地点安排成两日行程", True, 128, False),
        ("帮我搜索人民公园", True, 128, False),
    ],
)
def test_deterministic_schedule_extraction_is_narrow(content, has_trip, budget, expected):
    assert should_use_deterministic_schedule_extraction(
        content=content,
        has_trip=has_trip,
        saved_candidates=[{"name": "人民公园"}],
        output_token_budget=budget,
    ) is expected
