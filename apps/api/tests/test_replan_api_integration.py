from datetime import UTC, date, datetime, time
from decimal import Decimal
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi import HTTPException
from travelpilot_api.api.v1 import events as events_api
from travelpilot_api.schemas.events import ReplanRequest
from travelpilot_infra.db.models.plan import TripPlanModel, TripPlanNodeModel
from travelpilot_infra.db.models.poi import PoiModel
from travelpilot_infra.db.models.trip import TripModel
from travelpilot_infra.db.models.wishlist import WishlistItemModel
from travelpilot_infra.db.models.workflow import TripEventModel, WorkflowRunModel


class Result:
    def __init__(self, rows=()):
        self.rows = rows

    def all(self):
        return self.rows

    def one_or_none(self):
        return self.rows[0] if self.rows else None


class FakeMaps:
    provider = SimpleNamespace()

    def __init__(self, fail=False):
        self.fail = fail
        self.calls = 0

    async def get_distance_matrix(self, points):
        self.calls += 1
        if self.fail:
            raise events_api.MapsProviderError("upstream unavailable")
        assert len(points) == 2
        return [[0, 10], [10, 0]]


class FakeSession:
    def __init__(self, trip, event, candidate_poi, wishlist_item):
        self.objects = {
            TripModel: trip,
            TripEventModel: event,
            PoiModel: candidate_poi,
            WishlistItemModel: wishlist_item,
        }
        self.query_count = 0
        self.added = []

    async def get(self, model, _identity):
        return self.objects.get(model)

    async def execute(self, _query):
        self.query_count += 1
        if self.query_count == 1:
            return Result()
        return Result([(self.objects[WishlistItemModel], self.objects[PoiModel])])

    def add(self, obj):
        self.added.append(obj)

    def add_all(self, objects):
        self.added.extend(objects)

    async def flush(self):
        for obj in self.added:
            if isinstance(obj, TripPlanModel) and obj.id is None:
                obj.id = uuid4()

    async def commit(self):
        return None


