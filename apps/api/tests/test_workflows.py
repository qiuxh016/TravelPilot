import asyncio
from datetime import UTC, datetime, time
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from pydantic import BaseModel
from travelpilot_api.main import app
from travelpilot_core.agents.extraction import fallback_extract
from travelpilot_core.agents.runtime import AgentRuntime, redact_tool_trace
from travelpilot_core.engines.impact_analysis.replan import Node, plan_diff, replan
from travelpilot_core.ports.tools import ToolFailure, ToolSpec
from travelpilot_infra.providers.maps.amap import AmapProvider
from travelpilot_infra.providers.maps.cached import CachedMapsProvider


def test_openapi_exposes_week_three_and_four_routes():
    paths = TestClient(app).get("/openapi.json").json()["paths"]
    assert "/api/v1/trips/{trip_id}/content" in paths
    assert "/api/v1/trips/{trip_id}/replan-runs" in paths
    assert "/api/v1/trips/{trip_id}/feedback" in paths
    assert "/api/v1/advisor/sessions/{session_id}/destinations" in paths


def test_fallback_extraction_requires_confirmation():
    result = fallback_extract("我想去宽窄巷子和熊猫基地，下午放松")
    assert {place.name for place in result.places} >= {"宽窄巷子", "成都大熊猫繁育研究基地"}
    assert all(place.confidence < 1 for place in result.places)


def test_replan_freezes_other_days_and_fixed_nodes():
    day = datetime(2026, 10, 1, 10, tzinfo=UTC)
    first = Node(uuid4(), "宽窄巷子", "ATTRACTION", day.date(), time(9), time(10), 3)
    second = Node(uuid4(), "熊猫基地", "ATTRACTION", day.date(), time(11), time(13), 3,
                  environment_profile={"exposure": "OUTDOOR", "sensitivity": {"RAIN": "HIGH"},
                                       "source": "USER", "confidence": 1})
    fixed = Node(uuid4(), "预约", "ATTRACTION", day.date(), time(14), time(15), 3, "FIXED")
    next_day = Node(uuid4(), "后一天", "ATTRACTION", day.date().replace(day=2), time(11), time(13), 3)
    result, warnings = replan(
        [first, second, fixed, next_day], "WEATHER_CHANGE", day, {"condition": "RAIN"},
    )
    assert first in result and fixed in result and next_day in result
    assert second not in result and warnings
    changes = plan_diff([first, second, fixed, next_day], result, "WEATHER_CHANGE")
    removed = [item for item in changes if item["operation"] == "REMOVE"]
    assert len(removed) == 1 and removed[0]["stable_key"] == str(second.poi_id)


@pytest.mark.asyncio
async def test_map_cache_avoids_duplicate_provider_calls():
    class Provider:
        def __init__(self):
            self.calls = 0

        async def get_distance_matrix(self, points):
            self.calls += 1
            return [[0, 10], [10, 0]]

    provider = Provider()
    cache = CachedMapsProvider(provider)
    points = [(30.0, 104.0), (30.1, 104.1)]
    assert await cache.get_distance_matrix(points) == await cache.get_distance_matrix(points)
    assert provider.calls == 1


@pytest.mark.asyncio
async def test_amap_matrix_has_one_row_per_origin():
    provider = AmapProvider("test")

    async def fake_get(path, **params):
        destination = params["destination"]
        # The upstream service may report a small non-zero self-route time.
        return {"results": [{"duration": "60" if origin == destination else "600"}
                            for origin in params["origins"].split("|")]}

    provider._get = fake_get
    matrix = await provider.get_distance_matrix([(30, 104), (31, 105)])
    assert matrix == [[0, 10], [10, 0]]


@pytest.mark.asyncio
async def test_agent_rejects_unknown_tool_and_records_retry_failure():
    class Input(BaseModel):
        value: int

    async def slow(_):
        await asyncio.sleep(0.02)
        return {"ok": True}

    traces = []

    async def record(data):
        traces.append(data)

    runtime = AgentRuntime([ToolSpec("slow", Input, slow, timeout_seconds=0.001, retry_limit=1)], record)
    with pytest.raises(ToolFailure, match="UNKNOWN_TOOL"):
        await runtime.call("unknown", {"value": 1})
    with pytest.raises(ToolFailure, match="TOOL_TIMEOUT"):
        await runtime.call("slow", {"value": 1})
    assert len(traces) == 2
    assert all(trace["error_code"] == "TOOL_TIMEOUT" for trace in traces)


def test_tool_trace_redacts_personal_queries_and_coordinates():
    import json

    safe = redact_tool_trace({
        "tool": "search_places", "status": "SUCCEEDED", "latency_ms": 12,
        "input_data": {"keyword": "用户私密地点", "city": "成都"},
        "output_data": {"status": "VERIFIED", "places": [{"name": "私密地点", "latitude": 30.1}]},
    })

    encoded = json.dumps(safe, ensure_ascii=False)
    assert "用户私密地点" not in encoded and "私密地点" not in encoded and "成都" not in encoded
    assert safe["input_data"]["query_length"] == len("用户私密地点")
    assert safe["output_data"]["places_count"] == 1


def test_tool_trace_preserves_runtime_attempt_metadata_without_raw_arguments():
    safe = redact_tool_trace({
        "tool": "search_places", "status": "FAILED", "latency_ms": 20,
        "input_data": {"keyword": "我的私密地点"}, "output_data": None,
        "error_code": "TOOL_RETRYABLE_FAILURE", "logical_step": 2, "attempt": 3,
        "tool_attempts_total": 7, "retryable": True,
    })

    assert safe["logical_step"] == 2
    assert safe["attempt"] == 3
    assert safe["tool_attempts_total"] == 7
    assert safe["retryable"] is True
    assert "keyword" not in safe["input_data"]
