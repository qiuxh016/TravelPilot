import httpx
import pytest
from pydantic import BaseModel
from travelpilot_core.agents.runtime import AgentRuntime
from travelpilot_core.ports.tools import RetryableToolError, ToolFailure, ToolSpec
from travelpilot_infra.providers.maps.amap import AmapProvider
from travelpilot_infra.providers.maps.errors import MapsRetryableError, MapsUpstreamError
from travelpilot_infra.providers.tool_adapters import map_tool_specs


class Input(BaseModel):
    value: int


@pytest.mark.parametrize(("kwargs", "message"), [
    ({"max_steps": -1}, "max_steps"),
    ({"max_attempts": True}, "max_attempts"),
    ({"token_budget": 1.5}, "token_budget"),
    ({"token_budget": -1}, "token_budget"),
])
def test_runtime_rejects_invalid_execution_limits(kwargs, message):
    async def record(_trace):
        return None

    with pytest.raises(ValueError, match=message):
        AgentRuntime([], record, **kwargs)


@pytest.mark.parametrize(("timeout", "retries", "message"), [
    (0, 0, "timeout_seconds"),
    (float("inf"), 0, "timeout_seconds"),
    (1, -1, "retry_limit"),
])
def test_runtime_rejects_invalid_tool_execution_limits(timeout, retries, message):
    async def handler(_arguments):
        return {"ok": True}

    async def record(_trace):
        return None

    with pytest.raises(ValueError, match=message):
        AgentRuntime([ToolSpec("lookup", Input, handler, timeout_seconds=timeout,
                               retry_limit=retries)], record)


def test_runtime_rejects_duplicate_tool_names_instead_of_shadowing():
    async def handler(_arguments):
        return {"ok": True}

    async def record(_trace):
        return None

    tools = [ToolSpec("lookup", Input, handler), ToolSpec("lookup", Input, handler)]
    with pytest.raises(ValueError, match="unique"):
        AgentRuntime(tools, record)


@pytest.mark.asyncio
async def test_runtime_retries_only_explicit_transient_errors_and_counts_attempts():
    attempts = 0
    traces = []

    async def handler(_arguments):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise RetryableToolError("temporary")
        return {"ok": True}

    async def record(trace):
        traces.append(trace)

    runtime = AgentRuntime([
        ToolSpec("lookup", Input, handler, retry_limit=2,
                 retryable_exceptions=(RetryableToolError,)),
    ], record)

    assert await runtime.call("lookup", {"value": 1}) == {"ok": True}
    assert runtime.execution_metrics["logical_steps"] == 1
    assert runtime.execution_metrics["tool_attempts"] == 2
    assert runtime.execution_metrics["retries"] == 1
    assert [trace["status"] for trace in traces] == ["FAILED", "SUCCEEDED"]
    assert traces[0]["error_code"] == "TOOL_RETRYABLE_FAILURE"
    assert traces[0]["retryable"] is True
    assert traces[1]["attempt"] == 2


def build_failing_tool(failure):
    state = {"calls": 0, "traces": []}

    async def handler(_arguments):
        state["calls"] += 1
        raise failure

    async def record(trace):
        state["traces"].append(trace)

    return state, handler, record


@pytest.mark.asyncio
async def test_runtime_does_not_retry_programming_or_domain_errors():
    for failure, expected_code in ((ValueError("bug"), "TOOL_INTERNAL_ERROR"),
                                   (ToolFailure("NOT_FOUND"), "NOT_FOUND")):
        state, handler, record = build_failing_tool(failure)
        runtime = AgentRuntime([ToolSpec("lookup", Input, handler, retry_limit=3)], record)
        with pytest.raises(ToolFailure, match=expected_code):
            await runtime.call("lookup", {"value": 1})

        assert state["calls"] == 1
        assert runtime.execution_metrics["tool_attempts"] == 1
        assert runtime.execution_metrics["retries"] == 0
        assert state["traces"][-1]["error_code"] == expected_code
        assert state["traces"][-1]["retryable"] is False