@pytest.mark.asyncio
@pytest.mark.parametrize("map_failure", [False, True])
async def test_weather_replan_api_uses_route_matrix_and_never_fabricates_missing_routes(
    monkeypatch,
    map_failure,
):
    trip_id = uuid4()
    removed_id, following_id, candidate_id = uuid4(), uuid4(), uuid4()
    trip = SimpleNamespace(
        id=trip_id,
        start_date=date(2026, 10, 1),
        end_date=date(2026, 10, 2),
        status="READY",
    )
    event = SimpleNamespace(
        id=uuid4(),
        trip_id=trip_id,
        event_type="WEATHER_CHANGE",
        effective_at=datetime(2026, 10, 1, 6, tzinfo=UTC),
        effective_timezone_offset_minutes=480,
        payload={"condition": "RAIN"},
    )
    previous_plan = SimpleNamespace(id=uuid4(), version=1)
    removed_poi = PoiModel(
        id=removed_id,
        name="雨天公园",
        category="ATTRACTION",
        latitude=Decimal("30.6"),
        longitude=Decimal(104),
        suggested_duration_min=60,
        opening_hours=None,
        environment_profile={"exposure": "OUTDOOR", "sensitivity": {"RAIN": "HIGH"}},
    )
    following_poi = PoiModel(
        id=following_id,
        name="后续景点",
        category="ATTRACTION",
        latitude=Decimal("30.7"),
        longitude=Decimal("104.1"),
        suggested_duration_min=60,
        opening_hours=None,
        environment_profile={"exposure": "INDOOR"},
    )
    candidate_poi = PoiModel(
        id=candidate_id,
        name="室内博物馆",
        category="MUSEUM",
        latitude=Decimal("30.65"),
        longitude=Decimal("104.05"),
        suggested_duration_min=45,
        opening_hours={"open": "09:00", "close": "20:00"},
        environment_profile=None,
    )
    wishlist_item = WishlistItemModel(
        id=uuid4(),
        trip_id=trip_id,
        poi_id=candidate_id,
        priority=4,
        interest_score=Decimal("0.8"),
        environment_profile={"exposure": "INDOOR", "source": "USER"},
    )
    old_rows = [
        (
            TripPlanNodeModel(
                id=uuid4(),
                plan_id=previous_plan.id,
                poi_id=removed_id,
                day=date(2026, 10, 1),
                start_time=time(14),
                end_time=time(15),
                priority=3,
                status="PLANNED",
                note=None,
            ),
            removed_poi,
        ),
        (
            TripPlanNodeModel(
                id=uuid4(),
                plan_id=previous_plan.id,
                poi_id=following_id,
                day=date(2026, 10, 1),
                start_time=time(16),
                end_time=time(17),
                priority=3,
                status="PLANNED",
                note=None,
            ),
            following_poi,
        ),
    ]

    class Repository:
        def __init__(self, _session):
            self.calls = 0

        async def list_by_trip(self, _trip_id):
            return [previous_plan]

        async def get_with_nodes(self, _trip_id, _plan_id):
            self.calls += 1
            if self.calls == 1:
                return previous_plan, old_rows
            plan = next(obj for obj in session.added if isinstance(obj, TripPlanModel))
            return plan, old_rows

        async def next_version(self, _trip_id):
            return 2

    session = FakeSession(trip, event, candidate_poi, wishlist_item)
    maps = FakeMaps(fail=map_failure)
    monkeypatch.setattr(events_api, "PlanRepository", Repository)
    monkeypatch.setattr(events_api, "get_maps_provider", lambda: maps)
    monkeypatch.setattr(events_api, "to_read", lambda plan, _rows: {"id": plan.id})

    result = await events_api.create_replan(
        trip_id,
        ReplanRequest(event_id=event.id),
        session=session,
        _trip=trip,
    )

    operations = {change["operation"] for change in result["diff"]}
    assert maps.calls == 1
    assert "REMOVE" in operations
    plan = next(obj for obj in session.added if isinstance(obj, TripPlanModel))
    assert any("地图路线耗时不可用" in warning for warning in plan.warnings) is map_failure
    assert (
        any("天气替换：雨天公园 → 室内博物馆" in warning for warning in plan.warnings)
        is not map_failure
    )
    assert ("ADD" in operations) is not map_failure
    workflow = next(obj for obj in session.added if isinstance(obj, WorkflowRunModel))
    assert workflow.input_data["skill"] is None
    assert workflow.input_data["skill_applicability"]["selected"] is False
    assert workflow.input_data["skill_applicability"]["reason_code"] == "EXCLUDED_CONTEXT"
    assert workflow.input_data["skill_applicability"]["matched_exclusions"] == ["simulated_weather"]
    search = workflow.output_data["replacement_search"]
    assert search["objective"]["filled_visits"] == (0 if map_failure else 1)
    assert search["objective"]["plan_change_cost"] == (3 if map_failure else 6)
    assert search["objective_weights"]["route_time_delta_min"] == 0.1
    assert search["beam_width"] == 128
    assert len(search["diagnostics"]) == 1
    assert search["diagnostics"][0]["outcome"] == ("UNFILLED" if map_failure else "REPLACED")
    if map_failure:
        assert search["assignments"][0]["reason_code"] == "ROUTE_TIME_UNKNOWN"
        assert search["diagnostics"][0]["candidate_rejection_state_counts"] == {
            "ROUTE_TIME_UNKNOWN": 1,
        }
        assert "地图服务" in search["assignments"][0]["suggestion"]
        assert any(warning.startswith("建议：") for warning in plan.warnings)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("map_failure", "route_minutes", "expected_reason"),
    [
        (False, 10, "ADDED"),
        (True, 10, "ROUTE_TIME_UNKNOWN"),
        (False, 420, "TIME_OR_ROUTE_CONFLICT"),
    ],
)
async def test_user_add_replan_requires_route_feasibility_and_preserves_booking(
    monkeypatch, map_failure, route_minutes, expected_reason
):
    trip_id = uuid4()
    fixed_id, candidate_id = uuid4(), uuid4()
    trip = SimpleNamespace(
        id=trip_id,
        start_date=date(2026, 10, 17),
        end_date=date(2026, 10, 18),
        status="READY",
    )
    event = SimpleNamespace(
        id=uuid4(),
        trip_id=trip_id,
        event_type="USER_ADD",
        effective_at=datetime(2026, 10, 17, 3, tzinfo=UTC),
        effective_timezone_offset_minutes=480,
        payload={"poi_id": str(candidate_id)},
    )
    previous_plan = SimpleNamespace(id=uuid4(), version=1)
    fixed_poi = PoiModel(
        id=fixed_id,
        name="预约活动",
        category="ATTRACTION",
        latitude=Decimal("30.60"),
        longitude=Decimal("104.00"),
        suggested_duration_min=60,
        opening_hours={"open": "09:00", "close": "20:00"},
    )
    candidate_poi = PoiModel(
        id=candidate_id,
        name="新增地点",
        category="ATTRACTION",
        latitude=Decimal("30.61"),
        longitude=Decimal("104.01"),
        suggested_duration_min=45,
        opening_hours={"open": "09:00", "close": "20:00"},
    )
    wishlist_item = WishlistItemModel(
        id=uuid4(), trip_id=trip_id, poi_id=candidate_id, priority=4,
        interest_score=Decimal("0.8"),
    )
    fixed_node = TripPlanNodeModel(
        id=uuid4(), plan_id=previous_plan.id, poi_id=fixed_id,
        day=date(2026, 10, 17), start_time=time(14), end_time=time(15),
        priority=5, status="FIXED", note="confirmed booking",
    )
    old_rows = [(fixed_node, fixed_poi)]

    class Repository:
        def __init__(self, _session):
            self.calls = 0

        async def list_by_trip(self, _trip_id):
            return [previous_plan]

        async def get_with_nodes(self, _trip_id, _plan_id):
            self.calls += 1
            if self.calls == 1:
                return previous_plan, old_rows
            plan = next(obj for obj in session.added if isinstance(obj, TripPlanModel))
            return plan, old_rows

        async def next_version(self, _trip_id):
            return 2

    class Maps:
        async def get_distance_matrix(self, points):
            assert len(points) == 2
            if map_failure:
                raise events_api.MapsProviderError("unavailable")
            return [[0, route_minutes], [route_minutes, 0]]

    session = FakeSession(trip, event, candidate_poi, wishlist_item)
    monkeypatch.setattr(events_api, "PlanRepository", Repository)
    monkeypatch.setattr(events_api, "get_maps_provider", lambda: Maps())
    monkeypatch.setattr(events_api, "to_read", lambda plan, _rows: {"id": plan.id})

    await events_api.create_replan(
        trip_id, ReplanRequest(event_id=event.id), session=session, _trip=trip
    )

    workflow = next(obj for obj in session.added if isinstance(obj, WorkflowRunModel))
    assert workflow.output_data["user_add"]["reason_code"] == expected_reason
    assert workflow.output_data["user_add"]["outcome"] == (
        "ADDED" if expected_reason == "ADDED" else "REJECTED"
    )
    new_plan_nodes = [obj for obj in session.added if isinstance(obj, TripPlanNodeModel)]
    fixed_after = next(node for node in new_plan_nodes if node.poi_id == fixed_id)
    assert (fixed_after.start_time, fixed_after.end_time, fixed_after.status) == (
        time(14), time(15), "FIXED"
    )
    candidate_nodes = [node for node in new_plan_nodes if node.poi_id == candidate_id]
    assert len(candidate_nodes) == (1 if expected_reason == "ADDED" else 0)
    if candidate_nodes:
        assert candidate_nodes[0].start_time == time(11)
        assert candidate_nodes[0].end_time == time(11, 45)


