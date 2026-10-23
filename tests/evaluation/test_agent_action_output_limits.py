import pytest
from travelpilot_core.agents.planner import PlannerDecisionFailure, decide_next_action


class _Response:
    def __init__(self, body):
        self._body = body

    def raise_for_status(self):
        return None

    def json(self):
        return self._body


@pytest.mark.asyncio
async def test_action_request_uses_reserved_budget_up_to_absolute_cap(monkeypatch):
    requests = []

    class _Client:
        def __init__(self, **_kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def post(self, _url, **kwargs):
            requests.append(kwargs["json"])
            return _Response({
                "usage": {"prompt_tokens": 20, "completion_tokens": 5, "total_tokens": 25},
                "choices": [{"message": {"content": '{"kind":"FINISH","summary":"ready"}'}}],
            })

    monkeypatch.setattr("travelpilot_core.agents.planner.httpx.AsyncClient", _Client)
    await decide_next_action(
        {}, {}, api_key="test", model="test", base_url="https://example.invalid/v1",
        token_budget=700,
    )

    assert requests[0]["max_tokens"] == 512


@pytest.mark.asyncio
async def test_action_error_identifies_provider_reported_length_truncation(monkeypatch):
    class _Client:
        def __init__(self, **_kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def post(self, _url, **_kwargs):
            return _Response({
                "usage": {"prompt_tokens": 20, "completion_tokens": 128, "total_tokens": 148},
                "choices": [{"message": {"content": '{"kind":"CALL_TOOL"'},
                             "finish_reason": "length"}],
            })

    monkeypatch.setattr("travelpilot_core.agents.planner.httpx.AsyncClient", _Client)
    with pytest.raises(PlannerDecisionFailure) as error:
        await decide_next_action(
            {}, {"get_distance_matrix": {"input_schema": {"type": "object"}}},
            api_key="test", model="test", base_url="https://example.invalid/v1",
            token_budget=256,
        )

    assert error.value.reason == "MESSAGE_CONTENT_TRUNCATED"
    assert error.value.usage.completion_tokens == 128
