from datetime import UTC, date, datetime, timedelta

import pytest
from travelpilot_api.advisor.evidence_reuse import (
    reusable_route_from_pending_turn,
    reusable_weather_from_pending_turn,
)

NOW = datetime(2026, 10, 19, 12, 0, tzinfo=UTC)
PENDING = {
    "pending_task": "安排收藏地点并查询路线和天气",
    "pending_task_id": "task-1",
    "context_revision": 4,
}


def _forecast(*, city="成都", fetched_at=None, days=None):
    return {
        "status": "AVAILABLE",
        "city": city,
        "source": "Local simulated forecast",
        "fetched_at": (fetched_at or NOW).isoformat(),
        "days": days or [
            {
                "date": "2026-10-22", "description": "小雨", "weather_code": 61,
                "temperature_min_c": 14, "temperature_max_c": 21,
                "precipitation_probability_max": 70,
            },
            {
                "date": "2026-10-23", "description": "局部多云", "weather_code": 2,
                "temperature_min_c": 14, "temperature_max_c": 21,
                "precipitation_probability_max": 20,
            },
        ],
    }


def test_reuses_recent_weather_for_the_same_pending_task_and_trip_scope():
    payload = {**PENDING, "weather_forecast": _forecast()}

    result = reusable_weather_from_pending_turn(
        payload,
        task_id="task-1",
        context_revision=4,
        city="成都",
        start_date=date(2026, 10, 22),
        end_date=date(2026, 10, 23),
        source="Local simulated forecast",
        now=NOW,
    )

    assert result == payload["weather_forecast"]


@pytest.mark.parametrize(
    ("task_id", "revision", "city", "days", "age"),
    [
        ("other-task", 4, "成都", None, timedelta()),
        ("task-1", 5, "成都", None, timedelta()),
        ("task-1", 4, "重庆", None, timedelta()),
        ("task-1", 4, "成都", [
            {"date": "2026-10-22", "description": "小雨", "weather_code": 61,
             "temperature_min_c": 14, "temperature_max_c": 21,
             "precipitation_probability_max": 70},
        ], timedelta()),
        ("task-1", 4, "成都", None, timedelta(hours=7)),
    ],
)
def test_rejects_weather_from_wrong_scope_incomplete_dates_or_stale_age(
    task_id, revision, city, days, age,
):
    payload = {**PENDING, "weather_forecast": _forecast(
        city=city, fetched_at=NOW - age, days=days,
    )}

    result = reusable_weather_from_pending_turn(
        payload,
        task_id=task_id,
        context_revision=revision,
        city="成都",
        start_date=date(2026, 10, 22),
        end_date=date(2026, 10, 23),
        source="Local simulated forecast",
        now=NOW,
    )

    assert result is None


def _route_payload(*, origin="人民公园", destination="武侯祠", retrieved_at=None):
    return {
        **PENDING,
        "agent_trace": {"stage_statuses": [
            {"stage": "ROUTE", "status": "SUCCEEDED"},
        ]},
        "transport_route": {
            "origin_place_id": "place-a" if origin == "人民公园" else "place-b",
            "destination_place_id": "place-b" if destination == "武侯祠" else "place-a",
            "origin_name": origin,
            "destination_name": destination,
            "mode": "DRIVING",
            "source": "Local simulated maps",
            "retrieved_at": (retrieved_at or NOW).isoformat(),
        },
    }


PLACES = [
    {"id": "place-a", "name": "人民公园", "latitude": 30.66, "longitude": 104.05},
    {"id": "place-b", "name": "武侯祠", "latitude": 30.65, "longitude": 104.04},
    {"id": "place-c", "name": "锦里", "latitude": 30.64, "longitude": 104.05},
]


def test_reuses_recent_route_only_for_same_explicit_endpoint_pair():
    payload = _route_payload()

    result = reusable_route_from_pending_turn(
        payload,
        task_id="task-1",
        context_revision=4,
        user_goal="安排三处，并查人民公园到武侯祠的驾车路线",
        places=PLACES,
        source="Local simulated maps",
        now=NOW,
    )

    assert result is payload["transport_route"]


@pytest.mark.parametrize(
    ("payload", "goal", "source"),
    [
        ({**_route_payload(), "context_revision": 5}, "查人民公园到武侯祠路线", "Local simulated maps"),
        (_route_payload(origin="武侯祠", destination="人民公园"), "查人民公园到武侯祠路线", "Local simulated maps"),
        (_route_payload(), "查人民公园到锦里路线", "Local simulated maps"),
        (_route_payload(retrieved_at=NOW - timedelta(hours=7)), "查人民公园到武侯祠路线", "Local simulated maps"),
        ({**_route_payload(), "agent_trace": {"stage_statuses": []}}, "查人民公园到武侯祠路线", "Local simulated maps"),
        (_route_payload(), "查人民公园到武侯祠路线", "AMap Web Service"),
    ],
)
def test_rejects_route_from_wrong_task_endpoint_provider_or_age(payload, goal, source):
    result = reusable_route_from_pending_turn(
        payload,
        task_id="task-1",
        context_revision=4,
        user_goal=goal,
        places=PLACES,
        source=source,
        now=NOW,
    )

    assert result is None
