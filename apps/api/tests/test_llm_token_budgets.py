import httpx
import pytest
from travelpilot_core.agents.advisor_reply import compose_advisor_reply
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
    assert "Return json only" in system_prompt
    assert '{"places":[],"search_queries":[],"pace_adjustment":"none"}' in system_prompt
    assert "confidence must be a JSON number" in system_prompt


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
