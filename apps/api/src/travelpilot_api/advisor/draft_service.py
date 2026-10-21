"""Validate a user's editable draft selection before planning checks run."""

from datetime import date, time
from itertools import pairwise

from fastapi import HTTPException


def overlaps_time_window(
    start_minute: int,
    end_minute: int,
    window_start: int,
    window_end: int,
    *,
    minimum_overlap: int = 30,
) -> bool:
    """Return whether a scheduled stop meaningfully overlaps a meal window."""
    if end_minute < start_minute:
        return False
    if end_minute == start_minute:
        return window_start <= start_minute < window_end
    return min(end_minute, window_end) - max(start_minute, window_start) >= minimum_overlap


def rest_check_item(day: date, stops: list[tuple[time, time]]) -> dict[str, str] | None:
    """Suggest review for a long, tightly scheduled day; this is not a blocker."""
    if len(stops) < 3:
        return None
    ordered = sorted(stops, key=lambda item: item[0])
    span_minutes = (
        ordered[-1][1].hour * 60 + ordered[-1][1].minute
        - (ordered[0][0].hour * 60 + ordered[0][0].minute)
    )
    if span_minutes < 9 * 60:
        return None
    for previous, current in pairwise(ordered):
        gap_minutes = (
            current[0].hour * 60 + current[0].minute
            - (previous[1].hour * 60 + previous[1].minute)
        )
        if gap_minutes >= 45:
            return None
    return {"kind": "REST_CHECK", "day": day.isoformat()}


def meal_time_advisory(candidate: dict, start_time: time) -> str | None:
    """Flag a surprising explicit meal/time pairing without blocking user choice."""
    if candidate.get("planning_category") != "FOOD":
        return None
    meal_type = candidate.get("meal_type")
    windows = {
        "BREAKFAST": (6 * 60, 11 * 60, "早餐", "06:00–11:00"),
        "LUNCH": (11 * 60, 14 * 60, "午餐", "11:00–14:00"),
        "DINNER": (17 * 60, 20 * 60, "晚餐", "17:00–20:00"),
    }
    window = windows.get(meal_type)
    if window is None:
        return None
    start, end, label, display_window = window
    minutes = start_time.hour * 60 + start_time.minute
    if start <= minutes < end:
        return None
    name = str(candidate.get("name") or "该餐饮地点")[:100]
    return (
        f"{name}标记为{label}，当前安排在 {start_time.strftime('%H:%M')}，"
        f"不在常见参考时段 {display_window} 内；请确认是否符合你的作息。"
    )


def meal_schedule_advisories(
    selected: list[dict], schedule_by_id: dict[str, object],
) -> list[str]:
    """Build user-visible timing notes for selected, explicitly typed meals."""
    advisories = []
    for candidate in selected:
        if candidate.get("planning_category") != "FOOD":
            continue
        schedule = schedule_by_id.get(str(candidate.get("id", "")))
        start_time = getattr(schedule, "start_time", None)
        if isinstance(start_time, time):
            advisory = meal_time_advisory(candidate, start_time)
            if advisory:
                advisories.append(advisory)
    return advisories


def validate_draft_selection(
    candidates: list[dict], candidate_ids: list, schedules: list,
    start_date: date, end_date: date,
) -> tuple[list[dict], dict]:
    by_id = {str(candidate["id"]): candidate for candidate in candidates}
    selected_ids = [str(candidate_id) for candidate_id in candidate_ids]
    selected = [by_id.get(candidate_id) for candidate_id in selected_ids]
    if any(candidate is None for candidate in selected) or len(set(selected_ids)) != len(selected_ids):
        raise HTTPException(400, detail="候选地点无效或重复")
    locked_ids = {
        candidate_id for candidate_id, candidate in by_id.items()
        if candidate.get("fixed_schedule") and candidate.get("planning_category") != "LODGING"
    }
    if not locked_ids.issubset(set(selected_ids)):
        raise HTTPException(409, detail="草案不能移除固定预约地点；请先在行程计划中调整预约状态")
    schedule_by_id = {str(item.candidate_id): item for item in schedules}
    selected_set = set(selected_ids)
    timed_ids = {
        candidate_id for candidate_id in selected_set
        if by_id[candidate_id].get("planning_category") != "LODGING"
    }
    if len(schedule_by_id) != len(schedules) or set(schedule_by_id) != timed_ids:
        raise HTTPException(400, detail="每个游览或用餐地点都需要且只能设置一个日期和开始时间；住宿只加入住宿清单")
    if any(not start_date <= item.day <= end_date for item in schedules):
        raise HTTPException(400, detail="地点日期必须在旅行日期范围内")
    for candidate in selected:
        fixed = candidate.get("fixed_schedule") if candidate.get("planning_category") != "LODGING" else None
        if fixed:
            requested = schedule_by_id[candidate["id"]]
            if (
                requested.day.isoformat() != fixed.get("day")
                or requested.start_time.strftime("%H:%M") != fixed.get("start_time")
            ):
                raise HTTPException(
                    409,
                    detail=f"{candidate['name']} 是固定预约，Agent 不会擅自更改其日期和时间",
                )
    return selected, schedule_by_id
