"""Existing-trip schedule proposal orchestration."""

import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import date, time
from itertools import pairwise
from math import sqrt
from typing import Any
from uuid import UUID

from travelpilot_core.agents.loop import AgentTaskContext
from travelpilot_core.agents.runtime import AgentRuntime
from travelpilot_core.engines.optimizer.itinerary import Candidate, Visit, build_itinerary
from travelpilot_core.ports.tools import ToolFailure
from travelpilot_infra.providers.maps.errors import MapsProviderError

from travelpilot_api.advisor.interpretation import _clock_minutes
from travelpilot_api.advisor.route_evidence import gather_route_evidence, reusable_route_evidence
from travelpilot_api.advisor.tools import distance_tool_runtime, map_evidence_source


@dataclass
class ExistingTripScheduleResult:
    suggestions: list[dict[str, str]] = field(default_factory=list)
    reserved_breaks: list[dict[str, Any]] = field(default_factory=list)
    error: str | None = None
    warning: str | None = None
    trace: dict[str, Any] | None = None
    warnings: list[str] = field(default_factory=list)
    selected_candidate_ids: list[str] = field(default_factory=list)
    candidate_count: int = 0
    runtime: AgentRuntime | None = None


def _blocked(message: str) -> ExistingTripScheduleResult:
    return ExistingTripScheduleResult(
        error=message,
        trace={
            "decision_source": "RULE",
            "decision_summary": message,
            "status": "NEEDS_USER_INPUT",
            "route_evidence": "NOT_REQUIRED",
            "transitions": [],
        },
    )


def _requested_day_start_times(user_goal: str, start_date: date, end_date: date) -> dict[date, int]:
    """Translate explicit day-level late-start requests into hard planner bounds."""
    result: dict[date, int] = {}
    day_words = {"一": 1, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7,
                 "1": 1, "2": 2, "3": 3, "4": 4, "5": 5, "6": 6, "7": 7}
    late_start_terms = ("上午留空", "上午不安排", "上午自由", "中午以后", "下午开始", "午后开始")
    for match in re.finditer(r"第\s*([一二三四五六七1-7])\s*天", user_goal):
        if not any(term in user_goal[match.end():match.end() + 24] for term in late_start_terms):
            continue
        day_number = day_words.get(match.group(1))
        if day_number is None:
            continue
        day = start_date.fromordinal(start_date.toordinal() + day_number - 1)
        if day <= end_date:
            result[day] = 12 * 60
    return result


def _requested_meal_breaks(
    user_goal: str, start_date: date, end_date: date,
) -> list[dict[str, Any]]:
    """Turn an explicit meal-duration request into editable draft-only breaks.

    The meal duration must be explicit. If the user does not give a clock time,
    the standard midday/evening slot is presented as a proposed default so the
    user can review it before saving the plan.
    """
    compact = re.sub(r"\s+", "", user_goal)
    if not any(term in compact for term in ("留出", "预留", "留", "空出")):
        return []
    if (
        re.search(r"(?:不要|不用|不需要|不必|取消).{0,10}(?:午餐|午饭|晚餐|晚饭)", compact)
        or re.search(r"(?:午餐|午饭|晚餐|晚饭).{0,10}(?:不要|不用|不需要|不必|无需).{0,8}(?:留|预留|安排)", compact)
    ):
        return []

    duration_min = None
    if "一个半小时" in compact or "一個半小时" in compact:
        duration_min = 90
    elif "半小时" in compact or "半個小时" in compact:
        duration_min = 30
    else:
        hour_match = re.search(r"(\d+(?:\.\d+)?|一|两|二|三|四|五|六|七|八|九|十)个?小时", compact)
        minute_match = re.search(r"(\d{1,3})分钟", compact)
        chinese_numbers = {"一": 1, "两": 2, "二": 2, "三": 3, "四": 4, "五": 5,
                           "六": 6, "七": 7, "八": 8, "九": 9, "十": 10}
        if hour_match:
            raw_hours = hour_match.group(1)
            hours = (
                chinese_numbers[raw_hours]
                if raw_hours in chinese_numbers
                else float(raw_hours) if raw_hours[0].isdigit() else 0
            )
            duration_min = round(hours * 60)
        elif minute_match:
            duration_min = int(minute_match.group(1))
    if duration_min is None or not 15 <= duration_min <= 240:
        return []

    normalized = []
    if any(term in compact for term in ("午餐", "午饭")):
        normalized.append(("LUNCH", "午餐", 12 * 60))
    if any(term in compact for term in ("晚餐", "晚饭")):
        normalized.append(("DINNER", "晚餐", 18 * 60))
    if not normalized:
        return []

    breaks = []
    for day_ordinal in range(start_date.toordinal(), end_date.toordinal() + 1):
        day = date.fromordinal(day_ordinal)
        for meal_type, label, start_minute in normalized:
            end_minute = start_minute + duration_min
            if end_minute >= 24 * 60:
                continue
            breaks.append({
                "day": day.isoformat(),
                "meal_type": meal_type,
                "label": label,
                "start_time": f"{start_minute // 60:02d}:{start_minute % 60:02d}",
                "end_time": f"{end_minute // 60:02d}:{end_minute % 60:02d}",
                "duration_min": duration_min,
                "time_is_proposed_default": True,
            })
    return breaks