@pytest.mark.asyncio
async def test_runtime_enforces_global_attempt_limit_separately_from_step_limit():
    traces = []

    async def handler(_arguments):
        return {"ok": True}

    async def record(trace):
        traces.append(trace)

    runtime = AgentRuntime([ToolSpec("lookup", Input, handler)], record,
                           max_steps=3, max_attempts=1)
    await runtime.call("lookup", {"value": 1})
    with pytest.raises(ToolFailure, match="ATTEMPT_LIMIT"):
        await runtime.call("lookup", {"value": 2})

    assert runtime.execution_metrics["logical_steps"] == 2
    assert runtime.execution_metrics["tool_attempts"] == 1
    assert traces[-1]["status"] == "REJECTED"
    assert traces[-1]["attempt"] == 1


def test_runtime_accounts_generated_token_budget_and_observed_prompt_usage():
    async def handler(_arguments):
        return {"ok": True}

    async def record(_trace):
        return None

    runtime = AgentRuntime([ToolSpec("lookup", Input, handler)], record, token_budget=10)
    first_reservation = runtime.reserve_output_tokens(7)
    runtime.record_token_usage(input_tokens=100, output_tokens=6,
                               total_tokens=106, reserved=first_reservation)
    second_reservation = runtime.reserve_output_tokens(8)
    runtime.record_token_usage(input_tokens=20, output_tokens=4,
                               total_tokens=24, reserved=second_reservation)

    assert runtime.execution_metrics == {
        "logical_steps": 0,
        "tool_attempts": 0,
        "retries": 0,
        "input_tokens": 120,
        "output_tokens": 10,
        "total_tokens": 130,
        "output_token_budget": 10,
    }
    with pytest.raises(ToolFailure, match="TOKEN_BUDGET_EXCEEDED"):
        runtime.reserve_output_tokens(1)


def test_runtime_records_actual_usage_even_when_provider_exceeds_reserved_output():
    async def handler(_arguments):
        return {"ok": True}

    async def record(_trace):
        return None

    runtime = AgentRuntime([ToolSpec("lookup", Input, handler)], record, token_budget=3)
    reservation = runtime.reserve_output_tokens(3)
    with pytest.raises(ToolFailure, match="TOKEN_BUDGET_EXCEEDED"):
        runtime.record_token_usage(input_tokens=12, output_tokens=4, total_tokens=16,
                                   reserved=reservation)

    assert runtime.execution_metrics["input_tokens"] == 12
    assert runtime.execution_metrics["output_tokens"] == 4
    assert runtime.execution_metrics["total_tokens"] == 16


@pytest.mark.asyncio
@pytest.mark.parametrize(("status", "expected"), [
    (503, MapsRetryableError),
    (429, MapsRetryableError),
    (401, MapsUpstreamError),
])
async def test_amap_classifies_only_transient_http_statuses_as_retryable(monkeypatch, status, expected):
    class Client:
        def __init__(self, **_kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def get(self, *_args, **_kwargs):
            return httpx.Response(status, request=httpx.Request("GET", "https://maps.invalid"))

    monkeypatch.setattr(httpx, "AsyncClient", Client)
    with pytest.raises(expected):
        await AmapProvider("test")._get("/place/text")


@pytest.mark.asyncio
async def test_map_adapter_retries_only_classified_amap_transient_errors():
    class Maps:
        def __init__(self):
            self.calls = 0

        async def search_place(self, _keyword, _city):
            self.calls += 1
            if self.calls == 1:
                raise MapsRetryableError("temporary")
            return []

    async def record(_trace):
        return None

    maps = Maps()
    search = next(tool for tool in map_tool_specs(maps) if tool.name == "search_places")
    runtime = AgentRuntime([search], record, max_steps=1)

    assert await runtime.call("search_places", {"keyword": "公园"}) == {"places": []}
    assert maps.calls == 2
    assert runtime.execution_metrics["tool_attempts"] == 2


@pytest.mark.asyncio
async def test_map_adapter_does_not_retry_non_transient_provider_errors():
    class Maps:
        def __init__(self):
            self.calls = 0

        async def search_place(self, _keyword, _city):
            self.calls += 1
            raise MapsUpstreamError("invalid provider request")

    async def record(_trace):
        return None

    maps = Maps()
    search = next(tool for tool in map_tool_specs(maps) if tool.name == "search_places")
    runtime = AgentRuntime([search], record, max_steps=1)

    with pytest.raises(ToolFailure, match="TOOL_PROVIDER_ERROR"):
        await runtime.call("search_places", {"keyword": "公园"})

    assert maps.calls == 1
    assert runtime.execution_metrics["retries"] == 0
