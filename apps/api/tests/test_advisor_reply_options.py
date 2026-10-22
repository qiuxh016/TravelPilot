import pytest
from travelpilot_core.agents import advisor_reply


@pytest.mark.asyncio
async def test_model_follow_up_options_are_validated_and_preserved(monkeypatch):
    requests = []

    class FakeResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return {
                "usage": {"total_tokens": 64, "completion_tokens": 16},
                "choices": [{"message": {"content": (
                    '{"reply":"可以按你偏好的方向继续筛选。",'
                    '"follow_up_question":"你更喜欢历史文化还是自然风景？",'
                    '"follow_up_options":['
                    '{"label":"历史文化","answer":"我选历史文化"},'
                    '{"label":"自然风景","answer":"我选自然风景"}]}'
                )}}],
            }

    class FakeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def post(self, url, *, headers, json):
            requests.append({"url": url, "headers": headers, "body": json})
            return FakeResponse()

    monkeypatch.setattr(advisor_reply.httpx, "AsyncClient", lambda timeout: FakeClient())

    result = await advisor_reply.compose_advisor_reply_with_diagnostics(
        {"current_message": "我选了泉州，请继续帮我规划", "destination_selected_this_turn": True},
        api_key="test", model="test", base_url="https://example.invalid/v1", token_budget=128,
    )

    assert result.reply is not None
    assert [(option.label, option.answer) for option in result.reply.follow_up_options] == [
        ("历史文化", "我选历史文化"),
        ("自然风景", "我选自然风景"),
    ]
    assert requests[0]["body"]["messages"][0]["content"].find("follow_up_options") >= 0
    assert "do not invent choices" in requests[0]["body"]["messages"][0]["content"]
