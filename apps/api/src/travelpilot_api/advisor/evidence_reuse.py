"""Scope checks for reusing verified read-only evidence after a user answer."""

from datetime import UTC, date, datetime, timedelta
from typing import Any

from travelpilot_api.advisor.route_query import _resolve_explicit_places
from travelpilot_api.advisor.weather_evidence import valid_weather_evidence


def _same_pending_task(
    payload: dict[str, Any], *, task_id: str | None, context_revision: int,
) -> bool:
    return bool(
        task_id
        and payload.get("pending_task_id") == task_id
        and payload.get("pending_task")
        and payload.get("context_revision") == context_revision
    )


def pending_evidence_scope_matches(
    payload: dict[str, Any] | None, *, task_id: str | None, context_revision: int,
) -> bool:
    return bool(
        isinstance(payload, dict)
        and _same_pending_task(
            payload, task_id=task_id, context_revision=context_revision,
        )
    )


def pending_evidence_scope_status(
    payload: dict[str, Any] | None, *, task_id: str | None, context_revision: int,
) -> str:
    if not isinstance(payload, dict):
        return "NO_PREVIOUS_PAYLOAD"
    if not task_id:
        return "NO_CONTINUING_TASK_ID"
    if not payload.get("pending_task"):
        return "NOT_A_PENDING_TASK"
    if payload.get("pending_task_id") != task_id:
        return "TASK_ID_MISMATCH"
    if payload.get("context_revision") != context_revision:
        return "CONTEXT_REVISION_MISMATCH"
    return "MATCH"


def reusable_weather_from_pending_turn(
    payload: dict[str, Any] | None,
    *,
    task_id: str | None,
    context_revision: int,
    city: str | None,
    start_date: date | None,
    end_date: date | None,
    source: str,
    now: datetime | None = None,
) -> dict[str, Any] | None:
    """Reuse a recent same-task forecast only when it covers the full trip."""
    if (
        not isinstance(payload, dict)
        or not _same_pending_task(payload, task_id=task_id, context_revision=context_revision)
        or not city or not start_date or not end_date
    ):
        return None
    forecast = payload.get("weather_forecast")
    if not valid_weather_evidence(forecast, expected_source=source):
        return None
    if forecast["city"].strip().casefold() != city.strip().casefold():
        return None
    fetched_at = forecast["fetched_at"]
    fetched = fetched_at if isinstance(fetched_at, datetime) else datetime.fromisoformat(fetched_at)
    current = now or datetime.now(UTC)
    if fetched > current + timedelta(minutes=2) or current - fetched > timedelta(hours=6):
        return None
    dates = {item["date"] for item in forecast["days"]}
    expected_dates = {
        (start_date + timedelta(days=offset)).isoformat()
        for offset in range((end_date - start_date).days + 1)
    }
    if not expected_dates or not expected_dates <= dates:
        return None
    return forecast


def reusable_route_from_pending_turn(
    payload: dict[str, Any] | None,
    *,
    task_id: str | None,
    context_revision: int,
    user_goal: str,
    places: list[dict[str, Any]],
    source: str,
    now: datetime | None = None,
) -> dict[str, Any] | None:
    """Reuse only a recent route for the same explicit endpoints and task."""
    if (
        not isinstance(payload, dict)
        or not _same_pending_task(payload, task_id=task_id, context_revision=context_revision)
    ):
        return None
    trace = payload.get("agent_trace")
    statuses = trace.get("stage_statuses", []) if isinstance(trace, dict) else []
    if not any(
        isinstance(item, dict)
        and item.get("stage") == "ROUTE"
        and item.get("status") == "SUCCEEDED"
        for item in statuses
    ):
        return None
    route = payload.get("transport_route")
    if not isinstance(route, dict) or route.get("source") != source:
        return None
    endpoints = _resolve_explicit_places(user_goal, places)
    if len(endpoints) != 2 or [route.get("origin_place_id"), route.get("destination_place_id")] != [
        str(item.get("id")) for item in endpoints
    ]:
        return None
    retrieved_at = route.get("retrieved_at")
    if not isinstance(retrieved_at, str):
        return None
    try:
        retrieved = datetime.fromisoformat(retrieved_at)
    except ValueError:
        return None
    if retrieved.tzinfo is None or retrieved.utcoffset() is None:
        return None
    current = now or datetime.now(UTC)
    if retrieved > current + timedelta(minutes=2) or current - retrieved > timedelta(hours=6):
        return None
    return route
