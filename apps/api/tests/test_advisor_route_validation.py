from datetime import date
from types import SimpleNamespace

import pytest
from travelpilot_api.advisor.context import trip_completeness_audit
from travelpilot_api.advisor.route_evidence import reusable_route_evidence
from travelpilot_api.advisor.route_validation import (
    itinerary_route_summary,
    validate_active_itinerary_routes,
)
from travelpilot_core.ports.maps import Route
from travelpilot_infra.providers.maps.errors import MapsProviderError
from travelpilot_infra.providers.maps.fake import FakeMapsProvider


def _trip(*, second_has_coordinates=True):
    candidates = [
        {"id": "a", "name": "客家博物馆", "latitude": 24.30, "longitude": 116.10},
        {"id": "lunch", "name": "已选午餐", "planning_category": "FOOD"},
        {
            "id": "b",
            "name": "东山书院",
            **({"latitude": 24.31, "longitude": 116.11} if second_has_coordinates else {}),
        },
    ]
    stops = [
        {"candidate_id": "a", "name": "客家博物馆", "day": "2026-11-02", "start_time": "09:00"},
        {"candidate_id": "b", "name": "东山书院", "day": "2026-11-02", "start_time": "12:00"},
    ]
    context = {
        "revision": 3,
        "selected_candidate_ids": ["a", "lunch", "b"],
        "current_plan": {"stops": stops},
        "planning_inputs": {"origin": "梅州站"},
    }
    row = SimpleNamespace(
        candidates=candidates,
        destination="梅州",
        start_date=date(2026, 11, 2),
        end_date=date(2026, 11, 2),
        draft=None,
    )
    return row, context


class AmapProvider:
    def __init__(self):
        self.calls = []
        self.route_calls = []

    async def get_distance_matrix(self, points):
        self.calls.append(points)
        return [[0, 18], [18, 0]]

    async def get_route(self, origin, destination, mode="DRIVING"):
        self.route_calls.append((origin, destination, mode))
        return Route(origin, destination, 1200, 15, "amap", (origin, destination), mode)


@pytest.mark.asyncio
async def test_itinerary_validation_persists_verified_matrix_without_changing_selections():
    row, context = _trip()
    maps = AmapProvider()
    prior_selected = list(context["selected_candidate_ids"])

    result = await validate_active_itinerary_routes(
        row=row,
        context=context,
        maps=maps,
        user_goal="校验路线",
    )

    assert result.error is None
    assert result.status == "VERIFIED"
    assert result.candidate_ids == ["a", "b"]
    assert result.matrix == [[0, 18], [18, 0]]
    assert len(maps.calls) == 1
    assert context["agent_route_evidence"]["context_revision"] == 3
    assert context["selected_candidate_ids"] == prior_selected
    assert "客家博物馆 → 东山书院约 18 分钟" in itinerary_route_summary(result)
    assert "地点之间的实际交通路线尚未核验" not in trip_completeness_audit(row, context)


@pytest.mark.asyncio
async def test_itinerary_validation_reports_missing_coordinates_without_partial_success():
    row, context = _trip(second_has_coordinates=False)
    maps = AmapProvider()

    result = await validate_active_itinerary_routes(
        row=row,
        context=context,
        maps=maps,
        user_goal="校验路线",
    )

    assert result.matrix is None
    assert "东山书院" in result.error
    assert maps.calls == []
    assert "agent_route_evidence" not in context


@pytest.mark.asyncio
async def test_itinerary_validation_failure_keeps_previous_plan_and_gives_retry_path():
    class MapsUnavailable(AmapProvider):
        async def get_distance_matrix(self, points):
            self.calls.append(points)
            raise MapsProviderError("fixture outage")

    row, context = _trip()
    maps = MapsUnavailable()
    original_plan = context["current_plan"]
    original_selected = list(context["selected_candidate_ids"])

    result = await validate_active_itinerary_routes(
        row=row,
        context=context,
        maps=maps,
        user_goal="校验路线",
    )

    assert result.matrix is None
    assert result.error and "稍后重试" in result.error
    assert context["current_plan"] is original_plan
    assert context["selected_candidate_ids"] == original_selected
    assert "agent_route_evidence" not in context


@pytest.mark.asyncio
async def test_current_draft_is_validated_instead_of_outdated_formal_plan():
    row, context = _trip()
    row.draft = {
        "context_revision": 3,
        "stops": [
            {"candidate_id": "a", "name": "客家博物馆", "day": "2026-11-02", "start_time": "09:00"},
            {"candidate_id": "b", "name": "东山书院", "day": "2026-11-02", "start_time": "12:00"},
        ],
    }
    maps = AmapProvider()

    result = await validate_active_itinerary_routes(
        row=row,
        context=context,
        maps=maps,
        user_goal="校验当前调整草案路线",
    )

    assert result.error is None
    assert result.candidate_ids == ["a", "b"]
    assert context["agent_route_evidence"]["candidate_ids"] == ["a", "b"]


@pytest.mark.asyncio
async def test_explicit_walking_validation_uses_walking_routes_and_is_not_reused_for_driving():
    row, context = _trip()
    maps = AmapProvider()

    result = await validate_active_itinerary_routes(
        row=row,
        context=context,
        maps=maps,
        user_goal="请按步行核验路线",
    )

    assert result.error is None
    assert result.mode == "WALKING"
    assert result.matrix[0][1] == 15
    assert maps.calls == []
    assert maps.route_calls == [((24.3, 116.1), (24.31, 116.11), "WALKING")]
    assert "步行路线" in itinerary_route_summary(result)
    assert (
        reusable_route_evidence(
            context["agent_route_evidence"],
            result.candidate_ids,
            3,
            expected_source="AMap Web Service",
            expected_mode="DRIVING",
        )
        is None
    )


@pytest.mark.asyncio
async def test_simulated_map_result_is_disclosed_and_does_not_close_real_route_gap():
    row, context = _trip()

    result = await validate_active_itinerary_routes(
        row=row,
        context=context,
        maps=FakeMapsProvider(),
        user_goal="校验路线",
    )

    summary = itinerary_route_summary(result)
    assert result.status == "SIMULATED"
    assert "本地模拟地图" in summary
    assert "已核验当前行程" not in summary
    assert "地点之间的实际交通路线尚未核验" in trip_completeness_audit(row, context)
