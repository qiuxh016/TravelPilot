import json

import httpx
import pytest

from tests.evaluation.bounded_model_proxy import Budget, create_app


def _response(content: dict, status_code: int = 200) -> httpx.Response:
    return httpx.Response(status_code, json=content)


@pytest.mark.asyncio
async def test_proxy_clamps_per_call_and_forwards_only_chat_completions():
    seen = []

    async def handler(request: httpx.Request) -> httpx.Response:
        seen.append((request.url, request.headers.get("authorization"), json.loads(request.content)))
        return _response({"choices": [], "usage": {"completion_tokens": 1}})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    app = create_app(
        upstream_base_url="https://api.deepseek.com/v1",
        budget=Budget(max_requests=2, max_requested_output_tokens=600, max_output_tokens_per_request=512),
        client=client,
    )
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as proxy:
        response = await proxy.post(
            "/v1/chat/completions", headers={"Authorization": "Bearer secret"},
            json={"model": "test", "max_tokens": 900, "messages": []},
        )
        assert response.status_code == 200
        assert seen[0][0].path == "/v1/chat/completions"
        assert seen[0][1] == "Bearer secret"
        assert seen[0][2]["max_tokens"] == 512
        budget = (await proxy.get("/budget")).json()
    await client.aclose()
    assert budget["admitted_requests"] == 1
    assert budget["requested_output_tokens"] == 512


@pytest.mark.asyncio
async def test_proxy_rejects_aggregate_requested_tokens_before_upstream_call():
    calls = 0

    async def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return _response({"ok": True})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    app = create_app(
        upstream_base_url="https://api.deepseek.com/v1",
        budget=Budget(max_requests=5, max_requested_output_tokens=700, max_output_tokens_per_request=512),
        client=client,
    )
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as proxy:
        assert (await proxy.post("/v1/chat/completions", json={"max_tokens": 512})).status_code == 200
        rejected = await proxy.post("/v1/chat/completions", json={"max_tokens": 200})
        state = (await proxy.get("/budget")).json()
    await client.aclose()
    assert rejected.status_code == 429
    assert rejected.json()["error"]["code"] == "EVAL_PROXY_BUDGET_EXHAUSTED"
    assert calls == 1
    assert state["requested_output_tokens"] == 512
    assert state["rejected_requests"] == 1


@pytest.mark.asyncio
async def test_proxy_enforces_request_count_under_concurrent_calls():
    calls = 0

    async def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return _response({"ok": True})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    app = create_app(
        upstream_base_url="https://api.deepseek.com/v1",
        budget=Budget(max_requests=2, max_requested_output_tokens=1024, max_output_tokens_per_request=512),
        client=client,
    )
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as proxy:
        results = await __import__("asyncio").gather(*(
            proxy.post("/v1/chat/completions", json={"max_tokens": 128}) for _ in range(3)
        ))
        state = (await proxy.get("/budget")).json()
    await client.aclose()
    assert sorted(item.status_code for item in results) == [200, 200, 429]
    assert calls == 2
    assert state["admitted_requests"] == 2
    assert state["rejected_requests"] == 1


def test_proxy_requires_secure_fixed_upstream():
    with pytest.raises(ValueError, match="HTTPS"):
        create_app(upstream_base_url="http://model.example/v1")
    with pytest.raises(ValueError, match="credentials"):
        create_app(upstream_base_url="https://user:pass@api.deepseek.com/v1")
    with pytest.raises(ValueError):
        create_app(upstream_base_url="https://api.deepseek.com.attacker.invalid/v1")
    with pytest.raises(ValueError):
        create_app(upstream_base_url="https://api.deepseek.com:444/v1")
    with pytest.raises(ValueError):
        create_app(upstream_base_url="https://api.deepseek.com/other")

    root_base = create_app(upstream_base_url="https://api.deepseek.com")
    assert root_base.state.upstream_base_url == "https://api.deepseek.com"


def test_proxy_rejects_non_positive_programmatic_budget():
    with pytest.raises(ValueError, match="positive"):
        create_app(
            upstream_base_url="https://api.deepseek.com/v1",
            budget=Budget(max_requests=0),
        )
