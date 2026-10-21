"""Deterministic route validation for the active itinerary snapshot."""

from dataclasses import dataclass
from datetime import UTC, datetime
from itertools import pairwise
from math import isfinite
from typing import Any

from travelpilot_infra.providers.maps.errors import MapsProviderError

from travelpilot_api.advisor.route_evidence import gather_route_evidence, reusable_route_evidence
from travelpilot_api.advisor.task_contract import build_agent_task_context
from travelpilot_api.advisor.tools import distance_tool_runtime, map_evidence_source


@dataclass(frozen=True)
class ItineraryRouteValidation:
    stops: list[dict[str, Any]]
    candidate_ids: list[str]
    matrix: list[list[float]] | None = None
    status: str = "UNAVAILABLE"
    source: str | None = None
    retrieved_at: str | None = None
    mode: str = "DRIVING"
    trace: dict[str, Any] | None = None
    error: str | None = None


def active_itinerary_stops(row, context: dict) -> list[dict[str, Any]]:
    """Use the current draft when fresh; otherwise use the saved itinerary."""
    draft = getattr(row, "draft", None)
    if (
        isinstance(draft, dict)
        and draft.get("context_revision") == context.get("revision")
        and isinstance(draft.get("stops"), list)
    ):
        stops = draft["stops"]
    else:
        stops = (context.get("current_plan") or {}).get("stops", [])
    return sorted(
        [stop for stop in stops if isinstance(stop, dict) and stop.get("candidate_id")],
        key=lambda stop: (str(stop.get("day", "")), str(stop.get("start_time", ""))),
    )