def _schedule_conflicts(
    visits: list[Visit], candidate_ids: list[str], matrix: list[list[float]] | None,
) -> list[str]:
    """Return transfer-buffer conflicts after fixed visits have been anchored."""
    index_by_id = {candidate_id: index for index, candidate_id in enumerate(candidate_ids)}
    grouped: dict[date, list[Visit]] = {}
    for visit in visits:
        grouped.setdefault(visit.day, []).append(visit)
    conflicts = []
    for day, day_visits in grouped.items():
        day_visits.sort(key=lambda visit: visit.start_time)
        for previous, current in pairwise(day_visits):
            previous_id = str(previous.candidate.poi_id)
            current_id = str(current.candidate.poi_id)
            if matrix is not None and previous_id in index_by_id and current_id in index_by_id:
                travel_min = matrix[index_by_id[previous_id]][index_by_id[current_id]]
            else:
                lat_delta = previous.candidate.latitude - current.candidate.latitude
                lon_delta = (previous.candidate.longitude - current.candidate.longitude) * 0.86
                travel_min = max(5, round(sqrt(lat_delta**2 + lon_delta**2) * 111 * 2.5))
            end_min = previous.end_time.hour * 60 + previous.end_time.minute
            next_start_min = current.start_time.hour * 60 + current.start_time.minute
            if end_min + travel_min + 15 > next_start_min:
                conflicts.append(
                    f"{day} {previous.candidate.name} 至 {current.candidate.name} 时间不足 "
                    f"（游览后还需约 {travel_min} 分钟交通和 15 分钟换乘）"
                )
    return conflicts


