from datetime import date, time
from types import SimpleNamespace
from uuid import uuid4

import pytest
from travelpilot_api.advisor.context import refresh_trip_snapshot
from travelpilot_infra.db.models.plan import TripPlanModel, TripPlanNodeModel
from travelpilot_infra.db.models.trip import TripModel


@pytest.mark.asyncio
async def test_route_validation_snapshot_refresh_keeps_session_meal_lodging_and_selection():
    owner_id, trip_id, plan_id, poi_id, meal_poi_id = uuid4(), uuid4(), uuid4(), uuid4(), uuid4()
    start, end = date(2026, 11, 2), date(2026, 11, 3)
    trip = SimpleNamespace(
        id=trip_id,
        owner_id=owner_id,
        destination="梅州",
        start_date=start,
        end_date=end,
        budget=None,
        pace_level=3,
    )
    plan = SimpleNamespace(
        id=plan_id,
        version=4,
        status="READY",
        warnings=[],
        lodging=[{"name": "梅州确认酒店", "day": start.isoformat()}],
    )
    node = SimpleNamespace(
        poi_id=poi_id,
        note=None,
        status="PLANNED",
        day=start,
        start_time=time(9),
        end_time=time(11),
        priority=1,
    )
    poi = SimpleNamespace(
        id=poi_id,
        provider="amap",
        provider_id="museum",
        name="客家博物馆",
        address="梅州",
        category="ATTRACTION",
        latitude=24.3,
        longitude=116.1,
        suggested_duration_min=120,
        opening_hours=None,
        environment_profile=None,
    )

    class Database:
        async def scalar(self, statement):
            entity = statement.column_descriptions[0]["entity"]
            if entity is TripModel:
                return trip
            if entity is TripPlanModel:
                return plan
            raise AssertionError(f"unexpected scalar query: {entity}")

        async def execute(self, statement):
            entities = [column["entity"] for column in statement.column_descriptions]
            rows = [(node, poi)] if entities[0] is TripPlanNodeModel else []
            return SimpleNamespace(all=lambda: rows)

    row = SimpleNamespace(
        id=uuid4(),
        owner_id=owner_id,
        trip_id=trip_id,
        destination="梅州",
        start_date=start,
        end_date=end,
        budget=None,
        pace_level=3,
        preference_text="",
        draft=None,
        candidates=[
            {
                "id": "candidate-park",
                "existing_poi_id": str(poi_id),
                "name": "客家博物馆",
                "planning_category": "ATTRACTION",
            },
            {
                "id": "session-lunch",
                "existing_poi_id": str(meal_poi_id),
                "name": "已选午餐店",
                "planning_category": "FOOD",
                "meal_type": "LUNCH",
                "latitude": 24.31,
                "longitude": 116.11,
            },
            {
                "id": "session-hotel",
                "name": "已选住宿",
                "planning_category": "LODGING",
                "latitude": 24.32,
                "longitude": 116.12,
            },
        ],
        context={
            "revision": 8,
            "selected_candidate_ids": ["candidate-park", "session-lunch", "session-hotel"],
            "candidate_schedules": {
                "session-lunch": {"day": start.isoformat(), "start_time": "12:00"},
            },
            "current_plan": {"id": str(plan_id), "version": 4, "stops": []},
            "base_trip": {
                "destination": "梅州",
                "start_date": start.isoformat(),
                "end_date": end.isoformat(),
                "budget": None,
                "pace_level": 3,
            },
            "planning_inputs": {"origin": "梅州站"},
        },
    )
    before_selected = list(row.context["selected_candidate_ids"])

    await refresh_trip_snapshot(Database(), row)

    candidate_ids = {candidate["id"] for candidate in row.candidates}
    assert {"session-lunch", "session-hotel", "candidate-park"} <= candidate_ids
    assert row.context["selected_candidate_ids"] == before_selected
    assert row.context["candidate_schedules"]["session-lunch"] == {
        "day": start.isoformat(), "start_time": "12:00",
    }
    assert row.context["current_plan"]["stops"][0]["candidate_id"] == "candidate-park"
    assert row.context["current_plan"]["lodging"] == plan.lodging
