import json

import httpx
import pytest
from travelpilot_core.agents.advisor_reply import compose_advisor_reply_with_diagnostics


class Response:
    def __init__(self, content):
        self.content = content

    def raise_for_status(self):
        return None

    def json(self):
        return self.content


def mock_client(monkeypatch, reply):
    class Client:
        def __init__(self, **_kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def post(self, *_args, **_kwargs):
            return Response({
                "usage": {"total_tokens": 20, "completion_tokens": 8},
                "choices": [{"message": {"content": json.dumps({"reply": reply}, ensure_ascii=False)}}],
            })

    monkeypatch.setattr(httpx, "AsyncClient", Client)


@pytest.mark.asyncio
@pytest.mark.parametrize("reply", [
    "这版行程已提交保存。",
    "就作为你的确认版本提交保存。",
    "已经更新了正式计划。",
    "我帮你保存好了。",
    "正式行程已经写入。",
])
async def test_reply_rejects_unverified_plan_persistence_claim(monkeypatch, reply):
    mock_client(monkeypatch, reply)

    result = await compose_advisor_reply_with_diagnostics(
        {"current_message": "我确认，帮我直接保存"}, api_key="test", model="test",
        base_url="https://example.invalid/v1", token_budget=128,
    )

    assert result.reply is None
    assert result.error_code == "UNVERIFIED_PERSISTENCE_CLAIM"


@pytest.mark.asyncio
async def test_reply_allows_future_confirmation_language(monkeypatch):
    mock_client(monkeypatch, "检查后点击确认并应用到行程，才会保存正式计划。")

    result = await compose_advisor_reply_with_diagnostics(
        {"current_message": "怎么保存"}, api_key="test", model="test",
        base_url="https://example.invalid/v1", token_budget=128,
    )

    assert result.reply is not None