async def propose_existing_trip_schedule(
    *,
    context: dict[str, Any],
    candidates: list[dict[str, Any]],
    user_goal: str,
    destination: str,
    start_date: date | None,
    end_date: date | None,
    pace_level: int,
    maps,
    api_key: str,
    model: str,
    base_url: str,
    token_budget: int,
    task_context: AgentTaskContext | None = None,
    checkpoint: Callable[[dict[str, Any]], Awaitable[None]] | None = None,
    route_evidence_loader: Callable[..., Awaitable[dict[str, Any] | None]] | None = None,
    route_evidence_saver: Callable[[dict[str, Any]], Awaitable[None]] | None = None,
    load_decision_checkpoint: Callable[..., Awaitable[dict[str, str] | None]] | None = None,
    save_decision_checkpoint: Callable[..., Awaitable[None]] | None = None,
) -> ExistingTripScheduleResult:
    """Choose route evidence and build a verified, non-persistent schedule proposal."""
    current_plan = context.get("current_plan") or {}
    existing_stops = current_plan.get("stops", [])
    plan_poi_ids = {str(stop.get("poi_id")) for stop in existing_stops if stop.get("poi_id")}
    selected_candidate_ids = {
        str(candidate_id)
        for candidate_id in context.get("selected_candidate_ids", [])
    }
    schedule_candidates = [
        candidate for candidate in candidates
        if (
            candidate.get("in_current_plan")
            and str(candidate.get("existing_poi_id")) in plan_poi_ids
            or str(candidate.get("id")) in selected_candidate_ids
        )
        and candidate.get("planning_category") != "LODGING"
    ]
    if not schedule_candidates:
        return _blocked("请先选择要安排的收藏地点，或在行程中添加地点。")
    if not start_date or not end_date:
        return _blocked("当前旅行缺少日期，暂时无法为已确认地点推荐分日安排。")
    if len(schedule_candidates) > 29:
        return _blocked("地点数量超过路线距离核验上限，请先减少本次排程地点。")

    planner_candidates = [Candidate(
        poi_id=UUID(str(candidate["id"])),
        name=candidate["name"],
        latitude=float(candidate["latitude"]),
        longitude=float(candidate["longitude"]),
        duration_min=max(15, int(candidate.get("duration_min", 60))),
        priority=3,
        interest_score=float(candidate.get("confidence", 0.5)),
        opening_hours=candidate.get("opening_hours"),
        note=candidate.get("suggestion"),
    ) for candidate in schedule_candidates]
    points = [(candidate.latitude, candidate.longitude) for candidate in planner_candidates]
    selected_plan_ids = [str(candidate["id"]) for candidate in schedule_candidates]
    expected_source = map_evidence_source(maps)
    context_revision = int(context.get("revision", 0))
    route_cache = reusable_route_evidence(
        context.get("agent_route_evidence"), selected_plan_ids, context_revision,
        expected_source=expected_source,
    ) if len(planner_candidates) > 1 else None
    if route_cache is None and len(planner_candidates) > 1 and route_evidence_loader:
        recovered_cache = await route_evidence_loader(
            candidate_ids=selected_plan_ids,
            context_revision=context_revision,
            expected_source=expected_source,
        )
        route_cache = reusable_route_evidence(
            recovered_cache, selected_plan_ids, context_revision,
            expected_source=expected_source,
        )
    fixed_stop_count = sum(
        str(stop.get("status", "")).upper() in {"FIXED", "RESERVED"}
        for stop in existing_stops
    )
    reserved_breaks = _requested_meal_breaks(user_goal, start_date, end_date)
    reserved_break_windows: dict[date, list[tuple[int, int]]] = {}
    for item in reserved_breaks:
        day = date.fromisoformat(item["day"])
        reserved_break_windows.setdefault(day, []).append((
            _clock_minutes(time.fromisoformat(item["start_time"])),
            _clock_minutes(time.fromisoformat(item["end_time"])),
        ))
    result = ExistingTripScheduleResult(candidate_count=len(schedule_candidates))

    try:
        result.runtime = distance_tool_runtime(maps)
        # The runtime can make more than one bounded decision while collecting
        # route evidence. Its internal reservation ledger must receive the
        # remaining *turn* allowance, not the generic runtime default.
        result.runtime.token_budget = max(0, token_budget)

        async def build_schedule_proposal(matrix):
            visits, warnings = build_itinerary(
                start_date, end_date, planner_candidates, matrix,
                buffer_min=90 if pace_level <= 2 else 60,
                transfer_buffer_min=15,
                max_pois_per_day={1: 3, 2: 4, 3: 5, 4: 6, 5: 6}[pace_level],
                day_start_minutes=_requested_day_start_times(user_goal, start_date, end_date),
                reserved_break_windows=reserved_break_windows,
            )
            feasible = len(visits) == len(planner_candidates)
            return {
                "feasible": feasible,
                "reason": None if feasible else (
                    "当前日期和节奏下无法安置全部地点；"
                    + "；".join(warnings[:3])
                    + "。你希望调整旅行日期或节奏、选择减少地点，还是暂不调整？"
                ),
                "candidate_ids": [str(candidate.poi_id) for candidate in planner_candidates],
                "schedules": [{
                    "candidate_id": str(visit.candidate.poi_id),
                    "day": visit.day.isoformat(),
                    "start_time": visit.start_time.strftime("%H:%M"),
                    "end_time": visit.end_time.strftime("%H:%M"),
                } for visit in visits],
                "warnings": warnings,
                "reserved_breaks": reserved_breaks,
            }

        async def persist_route_observation(observation: dict[str, Any]) -> None:
            if route_evidence_saver is None:
                return
            await route_evidence_saver({
                "candidate_ids": selected_plan_ids,
                "context_revision": context_revision,
                "durations_min": observation.get("durations_min"),
                "status": observation.get("status"),
                "source": observation.get("source"),
                "retrieved_at": observation.get("retrieved_at"),
            })

        route_result = await gather_route_evidence(
            result.runtime,
            points,
            trip_context={
                "destination": destination,
                "start_date": start_date.isoformat(),
                "end_date": end_date.isoformat(),
                "pace_level": pace_level,
                "has_dates": True,
                "fixed_stop_count": fixed_stop_count,
                "candidate_ids": selected_plan_ids,
            },
            user_goal=user_goal,
            api_key=api_key,
            model=model,
            base_url=base_url,
            token_budget=token_budget,
            cached_evidence=route_cache,
            task_context=task_context,
            schedule_builder=build_schedule_proposal,
            checkpoint=checkpoint,
            route_observation_callback=persist_route_observation,
            load_decision_checkpoint=load_decision_checkpoint,
            save_decision_checkpoint=save_decision_checkpoint,
        )
        matrix = route_result.matrix
        proposal = route_result.draft
        result.trace = {
            "decision_source": route_result.decision_source,
            "decision_summary": route_result.decision_summary,
            "transitions": route_result.loop.transitions,
            "status": route_result.loop.status,
            "route_evidence": route_result.evidence_status,
            "route_source": route_result.evidence_source,
            "execution_metrics": result.runtime.execution_metrics,
            **({"error_code": getattr(route_result.loop, "error", None)}
               if getattr(route_result.loop, "error", None) else {}),
        }
        result.warning = (
            "地点路线尚未由地图核验，时间建议使用本地距离估算；请重点检查地点顺序与交通留白。"
            if len(planner_candidates) > 1 and matrix is None else None
        )
        if not proposal:
            result.error = route_result.question or "Agent 未能生成经过验证的排程草案，请稍后重试。"
            return result
        if not proposal.get("feasible"):
            result.error = str(proposal.get("reason") or "当前条件下无法完整安排所有地点。")
            result.trace["status"] = "NEEDS_USER_INPUT"
            return result

        result.reserved_breaks = list(proposal.get("reserved_breaks", []))

        candidate_by_id = {str(candidate.poi_id): candidate for candidate in planner_candidates}
        proposed_visits = [Visit(
            candidate_by_id[item["candidate_id"]],
            date.fromisoformat(item["day"]),
            time.fromisoformat(item["start_time"]),
            time.fromisoformat(item["end_time"]),
        ) for item in proposal["schedules"]]
        result.warnings = list(proposal.get("warnings", []))
        fixed_schedules = {
            str(candidate["id"]): candidate["fixed_schedule"]
            for candidate in schedule_candidates if candidate.get("fixed_schedule")
        }
        anchored_visits = []
        for visit in proposed_visits:
            fixed = fixed_schedules.get(str(visit.candidate.poi_id))
            if fixed:
                fixed_day = date.fromisoformat(fixed["day"])
                fixed_start = time.fromisoformat(fixed["start_time"])
                if not start_date <= fixed_day <= end_date:
                    result.error = f"{visit.candidate.name} 的固定预约不在当前旅行日期内，请先修正旅行日期或预约。"
                    return result
                fixed_end_minutes = _clock_minutes(fixed_start) + visit.candidate.duration_min
                if fixed_end_minutes >= 24 * 60:
                    result.error = f"{visit.candidate.name} 的固定预约与预计游览时长跨越当天，无法安全生成自动排程。"
                    return result
                visit = Visit(
                    visit.candidate, fixed_day, fixed_start,
                    time(fixed_end_minutes // 60, fixed_end_minutes % 60),
                )
            anchored_visits.append(visit)
        anchored_visits.sort(key=lambda visit: (visit.day, visit.start_time))
        conflicts = _schedule_conflicts(anchored_visits, selected_plan_ids, matrix)
        if conflicts:
            result.error = (
                "固定预约与其他地点的游览或交通时间冲突，未生成排程草案："
                + "；".join(conflicts[:4])
                + "。你希望保留预约并先选择可调整的地点、修改预约时间后重试，还是暂不调整？"
            )
            result.trace["status"] = "NEEDS_USER_INPUT"
            return result
        result.suggestions = [{
            "candidate_id": str(visit.candidate.poi_id),
            "day": visit.day.isoformat(),
            "start_time": visit.start_time.strftime("%H:%M"),
        } for visit in anchored_visits]
        result.selected_candidate_ids = selected_plan_ids
        if set(context.get("selected_candidate_ids", [])) != set(selected_plan_ids):
            context["selected_candidate_ids"] = selected_plan_ids
            context["revision"] = int(context.get("revision", 0)) + 1
        if matrix is not None and route_result.evidence_retrieved_at:
            context["agent_route_evidence"] = {
                "candidate_ids": selected_plan_ids,
                "context_revision": int(context.get("revision", 0)),
                "durations_min": matrix,
                "status": route_result.evidence_status,
                "source": route_result.evidence_source,
                "retrieved_at": route_result.evidence_retrieved_at,
            }
        if result.warning:
            result.warnings.append(result.warning)
        return result
    except ToolFailure as exc:
        result.error = "暂时无法取得可靠的地点间交通时间，未生成自动排程建议。"
        result.trace = {
            "decision_source": "RUNTIME_GUARD",
            "decision_summary": "排程工具未能取得可用结果；正式行程未修改",
            "transitions": [],
            "status": "FAILED",
            "error_code": exc.code[:64],
            "route_evidence": "UNAVAILABLE",
        }
        return result
    except MapsProviderError:
        result.error = "地图路线服务暂时不可用，没有生成已核验的时间建议；请稍后重试。"
        result.trace = {
            "decision_source": "RUNTIME_GUARD",
            "decision_summary": "地图路线服务不可用；正式行程未修改",
            "transitions": [],
            "status": "FAILED",
            "error_code": "MAPS_PROVIDER_UNAVAILABLE",
            "route_evidence": "UNAVAILABLE",
        }
        return result
    except ValueError:
        result.error = "排程输入或路线数据无效，未生成时间建议；请检查地点和旅行日期后重试。"
        result.trace = {
            "decision_source": "RUNTIME_GUARD",
            "decision_summary": "排程输入或路线数据未通过校验；正式行程未修改",
            "transitions": [],
            "status": "FAILED",
            "error_code": "SCHEDULE_INPUT_INVALID",
            "route_evidence": "REJECTED",
        }
        return result
