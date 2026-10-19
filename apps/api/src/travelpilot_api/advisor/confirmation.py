"""Fail-closed checks required before a proposed itinerary can be committed."""

from datetime import date, time
from itertools import pairwise

from fastapi import HTTPException


def validate_confirmation_draft(
    draft: dict | None,
    draft_version: int,
    submitted_version: int,
    current_context_revision: int,
    trip_start_date: date | None = None,
    trip_end_date: date | None = None,
    current_plan: dict | None = None,
) -> None:
    if not draft or submitted_version != draft_version:
        raise HTTPException(409, detail="草案版本已变化，请刷新后确认最新版本")
    if not draft.get("stops"):
        raise HTTPException(409, detail="草案中没有可保存的已安排地点")
    blockers = [warning for warning in draft.get("warnings", []) if str(warning).startswith("阻断：")]
    if blockers:
        raise HTTPException(
            409,
            detail="行程存在时间或交通冲突，请调整地点时间后重新生成：" + "；".join(blockers),
        )
    if draft.get("context_revision", 0) != current_context_revision:
        raise HTTPException(409, detail="对话偏好或地点清单已变化，请重新生成草案")
    stops = draft["stops"]
    selected_ids = draft.get("selected_candidate_ids")
    if not isinstance(stops, list) or not isinstance(selected_ids, list):
        raise HTTPException(409, detail="草案地点数据无效，请重新生成草案")
    if (
        any(not isinstance(candidate_id, str) or not candidate_id for candidate_id in selected_ids)
        or len(set(selected_ids)) != len(selected_ids)
    ):
        raise HTTPException(409, detail="草案已选地点重复或标识无效，请重新生成草案")
    seen_ids: set[str] = set()
    scheduled_by_id: dict[str, dict] = {}
    day_intervals: dict[date, list[tuple[time, time, str]]] = {}
    for stop in stops:
        if not isinstance(stop, dict):
            raise HTTPException(409, detail="草案地点数据无效，请重新生成草案")
        candidate_id = stop.get("candidate_id")
        if not isinstance(candidate_id, str) or not candidate_id or candidate_id in seen_ids:
            raise HTTPException(409, detail="草案地点重复或标识无效，请重新生成草案")
        seen_ids.add(candidate_id)
        scheduled_by_id[candidate_id] = stop
        try:
            stop_day = date.fromisoformat(stop["day"])
            start_time = time.fromisoformat(stop["start_time"])
            end_time = time.fromisoformat(stop["end_time"])
        except (KeyError, TypeError, ValueError) as exc:
            raise HTTPException(409, detail="草案日期或时间格式无效，请重新生成草案") from exc
        if start_time >= end_time:
            raise HTTPException(409, detail="草案地点的结束时间必须晚于开始时间，请重新生成草案")
        if (trip_start_date and stop_day < trip_start_date) or (
            trip_end_date and stop_day > trip_end_date
        ):
            raise HTTPException(409, detail="草案地点日期超出旅行日期，请重新生成草案")
        if stop.get("planning_category") == "LODGING":
            raise HTTPException(409, detail="住宿地点不能作为带游览时段的行程节点保存，请重新生成草案")
        day_intervals.setdefault(stop_day, []).append((start_time, end_time, candidate_id))
    if not seen_ids.issubset(set(selected_ids)):
        raise HTTPException(409, detail="草案安排引用了未选中的地点，请重新生成草案")
    for intervals in day_intervals.values():
        intervals.sort()
        if any(current[0] < previous[1] for previous, current in pairwise(intervals)):
            raise HTTPException(409, detail="草案中存在时间重叠，请重新生成草案")

    locked_stops = [
        stop for stop in (current_plan or {}).get("stops", [])
        if isinstance(stop, dict) and str(stop.get("status", "")).upper() in {"FIXED", "RESERVED"}
    ]
    for locked in locked_stops:
        candidate_id = locked.get("candidate_id")
        proposed = scheduled_by_id.get(candidate_id) if isinstance(candidate_id, str) else None
        if (
            proposed is None
            or proposed.get("day") != locked.get("day")
            or proposed.get("start_time") != locked.get("start_time")
        ):
            raise HTTPException(
                409,
                detail=f"固定或已预约地点“{locked.get('name', '未命名地点')}”不能移除或更改日期/开始时间，请重新生成草案",
            )
def validate_trip_snapshot(
    *,
    snapshot_plan: dict | None,
    latest_plan_id: str | None,
    latest_plan_version: int | None,
    base_trip: dict | None,
    current_trip: dict,
) -> None:
    latest_snapshot = (latest_plan_id, latest_plan_version) if latest_plan_id else None
    expected_snapshot = (
        (snapshot_plan["id"], snapshot_plan["version"]) if snapshot_plan else None
    )
    if latest_snapshot != expected_snapshot:
        raise HTTPException(409, detail="原旅行计划已在其他位置更新，请重新打开顾问会话")
    if base_trip and any(base_trip.get(key) != current_trip.get(key) for key in (
        "destination", "start_date", "end_date", "budget", "pace_level",
    )):
        raise HTTPException(409, detail="旅行条件已在其他位置更新，请重新打开顾问会话")


def validate_profile_memory_snapshot(draft: dict | None, current_fingerprint: str) -> None:
    """Reject confirmation when account memory changed after this draft was reviewed."""
    if not draft or not draft.get("profile_memory_fingerprint"):
        raise HTTPException(409, detail="草案缺少账户偏好快照，请重新生成草案后确认")
    if draft["profile_memory_fingerprint"] != current_fingerprint:
        raise HTTPException(409, detail="账户旅行偏好已变化，请重新生成草案后确认")