async def validate_active_itinerary_routes(
    *,
    row,
    context: dict,
    maps,
    user_goal: str,
    checkpoint=None,
    route_observation_callback=None,
) -> ItineraryRouteValidation:
    """Check every active stop's map coordinates and obtain a verified matrix.

    This is a read-only operation. It does not alter selected candidates, a
    draft, or the formal itinerary. Model calls are intentionally omitted: the
    button already specifies the action and the server owns stop order/coords.
    """
    stops = active_itinerary_stops(row, context)
    candidate_ids = [str(stop["candidate_id"]) for stop in stops]
    if len(stops) < 2:
        return ItineraryRouteValidation(
            stops=stops,
            candidate_ids=candidate_ids,
            error="当前有效行程至少需要两个已安排地点，才能校验地点间路线。",
        )
    if len(stops) > 30:
        return ItineraryRouteValidation(
            stops=stops,
            candidate_ids=candidate_ids,
            error=f"当前行程有 {len(stops)} 个地点，单次路线核验上限为 30 个；请按日期分段核验。",
        )
    if len(set(candidate_ids)) != len(candidate_ids):
        return ItineraryRouteValidation(
            stops=stops,
            candidate_ids=candidate_ids,
            error="当前行程包含重复的地点标识，无法安全匹配路线结果；请刷新顾问后重试。",
        )

    candidates_by_id = {
        str(candidate.get("id")): candidate
        for candidate in row.candidates or []
        if isinstance(candidate, dict) and candidate.get("id")
    }
    missing: list[str] = []
    points: list[tuple[float, float]] = []
    for stop in stops:
        candidate_id = str(stop["candidate_id"])
        candidate = candidates_by_id.get(candidate_id)
        try:
            lat, lon = float(candidate["latitude"]), float(candidate["longitude"])
            if not (-90 <= lat <= 90 and -180 <= lon <= 180):
                raise ValueError
        except (KeyError, TypeError, ValueError):
            missing.append(str(stop.get("name") or candidate_id))
            continue
        points.append((lat, lon))
    if missing:
        return ItineraryRouteValidation(
            stops=stops,
            candidate_ids=candidate_ids,
            error="以下地点缺少有效地图坐标，暂不能核验整段路线："
            + "、".join(dict.fromkeys(missing[:8]))
            + "。请重新搜索并加入地图已核验的地点后重试。",
        )

    source = map_evidence_source(maps)
    normalized_goal = user_goal.casefold()
    mode = (
        "WALKING"
        if any(term in normalized_goal for term in ("步行", "走路", "徒步"))
        else "DRIVING"
    )
    if any(term in normalized_goal for term in ("公交", "地铁", "公共交通")):
        return ItineraryRouteValidation(
            stops=stops,
            candidate_ids=candidate_ids,
            mode="TRANSIT",
            error="当前整段行程核验支持驾车或步行；公交和地铁逐段核验尚未接入，我没有替你改成其他出行方式。",
        )
    revision = int(context.get("revision", 0))
    cached = reusable_route_evidence(
        context.get("agent_route_evidence"),
        candidate_ids,
        revision,
        expected_source=source,
        expected_mode=mode,
    )
    if cached is not None:
        matrix = cached["durations_min"]
        status = cached["status"]
        evidence_source = cached["source"]
        retrieved_at = cached["retrieved_at"]
        trace = {
            "decision_source": "OBSERVATION",
            "decision_summary": f"复用同一行程版本下仍有效的{_mode_name(mode)}路线证据",
            "status": "SUCCEEDED",
            "transitions": [],
            "execution_metrics": {"tool_calls": 0},
        }
    elif mode == "WALKING":
        matrix = [[0 for _ in points] for _ in points]
        status = "SIMULATED" if source == "Local simulated maps" else "VERIFIED"
        evidence_source = source
        retrieved_at = datetime.now(UTC).isoformat()
        trace = {
            "decision_source": "RULE",
            "decision_summary": "按行程顺序逐段核验步行路线",
            "status": "SUCCEEDED",
            "transitions": [],
            "execution_metrics": {"route_legs": 0},
        }
        try:
            for index, (previous, current) in enumerate(pairwise(stops)):
                if str(previous.get("day", "")) != str(current.get("day", "")):
                    continue
                route = await maps.get_route(points[index], points[index + 1], "WALKING")
                if (
                    route.origin != points[index]
                    or route.destination != points[index + 1]
                    or route.mode != "WALKING"
                    or not isinstance(route.duration_min, int)
                    or isinstance(route.duration_min, bool)
                    or route.duration_min < 0
                    or not isinstance(route.distance_m, int)
                    or isinstance(route.distance_m, bool)
                    or route.distance_m < 0
                    or not route.provider
                    or not all(
                        len(point) == 2
                        and isfinite(point[0])
                        and -90 <= point[0] <= 90
                        and isfinite(point[1])
                        and -180 <= point[1] <= 180
                        for point in route.polyline
                    )
                ):
                    raise ValueError("INVALID_WALKING_ROUTE")
                matrix[index][index + 1] = route.duration_min
                status = "SIMULATED" if route.provider == "fake" else "VERIFIED"
                trace["execution_metrics"]["route_legs"] += 1
        except (
            MapsProviderError,
            OSError,
            ValueError,
            TypeError,
            KeyError,
            AttributeError,
            RuntimeError,
            TimeoutError,
        ):
            return ItineraryRouteValidation(
                stops=stops,
                candidate_ids=candidate_ids,
                status="UNAVAILABLE",
                source=evidence_source,
                mode=mode,
                trace={**trace, "status": "FAILED"},
                error="步行路线服务暂时不可用或结果未通过校验；正式行程和已选候选均未修改，请稍后重试。",
            )
    else:
        runtime = distance_tool_runtime(maps)

        async def persist_route_observation(observation: dict[str, Any]) -> None:
            if route_observation_callback is None:
                return
            await route_observation_callback({
                **observation,
                "candidate_ids": candidate_ids,
                "context_revision": revision,
            })

        result = await gather_route_evidence(
            runtime,
            points,
            trip_context={
                "destination": row.destination or "",
                "start_date": row.start_date.isoformat() if row.start_date else "",
                "end_date": row.end_date.isoformat() if row.end_date else "",
                "has_dates": bool(row.start_date and row.end_date),
                "candidate_ids": candidate_ids,
            },
            user_goal=user_goal,
            api_key="",
            model="",
            base_url="",
            token_budget=0,
            cached_evidence=cached,
            task_context=build_agent_task_context(
                task_type="TRANSPORT",
                goal=user_goal,
                known_facts=["selected_trip_stops", "verified_stop_coordinates"],
                missing_facts=[],
                available_tools=("get_distance_matrix",),
            ),
            checkpoint=checkpoint,
            route_observation_callback=(
                persist_route_observation if route_observation_callback is not None else None
            ),
        )
        matrix = result.matrix
        status = result.evidence_status
        evidence_source = result.evidence_source
        retrieved_at = result.evidence_retrieved_at
        trace = {
            "decision_source": result.decision_source,
            "decision_summary": result.decision_summary,
            "status": result.loop.status,
            "transitions": result.loop.transitions,
            "execution_metrics": runtime.execution_metrics,
        }
    if matrix is None or status not in {"VERIFIED", "SIMULATED"}:
        return ItineraryRouteValidation(
            stops=stops,
            candidate_ids=candidate_ids,
            status=status,
            source=evidence_source,
            mode=mode,
            trace=trace,
            error=(
                "地图路线服务暂时不可用或结果未通过校验；正式行程和已选候选均未修改，请稍后重试。"
            ),
        )

    context["agent_route_evidence"] = {
        "candidate_ids": candidate_ids,
        "context_revision": revision,
        "durations_min": matrix,
        "status": status,
        "source": evidence_source,
        "retrieved_at": retrieved_at,
        "mode": mode,
    }
    return ItineraryRouteValidation(
        stops=stops,
        candidate_ids=candidate_ids,
        matrix=matrix,
        status=status,
        source=evidence_source,
        retrieved_at=retrieved_at,
        mode=mode,
        trace=trace,
    )


def itinerary_route_summary(validation: ItineraryRouteValidation) -> str:
    """Format only consecutive same-day legs from the verified matrix."""
    if validation.matrix is None:
        return validation.error or "路线暂未核验。"
    legs = []
    for index, (previous, current) in enumerate(pairwise(validation.stops)):
        if str(previous.get("day", "")) != str(current.get("day", "")):
            continue
        minutes = round(float(validation.matrix[index][index + 1]))
        legs.append(
            f"{previous.get('name', '前一地点')} → {current.get('name', '后一地点')}约 {minutes} 分钟"
        )
    if not legs:
        return "已核验各日地点间的地图交通矩阵；每日只有一个地点，无需查询日内衔接路线。"
    mode_label = _mode_name(validation.mode)
    qualifier = "本地模拟地图" if validation.status == "SIMULATED" else "地图估算"
    action = "已完成本地模拟路线检查" if validation.status == "SIMULATED" else "已核验当前行程"
    return (
        f"{action}的每日相邻{mode_label}路线（{qualifier}）："
        + "；".join(legs)
        + f"。来源：{validation.source or '地图服务'}。这是交通时间估算，不代表实时路况。"
    )


def _mode_name(mode: str) -> str:
    return "步行" if mode == "WALKING" else "驾车"
