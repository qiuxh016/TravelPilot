import json

import httpx
import pytest
from travelpilot_core.agents.advisor_reply import (
    compose_advisor_reply,
    compose_advisor_reply_with_diagnostics,
)
from travelpilot_core.agents.extraction import extract_places
from travelpilot_core.agents.planner import decide_next_action, decide_preferences


class Response:
    def __init__(self, content):
        self.content = content
        self.status_code = 200

    def raise_for_status(self):
        return None

    def json(self):
        return self.content


def mock_client(monkeypatch, payload):
    requests = []

    class Client:
        def __init__(self, **_kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def post(self, *_args, **_kwargs):
            requests.append(_kwargs.get("json"))
            return Response(payload)

    monkeypatch.setattr(httpx, "AsyncClient", Client)
    return requests


@pytest.mark.asyncio
async def test_extraction_budget_tracks_generated_tokens_not_prompt_tokens(monkeypatch):
    requests = mock_client(monkeypatch, {
        "usage": {"total_tokens": 1000, "prompt_tokens": 950, "completion_tokens": 50},
        "choices": [{"message": {"content": '{"places": [], "search_queries": [], "pace_adjustment": "none"}'}}],
    })

    result = await extract_places(
        "想找旅行灵感", api_key="test", model="test", base_url="https://example.invalid/v1", token_budget=100,
    )

    assert result.token_usage == 1000
    assert result.output_tokens == 50
    assert "thinking" not in requests[0]


@pytest.mark.asyncio
async def test_extraction_uses_recent_dialogue_only_to_resolve_latest_user_reference(monkeypatch):
    requests = mock_client(monkeypatch, {
        "usage": {"total_tokens": 20, "prompt_tokens": 14, "completion_tokens": 6},
        "choices": [{"message": {"content": (
            '{"places":[{"name":"人民公园","category":"ATTRACTION",'
            '"confidence":0.9,"action":"add","evidence":"第一个地点"}],'
            '"search_queries":[],"pace_adjustment":"none"}'
        )}}],
    })

    result = await extract_places(
        "把第一个地点加入行程", api_key="test", model="test",
        base_url="https://example.invalid/v1", token_budget=100,
        conversation_context=[
            {"role": "assistant", "content": "候选：人民公园、武侯祠。"},
            {"role": "user", "content": "推荐成都适合散步的地方。"},
        ],
    )

    supplied = json.loads(requests[0]["messages"][1]["content"])
    assert supplied["current_message"] == "把第一个地点加入行程"
    assert supplied["recent_conversation"][0]["role"] == "assistant"
    assert result.places[0].name == "人民公园"
    assert result.places[0].action == "add"
    assert result.places[0].evidence == "第一个地点"


@pytest.mark.asyncio
async def test_extraction_bounds_opted_in_preferences_as_structured_context(monkeypatch):
    requests = mock_client(monkeypatch, {
        "usage": {"total_tokens": 16, "prompt_tokens": 12, "completion_tokens": 4},
        "choices": [{"message": {"content": '{"places": [], "search_queries": [], "pace_adjustment": "none"}'}}],
    })

    await extract_places(
        "推荐一些地点", api_key="test", model="test",
        base_url="https://example.invalid/v1", token_budget=100,
        travel_preferences=[f"偏好 {index}" for index in range(7)],
    )

    supplied = json.loads(requests[0]["messages"][1]["content"])
    assert supplied["confirmed_travel_preferences"] == [f"偏好 {index}" for index in range(5)]


@pytest.mark.asyncio
async def test_extraction_discards_evidence_not_present_in_latest_message(monkeypatch):
    mock_client(monkeypatch, {
        "usage": {"total_tokens": 20, "prompt_tokens": 14, "completion_tokens": 6},
        "choices": [{"message": {"content": (
            '{"places":[{"name":"人民公园","confidence":0.9,'
            '"action":"add","evidence":"之前我很喜欢"}],'
            '"search_queries":[],"pace_adjustment":"none"}'
        )}}],
    })

    result = await extract_places(
        "把第一个地点加入行程", api_key="test", model="test",
        base_url="https://example.invalid/v1", token_budget=100,
        conversation_context=[{"role": "assistant", "content": "之前我很喜欢人民公园。"}],
    )

    assert result.places[0].evidence is None


@pytest.mark.asyncio
async def test_extraction_keeps_only_task_intents_with_verbatim_current_message_evidence(monkeypatch):
    mock_client(monkeypatch, {
        "usage": {"total_tokens": 20, "prompt_tokens": 14, "completion_tokens": 6},
        "choices": [{"message": {"content": (
            '{"places":[],"search_queries":[],"pace_adjustment":"none",'
            '"requested_tasks":['
            '{"kind":"WEATHER_CHECK","evidence":"需要带伞吗"},'
            '{"kind":"ROUTE_QUERY","evidence":"之前提到的路线"}]}'
        )}}],
    })

    result = await extract_places(
        "下雨时需要带伞吗？", api_key="test", model="test",
        base_url="https://example.invalid/v1", token_budget=100,
    )

    assert [(item.kind, item.evidence) for item in result.requested_tasks] == [
        ("WEATHER_CHECK", "需要带伞吗"),
    ]


@pytest.mark.asyncio
async def test_extraction_can_disable_provider_thinking_mode(monkeypatch):
    requests = mock_client(monkeypatch, {
        "usage": {"total_tokens": 12, "prompt_tokens": 8, "completion_tokens": 4},
        "choices": [{"message": {"content": '{"places": [], "search_queries": [], "pace_adjustment": "none"}'}}],
    })

    await extract_places(
        "想找旅行灵感", api_key="test", model="test", base_url="https://api.deepseek.com",
        token_budget=100, thinking_mode="disabled",
    )

    assert requests[0]["thinking"] == {"type": "disabled"}
    system_prompt = requests[0]["messages"][0]["content"]
    assert "return json only" in system_prompt.casefold()
    assert '{"intent":"schedule","places":[],"search_queries":[]' in system_prompt
    assert "confidence must be a JSON number" in system_prompt


@pytest.mark.asyncio
async def test_extraction_uses_configured_budget_above_previous_800_token_cap(monkeypatch):
    requests = mock_client(monkeypatch, {
        "usage": {"total_tokens": 30, "prompt_tokens": 10, "completion_tokens": 20},
        "choices": [{"message": {"content": '{"places": [], "search_queries": [], "pace_adjustment": "none"}'}}],
    })

    await extract_places(
        "梅州一日游攻略", api_key="test", model="test", base_url="https://example.invalid/v1",
        token_budget=1200,
    )

    assert requests[0]["max_tokens"] == 1200


@pytest.mark.asyncio
async def test_extraction_accepts_a_complete_json_code_fence(monkeypatch):
    mock_client(monkeypatch, {
        "usage": {"total_tokens": 12, "prompt_tokens": 8, "completion_tokens": 4},
        "choices": [{"message": {"content": '```json\n{"places": [], "search_queries": [], "pace_adjustment": "none"}\n```'}}],
    })

    result = await extract_places(
        "想找旅行灵感", api_key="test", model="test", base_url="https://example.invalid/v1", token_budget=100,
    )

    assert result.places == []
    assert result.output_tokens == 4


@pytest.mark.asyncio
async def test_invalid_extraction_reports_response_shape_without_response_text(monkeypatch):
    from travelpilot_core.agents.extraction import TextExtractionFailure

    private_content = "private model output that is not json"
    mock_client(monkeypatch, {
        "usage": {"total_tokens": 12, "prompt_tokens": 8, "completion_tokens": 4},
        "choices": [{"message": {"content": private_content}}],
    })

    with pytest.raises(TextExtractionFailure) as error:
        await extract_places(
            "想找旅行灵感", api_key="test", model="test", base_url="https://example.invalid/v1", token_budget=100,
        )

    assert error.value.response_shape == {
        "content_type": "str",
        "character_count": len(private_content),
        "json_fence": False,
        "starts_with_object": False,
        "ends_with_object": False,
    }
    assert private_content not in str(error.value)


@pytest.mark.asyncio
async def test_empty_extraction_reports_safe_provider_metadata(monkeypatch):
    from travelpilot_core.agents.extraction import TextExtractionFailure

    mock_client(monkeypatch, {
        "usage": {"prompt_tokens": 50, "completion_tokens": 0, "total_tokens": 50},
        "choices": [{"message": {"content": ""}, "finish_reason": "length"}],
    })

    with pytest.raises(TextExtractionFailure) as error:
        await extract_places(
            "攻略提到宽窄巷子", api_key="test", model="test",
            base_url="https://example.invalid/v1", token_budget=100,
        )

    assert error.value.response_shape["character_count"] == 0
    assert error.value.provider_metadata == {
        "http_status": 200,
        "usage": {"prompt_tokens": 50, "completion_tokens": 0, "total_tokens": 50},
        "finish_reason": "length",
    }
    assert "攻略提到" not in str(error.value)


@pytest.mark.asyncio
async def test_successful_extraction_retains_safe_provider_metadata(monkeypatch):
    mock_client(monkeypatch, {
        "usage": {"prompt_tokens": 50, "completion_tokens": 9, "total_tokens": 59},
        "choices": [{
            "message": {"content": '{"places": [], "search_queries": [], "pace_adjustment": "none"}'},
            "finish_reason": "stop",
        }],
    })

    extraction = await extract_places(
        "攻略提到宽窄巷子", api_key="test", model="test",
        base_url="https://example.invalid/v1", token_budget=100,
    )

    assert extraction.provider_http_status == 200
    assert extraction.finish_reason == "stop"
    assert extraction.token_usage == 59
    assert extraction.output_tokens == 9


@pytest.mark.asyncio
async def test_reply_budget_tracks_generated_tokens_and_keeps_total_usage(monkeypatch):
    mock_client(monkeypatch, {
        "usage": {"total_tokens": 1000, "prompt_tokens": 950, "completion_tokens": 50},
        "choices": [{"message": {"content": '{"reply": "已收到", "follow_up_question": null}'}}],
    })

    reply, total_tokens, output_tokens = await compose_advisor_reply(
        {"current_message": "想找旅行灵感"},
        api_key="test", model="test", base_url="https://example.invalid/v1", token_budget=100,
    )

    assert reply is not None and reply.reply == "已收到"
    assert total_tokens == 1000
    assert output_tokens == 50


@pytest.mark.asyncio
async def test_deepseek_advisor_reply_disables_thinking_and_answers_open_questions(monkeypatch):
    requests = mock_client(monkeypatch, {
        "usage": {"total_tokens": 60, "prompt_tokens": 45, "completion_tokens": 15},
        "choices": [{"message": {"content": '{"reply":"带孩子可以优先考虑客家博物馆，行程节奏也更容易放慢。","follow_up_question":null}'}}],
    })

    reply = await compose_advisor_reply_with_diagnostics(
        {
            "current_message": "那哪个更适合带孩子？",
            "recent_conversation": [
                {"role": "assistant", "content": "梅州可以看客家博物馆或雁南飞。"},
                {"role": "user", "content": "我会带孩子，喜欢轻松一点。"},
            ],
            "verified_candidates": [
                {
                    "name": "客家博物馆", "category": "博物馆", "provider": "amap",
                    "verification_status": "VERIFIED", "evidence_scope": "PRIOR_TURN",
                }
            ],
            "planning_stage": "destination_discovery",
            "confirmed_personal_memories": [
                {"category": "PACE", "constraint_type": "PREFERENCE", "text": "喜欢轻松节奏"},
                {"category": "ALLERGY", "constraint_type": "HARD_LIMIT", "text": "花生过敏"},
            ],
        },
        api_key="test", model="deepseek-flash", base_url="https://api.deepseek.com", token_budget=128,
    )

    assert reply.reply is not None
    assert "带孩子" in reply.reply.reply
    assert requests[0]["thinking"] == {"type": "disabled"}
    assert requests[0]["max_tokens"] == 128
    serialized_facts = json.loads(requests[0]["messages"][1]["content"])
    assert serialized_facts["current_message"] == "那哪个更适合带孩子？"
    assert len(serialized_facts["recent_conversation"]) == 2
    assert "带孩子" in serialized_facts["recent_conversation"][1]["content"]
    assert serialized_facts["verified_candidates"][0]["evidence_scope"] == "PRIOR_TURN"
    assert len(serialized_facts["confirmed_personal_memories"]) == 2
    system_prompt = requests[0]["messages"][0]["content"]
    assert "latest explicit request" in system_prompt
    assert "server-side draft verifier remains authoritative" in system_prompt
    assert "do not treat prior-turn evidence as current opening hours" in system_prompt


@pytest.mark.asyncio
async def test_advisor_reply_synthesizes_only_the_requested_verified_candidate_category(monkeypatch):
    requests = mock_client(monkeypatch, {
        "usage": {"total_tokens": 70, "prompt_tokens": 52, "completion_tokens": 18},
        "choices": [{"message": {"content": '{"reply":"这两家都是已核验的午餐候选；目前没有菜单和价格数据，建议先按口味与位置挑选。","follow_up_question":"你更想吃客家菜还是清淡一些？"}'}}],
    })

    result = await compose_advisor_reply_with_diagnostics(
        {
            "current_message": "给我推荐午餐地点，顺便说哪家更适合带长辈",
            "requested_category": "FOOD",
            "requested_meal_type": "LUNCH",
            "reply_scope": "Only synthesize from these verified candidates.",
            "verified_candidates": [
                {"name": "客家菜馆", "planning_category": "FOOD", "meal_type": "LUNCH",
                 "verification_status": "VERIFIED", "evidence_scope": "CURRENT_TURN"},
                {"name": "江边公园", "planning_category": "ATTRACTION", "meal_type": None,
                 "verification_status": "VERIFIED", "evidence_scope": "CURRENT_TURN"},
            ],
        },
        api_key="test", model="test", base_url="https://example.invalid/v1", token_budget=128,
    )

    assert result.reply is not None
    facts = json.loads(requests[0]["messages"][1]["content"])
    assert facts["requested_category"] == "FOOD"
    assert facts["requested_meal_type"] == "LUNCH"
    assert [item["planning_category"] for item in facts["verified_candidates"]] == ["FOOD", "ATTRACTION"]
    system_prompt = requests[0]["messages"][0]["content"]
    assert "discuss only candidates with that planning_category" in system_prompt
    assert "do not infer them from a candidate name or category" in system_prompt


@pytest.mark.asyncio
async def test_advisor_reply_parses_opt_in_memory_suggestion(monkeypatch):
    mock_client(monkeypatch, {
        "usage": {"total_tokens": 24, "prompt_tokens": 18, "completion_tokens": 6},
        "choices": [{"message": {"content": (
            '{"reply":"我会优先推荐节奏舒缓的安排。",'
            '"memory_suggestion":{"category":"PACE","text":"偏好轻松节奏",'
            '"evidence":"以后旅行尽量安排轻松一点"}}'
        )}}],
    })

    result = await compose_advisor_reply_with_diagnostics(
        {"current_message": "以后旅行尽量安排轻松一点", "memory_suggestions_enabled": True},
        api_key="test", model="test", base_url="https://example.invalid/v1", token_budget=128,
    )

    assert result.reply is not None
    assert result.reply.memory_suggestion is not None
    assert result.reply.memory_suggestion.category == "PACE"
    assert result.reply.memory_suggestion.text == "偏好轻松节奏"
    assert result.reply.memory_suggestion.evidence == "以后旅行尽量安排轻松一点"


@pytest.mark.asyncio
async def test_advisor_reply_diagnostic_distinguishes_invalid_model_output(monkeypatch):
    mock_client(monkeypatch, {
        "usage": {"total_tokens": 6, "completion_tokens": 2},
        "choices": [{"message": {"content": "not-json"}}],
    })

    result = await compose_advisor_reply_with_diagnostics(
        {"current_message": "有什么建议"}, api_key="test", model="test",
        base_url="https://example.invalid/v1", token_budget=128,
    )

    assert result.reply is None
    assert result.error_code == "INVALID_JSON_OR_SCHEMA"


@pytest.mark.asyncio
async def test_advisor_reply_rejects_unverified_place_removal_claim(monkeypatch):
    mock_client(monkeypatch, {
        "usage": {"total_tokens": 22, "completion_tokens": 8},
        "choices": [{"message": {"content": '{"reply":"本轮调整中已从草案移除武侯祠。"}'}}],
    })

    result = await compose_advisor_reply_with_diagnostics(
        {
            "current_message": "请把第二天上午留空，不要替我保存",
            "known_trip_places": [{"name": "武侯祠"}],
            "adjustments_applied": [],
        }, api_key="test", model="test", base_url="https://example.invalid/v1", token_budget=128,
    )

    assert result.reply is None
    assert result.error_code == "UNSUPPORTED_STATE_CLAIM"
    assert result.total_tokens == 22


@pytest.mark.asyncio
async def test_planner_budget_caps_generated_tokens_and_tracks_prompt_separately(monkeypatch):
    requests = mock_client(monkeypatch, {
        "usage": {"total_tokens": 1000, "prompt_tokens": 950, "completion_tokens": 50},
        "choices": [{"message": {"content": '{"pace":"balanced","explanation":"test"}'}}],
    })

    decision, usage = await decide_preferences(
        "安排旅行", api_key="test", model="test", base_url="https://example.invalid/v1",
        token_budget=100,
    )

    assert decision.pace == "balanced"
    assert usage.prompt_tokens == 950
    assert usage.completion_tokens == 50
    assert usage.total_tokens == 1000
    assert requests[0]["max_tokens"] == 100


@pytest.mark.asyncio
async def test_planner_preserves_usage_when_model_returns_invalid_decision(monkeypatch):
    from travelpilot_core.agents.planner import PlannerDecisionFailure

    mock_client(monkeypatch, {
        "usage": {"total_tokens": 160, "prompt_tokens": 120, "completion_tokens": 40},
        "choices": [{"message": {"content": '{"pace":"impossible"}'}}],
    })

    with pytest.raises(PlannerDecisionFailure) as error:
        await decide_preferences(
            "安排旅行", api_key="test", model="test", base_url="https://example.invalid/v1",
            token_budget=100,
        )

    assert error.value.usage.prompt_tokens == 120
    assert error.value.usage.completion_tokens == 40
    assert error.value.usage.total_tokens == 160


@pytest.mark.asyncio
async def test_agent_action_is_limited_to_supplied_read_only_tools(monkeypatch):
    requests = mock_client(monkeypatch, {
        "usage": {"total_tokens": 25, "prompt_tokens": 20, "completion_tokens": 5},
        "choices": [{"message": {"content": '{"kind":"CALL_TOOL","tool":"get_distance_matrix","arguments":{"points":[]}}'}}],
    })

    action, usage = await decide_next_action(
        {"wishlist_count": 2},
        {"get_distance_matrix": {"input_schema": {"type": "object"}}},
        api_key="test", model="test", base_url="https://example.invalid/v1", token_budget=80,
    )

    assert action.tool == "get_distance_matrix"
    assert usage.total_tokens == 25
    assert requests[0]["max_tokens"] == 80


@pytest.mark.asyncio
async def test_deepseek_agent_decision_disables_thinking_and_requests_compact_json(monkeypatch):
    requests = mock_client(monkeypatch, {
        "usage": {"total_tokens": 25, "prompt_tokens": 20, "completion_tokens": 5},
        "choices": [{"message": {"content": '{"kind":"FINISH","summary":"ready"}'}}],
    })

    action, _usage = await decide_next_action(
        {"wishlist_count": 2},
        {"get_distance_matrix": {"input_schema": {"type": "object"}}},
        api_key="test", model="deepseek-flash", base_url="https://api.deepseek.com/v1",
        token_budget=128,
    )

    assert action.kind == "FINISH"
    assert requests[0]["thinking"] == {"type": "disabled"}
    assert requests[0]["max_tokens"] == 128
    system_prompt = requests[0]["messages"][0]["content"]
    assert '"kind":"CALL_TOOL"' in system_prompt
    assert "summary of at most 80 characters" in system_prompt
    assert "the user-facing answer is composed separately" in system_prompt


@pytest.mark.asyncio
async def test_agent_action_rejects_tool_outside_allowlist(monkeypatch):
    from travelpilot_core.agents.planner import PlannerDecisionFailure

    mock_client(monkeypatch, {
        "usage": {"total_tokens": 25, "prompt_tokens": 20, "completion_tokens": 5},
        "choices": [{"message": {"content": '{"kind":"CALL_TOOL","tool":"delete_trip","arguments":{}}'}}],
    })

    with pytest.raises(PlannerDecisionFailure, match="AGENT_ACTION_INVALID"):
        await decide_next_action(
            {}, {"get_distance_matrix": {"input_schema": {"type": "object"}}},
            api_key="test", model="test", base_url="https://example.invalid/v1", token_budget=80,
        )
