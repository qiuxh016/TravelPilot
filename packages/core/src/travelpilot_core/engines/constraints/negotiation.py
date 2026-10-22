"""Bounded, deterministic repairs for itinerary conflicts.

The engine treats fixed and required activities as immutable. It proposes
only validated moves or removals of optional activities; callers still need
to ask the traveler to choose before changing a formal itinerary.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from itertools import combinations, pairwise, product
from typing import Literal


@dataclass(frozen=True)
class NegotiationActivity:
    id: str
    name: str
    day: date
    start_minute: int
    duration_minute: int
    priority: int = 3
    optional: bool = False
    required: bool = False
    fixed: bool = False
    category: Literal["ATTRACTION", "LUNCH", "DINNER", "OTHER"] = "ATTRACTION"
    opens_at: int | None = None
    closes_at: int | None = None

    @property
    def end_minute(self) -> int:
        return self.start_minute + self.duration_minute


@dataclass(frozen=True)
class ScheduleConflict:
    code: str
    activity_ids: tuple[str, ...]
    message: str


@dataclass(frozen=True)
class NegotiationAlternative:
    id: str
    title: str
    summary: str
    change_cost: int
    activities: tuple[NegotiationActivity, ...]
    changes: tuple[dict[str, object], ...]


@dataclass(frozen=True)
class NegotiationResult:
    feasible: bool
    conflicts: tuple[ScheduleConflict, ...]
    minimal_conflict_set: tuple[str, ...]
    alternatives: tuple[NegotiationAlternative, ...]
    explored_states: int
    search_truncated: bool


def negotiate_schedule(
    activities: list[NegotiationActivity],
    *,
    start_date: date,
    end_date: date,
    travel_minutes: dict[tuple[str, str], int],
    day_start_minute: int = 6 * 60,
    day_end_minute: int = 23 * 60,
    transfer_buffer_minute: int = 20,
    lunch_window: tuple[int, int] = (11 * 60, 14 * 60),
    dinner_window: tuple[int, int] = (17 * 60, 20 * 60),
    max_alternatives: int = 3,
    max_states: int = 4096,
) -> NegotiationResult:
    """Find low-cost feasible one-activity changes under hard bounds.

    Route times must be supplied for every adjacent pair. Missing route data is
    reported as a conflict, never assumed to be zero. The search is bounded by
    ``max_states`` and returns only alternatives accepted by the same validator
    used to identify the original conflict.
    """
    if end_date < start_date:
        raise ValueError("end_date precedes start_date")
    if (end_date - start_date).days > 13:
        raise ValueError("negotiation is limited to trips of 14 days")
    if not 1 <= max_alternatives <= 3:
        raise ValueError("max_alternatives must be between 1 and 3")
    if max_states < 1:
        raise ValueError("max_states must be positive")
    if not 0 <= day_start_minute < day_end_minute <= 24 * 60:
        raise ValueError("day window is invalid")
    if len(activities) > 30:
        raise ValueError("at most 30 activities can be negotiated")
    ids = [item.id for item in activities]
    if len(set(ids)) != len(ids) or any(not value for value in ids):
        raise ValueError("activity ids must be unique and nonempty")
    for item in activities:
        if not start_date <= item.day <= end_date:
            raise ValueError(f"{item.id} is outside the trip dates")
        if item.duration_minute <= 0 or not 0 <= item.start_minute < 24 * 60:
            raise ValueError(f"{item.id} has an invalid time or duration")
        if item.priority < 1 or item.priority > 5:
            raise ValueError(f"{item.id} priority must be between 1 and 5")
        if (
            item.opens_at is not None
            and item.closes_at is not None
            and item.opens_at >= item.closes_at
        ):
            raise ValueError(f"{item.id} has an invalid opening window")

    original = tuple(sorted(activities, key=_sort_key))
    conflicts = _find_conflicts(
        original,
        travel_minutes,
        day_start_minute,
        day_end_minute,
        transfer_buffer_minute,
        lunch_window,
        dinner_window,
    )
    if not conflicts:
        return NegotiationResult(True, (), (), (), 0, False)

    minimal_conflict_ids = tuple(dict.fromkeys(conflicts[0].activity_ids))
    conflict_ids = tuple(dict.fromkeys(
        item_id for conflict in conflicts for item_id in conflict.activity_ids
    ))
    by_id = {item.id: item for item in original}
    movable = [
        by_id[item_id]
        for item_id in conflict_ids
        if item_id in by_id and not by_id[item_id].fixed and not by_id[item_id].required
    ]
    alternatives: dict[tuple[tuple[str, date, int], ...], NegotiationAlternative] = {}
    variants_by_id: dict[str, list[NegotiationActivity | None]] = {}
    explored = 0
    truncated = False
    single_change_budget = max(1, max_states // 2)

    def consider(
        candidate_items: list[NegotiationActivity],
        changed: NegotiationActivity,
        operation: str,
        before: NegotiationActivity | None,
    ) -> None:
        nonlocal explored, truncated
        if explored >= single_change_budget:
            truncated = True
            return
        explored += 1
        candidate = tuple(sorted(candidate_items, key=_sort_key))
        if _find_conflicts(
            candidate,
            travel_minutes,
            day_start_minute,
            day_end_minute,
            transfer_buffer_minute,
            lunch_window,
            dinner_window,
        ):
            return
        signature = tuple((item.id, item.day, item.start_minute) for item in candidate)
        if operation == "MOVE" and before is not None:
            cost = 2 + (abs((changed.day - before.day).days) * 4)
            cost += abs(changed.start_minute - before.start_minute) // 30
            summary = (
                f"将{changed.name}调整到{changed.day.isoformat()} "
                f"{_clock(changed.start_minute)}，保留其他安排"
            )
            change = {
                "operation": "MOVE",
                "activity_id": changed.id,
                "name": changed.name,
                "before_day": before.day.isoformat(),
                "before_start": _clock(before.start_minute),
                "after_day": changed.day.isoformat(),
                "after_start": _clock(changed.start_minute),
            }
            title = "调整时间或日期"
        else:
            cost = 20 + (5 - changed.priority) * 3
            summary = f"暂时移除可选地点{changed.name}，保留其他安排"
            change = {
                "operation": "REMOVE",
                "activity_id": changed.id,
                "name": changed.name,
                "before_day": changed.day.isoformat(),
                "before_start": _clock(changed.start_minute),
                "after_day": None,
                "after_start": None,
            }
            title = "移除一个可选地点"
        alternatives.setdefault(
            signature,
            NegotiationAlternative(
                id=f"option-{changed.id}-{operation.lower()}-{changed.day.isoformat()}-{changed.start_minute}",
                title=title,
                summary=summary,
                change_cost=cost,
                activities=candidate,
                changes=(change,),
            ),
        )

    for activity in sorted(movable, key=lambda item: (item.priority, item.id)):
        if activity.optional and not activity.required:
            variants_by_id.setdefault(activity.id, []).append(None)
            consider(
                [item for item in original if item.id != activity.id],
                activity,
                "REMOVE",
                activity,
            )
        for target_day in _trip_days(start_date, end_date):
            if target_day == activity.day:
                starts = _candidate_starts(
                    activity,
                    target_day,
                    original,
                    travel_minutes,
                    day_start_minute,
                    day_end_minute,
                    transfer_buffer_minute,
                    lunch_window,
                    dinner_window,
                )
            else:
                starts = _candidate_starts(
                    activity,
                    target_day,
                    [item for item in original if item.id != activity.id],
                    travel_minutes,
                    day_start_minute,
                    day_end_minute,
                    transfer_buffer_minute,
                    lunch_window,
                    dinner_window,
                )
            for start in sorted(starts, key=lambda value: (abs(value - activity.start_minute), value)):
                if target_day == activity.day and start == activity.start_minute:
                    continue
                moved = NegotiationActivity(
                    **{**activity.__dict__, "day": target_day, "start_minute": start}
                )
                variants = variants_by_id.setdefault(activity.id, [])
                if len(variants) < 8:
                    variants.append(moved)
                changed_items = [moved if item.id == activity.id else item for item in original]
                consider(changed_items, moved, "MOVE", activity)
                if explored >= single_change_budget:
                    truncated = True
                    break
            if explored >= single_change_budget:
                break
        if explored >= single_change_budget:
            break

    # Repair independent conflicts with a bounded two-activity combination.
    # Keep at most eight deterministic variants per activity and validate each
    # combined schedule against the complete original constraint set.
    for first_id, second_id in combinations(variants_by_id, 2):
        for first_variant, second_variant in product(
            variants_by_id[first_id], variants_by_id[second_id]
        ):
            if explored >= max_states:
                truncated = True
                break
            explored += 1
            replacements = {first_id: first_variant, second_id: second_variant}
            candidate_items = [
                replacements.get(item.id, item)
                for item in original
                if replacements.get(item.id, item) is not None
            ]
            candidate = tuple(sorted(candidate_items, key=_sort_key))
            if _find_conflicts(
                candidate,
                travel_minutes,
                day_start_minute,
                day_end_minute,
                transfer_buffer_minute,
                lunch_window,
                dinner_window,
            ):
                continue
            signature = tuple((item.id, item.day, item.start_minute) for item in candidate)
            changes: list[dict[str, object]] = []
            cost = 0
            summaries: list[str] = []
            for item_id, variant in replacements.items():
                before = by_id[item_id]
                if variant is None:
                    operation = "REMOVE"
                    item_cost = 20 + (5 - before.priority) * 3
                    summaries.append(f"移除可选地点{before.name}")
                    after_day = after_start = None
                    changed_name = before.name
                else:
                    operation = "MOVE"
                    item_cost = 2 + abs((variant.day - before.day).days) * 4
                    item_cost += abs(variant.start_minute - before.start_minute) // 30
                    summaries.append(
                        f"将{variant.name}调整到{variant.day.isoformat()} "
                        f"{_clock(variant.start_minute)}"
                    )
                    after_day, after_start = variant.day.isoformat(), _clock(variant.start_minute)
                    changed_name = variant.name
                cost += item_cost
                changes.append({
                    "operation": operation,
                    "activity_id": item_id,
                    "name": changed_name,
                    "before_day": before.day.isoformat(),
                    "before_start": _clock(before.start_minute),
                    "after_day": after_day,
                    "after_start": after_start,
                })
            alternatives.setdefault(
                signature,
                NegotiationAlternative(
                    id=f"option-combined-{first_id}-{second_id}",
                    title="同时调整两项安排",
                    summary="；".join(summaries) + "，其他安排不变",
                    change_cost=cost,
                    activities=candidate,
                    changes=tuple(changes),
                ),
            )
        if explored >= max_states:
            break

    ranked = sorted(
        alternatives.values(),
        key=lambda option: (option.change_cost, option.title, option.id),
    )[:max_alternatives]
    return NegotiationResult(
        feasible=bool(ranked),
        conflicts=tuple(conflicts),
        minimal_conflict_set=minimal_conflict_ids,
        alternatives=tuple(ranked),
        explored_states=explored,
        search_truncated=truncated,
    )


def _find_conflicts(
    activities: tuple[NegotiationActivity, ...],
    travel_minutes: dict[tuple[str, str], int],
    day_start: int,
    day_end: int,
    transfer_buffer: int,
    lunch_window: tuple[int, int],
    dinner_window: tuple[int, int],
) -> list[ScheduleConflict]:
    conflicts: list[ScheduleConflict] = []
    for item in activities:
        if item.start_minute < day_start or item.end_minute > day_end:
            conflicts.append(
                ScheduleConflict(
                    "DAY_WINDOW",
                    (item.id,),
                    f"{item.name} 超出当天可安排时段",
                )
            )
        if item.opens_at is not None and item.start_minute < item.opens_at:
            conflicts.append(
                ScheduleConflict(
                    "NOT_OPEN",
                    (item.id,),
                    f"{item.name} 开始时尚未营业",
                )
            )
        if item.closes_at is not None and item.end_minute > item.closes_at:
            conflicts.append(
                ScheduleConflict(
                    "CLOSING_TIME",
                    (item.id,),
                    f"{item.name} 结束时间超过营业时间",
                )
            )
        if item.category == "LUNCH" and not _inside(item, lunch_window):
            conflicts.append(
                ScheduleConflict(
                    "LUNCH_WINDOW",
                    (item.id,),
                    f"{item.name} 不在建议午餐时段内",
                )
            )
        if item.category == "DINNER" and not _inside(item, dinner_window):
            conflicts.append(
                ScheduleConflict(
                    "DINNER_WINDOW",
                    (item.id,),
                    f"{item.name} 不在建议晚餐时段内",
                )
            )
    by_day: dict[date, list[NegotiationActivity]] = {}
    for item in activities:
        by_day.setdefault(item.day, []).append(item)
    for day_items in by_day.values():
        ordered = sorted(day_items, key=_sort_key)
        for previous, current in pairwise(ordered):
            if previous.end_minute > current.start_minute:
                conflicts.append(
                    ScheduleConflict(
                        "OVERLAP",
                        (previous.id, current.id),
                        f"{previous.name} 与 {current.name} 时间重叠",
                    )
                )
                continue
            route = travel_minutes.get((previous.id, current.id))
            if not _valid_route(route):
                conflicts.append(
                    ScheduleConflict(
                        "ROUTE_UNKNOWN",
                        (previous.id, current.id),
                        f"{previous.name} 到 {current.name} 的交通时间尚未核验",
                    )
                )
                continue
            if current.start_minute - previous.end_minute < route + transfer_buffer:
                conflicts.append(
                    ScheduleConflict(
                        "TRANSFER_GAP",
                        (previous.id, current.id),
                        f"{previous.name} 到 {current.name} 的时间不足以覆盖交通和缓冲",
                    )
                )
    return conflicts


def _candidate_starts(
    activity: NegotiationActivity,
    day: date,
    other_activities: tuple[NegotiationActivity, ...] | list[NegotiationActivity],
    travel_minutes: dict[tuple[str, str], int],
    day_start: int,
    day_end: int,
    transfer_buffer: int,
    lunch_window: tuple[int, int],
    dinner_window: tuple[int, int],
) -> list[int]:
    open_at = max(day_start, activity.opens_at or day_start)
    close_at = min(day_end, activity.closes_at or day_end)
    if activity.category == "LUNCH":
        open_at = max(open_at, lunch_window[0])
        close_at = min(close_at, lunch_window[1])
    elif activity.category == "DINNER":
        open_at = max(open_at, dinner_window[0])
        close_at = min(close_at, dinner_window[1])
    latest = close_at - activity.duration_minute
    if latest < open_at:
        return []
    relevant = sorted(
        (item for item in other_activities if item.day == day and item.id != activity.id),
        key=_sort_key,
    )
    slots = set(range(open_at, latest + 1, 15))
    for item in relevant:
        slots.update(
            (
                max(open_at, item.start_minute - activity.duration_minute),
                min(latest, item.end_minute + transfer_buffer),
            )
        )
    return sorted(slot for slot in slots if open_at <= slot <= latest)


def _inside(item: NegotiationActivity, window: tuple[int, int]) -> bool:
    return window[0] <= item.start_minute and item.end_minute <= window[1]


def _valid_route(value: int | None) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


def _sort_key(item: NegotiationActivity) -> tuple[date, int, str]:
    return item.day, item.start_minute, item.id


def _trip_days(start: date, end: date) -> list[date]:
    return [start + timedelta(days=offset) for offset in range((end - start).days + 1)]


def _clock(minute: int) -> str:
    return f"{minute // 60:02d}:{minute % 60:02d}"
