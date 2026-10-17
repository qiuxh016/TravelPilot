"""A reproducible, constraint aware itinerary builder.

The engine accepts plain values so it can be tested without a database or a map SDK.
"""

from dataclasses import dataclass
from datetime import date, time, timedelta
from uuid import UUID

from travelpilot_core.engines.geo.clustering import cluster_places


@dataclass(frozen=True)
class Candidate:
    poi_id: UUID
    name: str
    latitude: float
    longitude: float
    duration_min: int
    priority: int
    interest_score: float
    opening_hours: dict | None = None
    note: str | None = None


@dataclass(frozen=True)
class Visit:
    candidate: Candidate
    day: date
    start_time: time
    end_time: time


def _minutes(value: str | None, default: int) -> int:
    if not value:
        return default
    try:
        parsed = time.fromisoformat(value)
    except ValueError:
        return default
    return parsed.hour * 60 + parsed.minute


def _travel(a: Candidate, b: Candidate) -> int:
    # Stable estimate when the caller has no provider matrix.
    distance_km = ((a.latitude - b.latitude) ** 2 +
                   ((a.longitude - b.longitude) * 0.86) ** 2) ** 0.5 * 111
    return max(5, round(distance_km * 2.5))


def build_itinerary(
    start_date: date,
    end_date: date,
    candidates: list[Candidate],
    travel_matrix: list[list[int]] | None = None,
    *,
    buffer_min: int = 60,
    transfer_buffer_min: int = 0,
    max_pois_per_day: int = 6,
    start_travel_min: list[int] | None = None,
) -> tuple[list[Visit], list[str]]:
    """Group nearby POIs, order each day by proximity, then validate capacity.

    A skipped POI is reported as a warning and never silently disappears.
    Matrix indices correspond to the input candidate order.
    """
    if end_date < start_date:
        raise ValueError("end_date precedes start_date")
    count = len(candidates)
    if travel_matrix is not None and (
        len(travel_matrix) != count or any(len(row) != count for row in travel_matrix)
    ):
        raise ValueError("travel matrix dimensions do not match candidates")
    if start_travel_min is not None and (
        len(start_travel_min) != count or any(value < 0 for value in start_travel_min)
    ):
        raise ValueError("start travel estimates do not match candidates")
    days = (end_date - start_date).days + 1
    indexed = list(enumerate(candidates))
    groups = cluster_places([candidate for _, candidate in indexed])
    positions = {candidate.poi_id: index for index, candidate in indexed}
    groups.sort(key=lambda group: (-max(candidate.priority for candidate in group),
                                    min(candidate.name for candidate in group)))
    buckets: list[list[tuple[int, Candidate]]] = [[] for _ in range(days)]
    for group in groups:
        group.sort(key=lambda candidate: (-candidate.priority, -candidate.interest_score,
                                          candidate.name, str(candidate.poi_id)))
        for candidate in group:
            day_index = min(range(days), key=lambda day: (len(buckets[day]), day))
            buckets[day_index].append((positions[candidate.poi_id], candidate))

    visits: list[Visit] = []
    warnings: list[str] = []
    for day_index, bucket in enumerate(buckets):
        day = start_date + timedelta(days=day_index)
        remaining = bucket[:]
        previous: tuple[int, Candidate] | None = None
        cursor = 9 * 60
        while remaining:
            if previous is None:
                if day_index == 0 and start_travel_min is not None:
                    selected = min(remaining, key=lambda entry: (
                        start_travel_min[entry[0]], -entry[1].priority,
                        -entry[1].interest_score, entry[1].name,
                    ))
                    cursor += start_travel_min[selected[0]]
                    remaining.remove(selected)
                else:
                    selected = remaining.pop(0)
            else:
                selected = min(remaining, key=lambda entry: (
                    travel_matrix[previous[0]][entry[0]] if travel_matrix is not None
                    else _travel(previous[1], entry[1]),
                    -entry[1].priority, entry[1].name,
                ))
                remaining.remove(selected)
            index, candidate = selected
            if previous is not None:
                cursor += max(0, travel_matrix[previous[0]][index]) if travel_matrix is not None else _travel(previous[1], candidate)
                cursor += max(0, transfer_buffer_min)
            open_at = _minutes((candidate.opening_hours or {}).get("open"), 0)
            close_at = _minutes((candidate.opening_hours or {}).get("close"), 24 * 60)
            start = max(cursor, open_at)
            end = start + candidate.duration_min
            if len([visit for visit in visits if visit.day == day]) >= max_pois_per_day:
                warnings.append(f"{day} 超过每日 {max_pois_per_day} 个地点：{candidate.name}")
            elif end > 21 * 60 - buffer_min:
                warnings.append(f"{day} 时间与缓冲不足：{candidate.name}")
            elif end > close_at:
                warnings.append(f"{candidate.name} 超出营业时间")
            else:
                visits.append(Visit(candidate, day, time(start // 60, start % 60),
                                    time(end // 60, end % 60)))
                cursor = end
                previous = selected
    return visits, warnings