@pytest.mark.asyncio
async def test_user_add_replan_rejects_event_outside_trip_dates(monkeypatch):
    trip_id = uuid4()
    trip = SimpleNamespace(
        id=trip_id,
        start_date=date(2026, 10, 17),
        end_date=date(2026, 10, 18),
        status="READY",
    )
    event = SimpleNamespace(
        id=uuid4(),
        trip_id=trip_id,
        event_type="USER_ADD",
        effective_at=datetime(2026, 10, 19, 3, tzinfo=UTC),
        effective_timezone_offset_minutes=480,
        payload={"poi_id": str(uuid4())},
    )
    existing_id = uuid4()
    existing_poi = PoiModel(
        id=existing_id,
        name="原有地点",
        category="ATTRACTION",
        latitude=Decimal("30.60"),
        longitude=Decimal("104.00"),
        suggested_duration_min=60,
    )
    previous_plan = SimpleNamespace(id=uuid4(), version=1)
    old_rows = [(
        TripPlanNodeModel(
            id=uuid4(), plan_id=previous_plan.id, poi_id=existing_id,
            day=date(2026, 10, 17), start_time=time(10), end_time=time(11),
            priority=3, status="PLANNED", note=None,
        ),
        existing_poi,
    )]

    class Repository:
        def __init__(self, _session):
            pass

        async def list_by_trip(self, _trip_id):
            return [previous_plan]

        async def get_with_nodes(self, _trip_id, _plan_id):
            return previous_plan, old_rows

    session = FakeSession(
        trip,
        event,
        existing_poi,
        SimpleNamespace(trip_id=trip_id, poi_id=existing_id),
    )
    monkeypatch.setattr(events_api, "PlanRepository", Repository)

    with pytest.raises(HTTPException) as error:
        await events_api.create_replan(
            trip_id, ReplanRequest(event_id=event.id), session=session, _trip=trip
        )

    assert error.value.status_code == 422
    assert error.value.detail == "Event is outside trip dates"
