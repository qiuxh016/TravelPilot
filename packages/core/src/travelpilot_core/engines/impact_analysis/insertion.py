"""Constraint-aware insertion of a user-requested visit into a fixed plan."""

from dataclasses import dataclass
from datetime import datetime, time
from uuid import UUID

from travelpilot_core.engines.impact_analysis.replan import Node


@dataclass(frozen=True)
class UserAddCandidate:
    poi_id: UUID
    name: str
    category: str
    duration_min: int
    priority: int
    opening_hours: dict | None


def find_user_add_slot(
    existing_nodes: list[Node],
    candidate: UserAddCandidate,
    effective_at: datetime,
    travel_minutes: dict[tuple[UUID, UUID], int],
    *,
    max_nodes_per_day: int = 6,
    minimum_transfer_min: int = 20,
    opening_time: time = time(9),
    closing_time: time = time(20),
) -> tuple[Node | None, str]:
    """Return the earliest feasible insertion without moving existing nodes.

    A missing route duration is not treated as zero. Callers must provide every
    edge used by a candidate slot whenever there are neighboring visits.
    """
    day = effective_at.date()
    day_nodes = sorted(
        (node for node in existing_nodes if node.day == day),
        key=lambda node: (node.start_time, node.stable_key),
    )
    if len(day_nodes) >= max_nodes_per_day:
        return None, "DAILY_CAPACITY"
    if candidate.duration_min <= 0:
        return None, "INVALID_DURATION"

    requested_minute = max(effective_at.hour * 60 + effective_at.minute, 0)
    opening_minute = opening_time.hour * 60 + opening_time.minute
    closing_minute = closing_time.hour * 60 + closing_time.minute
    hours = candidate.opening_hours or {}
    place_open = _minute(hours.get("open")) if hours.get("open") else None
    place_close = _minute(hours.get("close")) if hours.get("close") else None
    if place_open is None or place_close is None or place_close <= place_open:
        return None, "OPENING_HOURS_UNKNOWN"
    saw_unknown_route = False
    saw_opening_conflict = False
    saw_schedule_conflict = False

    for position in range(len(day_nodes) + 1):
        previous = day_nodes[position - 1] if position else None
        following = day_nodes[position] if position < len(day_nodes) else None
        incoming = 0
        outgoing = 0
        if previous is not None:
            value = travel_minutes.get((previous.poi_id, candidate.poi_id))
            if not _valid_duration(value):
                saw_unknown_route = True
                continue
            incoming = value
        if following is not None:
            value = travel_minutes.get((candidate.poi_id, following.poi_id))
            if not _valid_duration(value):
                saw_unknown_route = True
                continue
            outgoing = value

        previous_end = (
            previous.end_time.hour * 60 + previous.end_time.minute
            if previous is not None
            else opening_minute
        )
        start = max(
            requested_minute,
            opening_minute,
            place_open or 0,
            previous_end + incoming + (minimum_transfer_min if previous else 0),
        )
        end = start + candidate.duration_min
        if place_close is not None and end > place_close:
            saw_opening_conflict = True
            continue
        if end > closing_minute:
            saw_schedule_conflict = True
            continue
        if following is not None:
            following_start = following.start_time.hour * 60 + following.start_time.minute
            if end + outgoing + minimum_transfer_min > following_start:
                saw_schedule_conflict = True
                continue

        return (
            Node(
                candidate.poi_id,
                candidate.name,
                candidate.category,
                day,
                time(start // 60, start % 60),
                time(end // 60, end % 60),
                candidate.priority,
            ),
            "ADDED",
        )

    if saw_unknown_route:
        return None, "ROUTE_TIME_UNKNOWN"
    if saw_schedule_conflict:
        return None, "TIME_OR_ROUTE_CONFLICT"
    if saw_opening_conflict:
        return None, "CLOSED_AT_SLOT"
    return None, "TIME_OR_ROUTE_CONFLICT"


def _minute(value: str) -> int | None:
    try:
        parsed = time.fromisoformat(value)
    except (TypeError, ValueError):
        return None
    return parsed.hour * 60 + parsed.minute


def _valid_duration(value: int | None) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0
