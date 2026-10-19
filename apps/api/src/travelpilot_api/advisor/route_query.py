"""Bounded route lookup between owner-scoped, already-known Advisor places."""

import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from math import isfinite
from typing import Any

from pydantic import BaseModel, Field
from travelpilot_core.agents.loop import (
    AgentAction,
    AgentTaskContext,
    LoopResult,
    run_constrained_loop,
)
from travelpilot_core.agents.planner import PlannerDecisionFailure, decide_next_action
from travelpilot_core.agents.runtime import AgentRuntime
from travelpilot_core.ports.tools import ToolFailure, ToolSpec
from travelpilot_infra.providers.maps.errors import MapsProviderError

from travelpilot_api.advisor.orchestration_state import readonly_action_ref, stage_observation_ref
from travelpilot_api.advisor.task_contract import build_agent_task_context
from travelpilot_api.advisor.tools import map_evidence_source


class RouteQueryInput(BaseModel):
    origin_place_id: str = Field(min_length=1, max_length=64)
    destination_place_id: str = Field(min_length=1, max_length=64)
    mode: str = Field(pattern="^(DRIVING|WALKING|TRANSIT)$")


def _requested_mode(goal: str) -> str:
    normalized = goal.casefold()
    if any(term in normalized for term in ("公交", "地铁", "公共交通", "坐地铁", "乘地铁")):
        return "TRANSIT"
    return "WALKING" if any(term in normalized for term in ("步行", "走路", "徒步")) else "DRIVING"


@dataclass(frozen=True)
class RouteQueryResult:
    route: dict[str, Any] | None
    loop: LoopResult
    runtime: AgentRuntime
    decision_source: str
    decision_summary: str

    @property
    def trace(self) -> dict[str, Any]:
        return {
            "decision_source": self.decision_source,
            "decision_summary": self.decision_summary,
            "status": self.loop.status,
            "transitions": self.loop.transitions,
            "execution_metrics": self.runtime.execution_metrics,
        }


def _coordinate(place: dict[str, Any]) -> tuple[float, float] | None:
    try:
        latitude, longitude = float(place["latitude"]), float(place["longitude"])
    except (KeyError, TypeError, ValueError):
        return None
    if not (isfinite(latitude) and isfinite(longitude)
            and -90 <= latitude <= 90 and -180 <= longitude <= 180):
        return None
    return latitude, longitude


def _valid_path(points: Any) -> bool:
    return isinstance(points, list) and len(points) <= 500 and all(
        isinstance(point, list) and len(point) == 2
        and isinstance(point[0], (int, float)) and not isinstance(point[0], bool)
        and isinstance(point[1], (int, float)) and not isinstance(point[1], bool)
        and isfinite(point[0]) and isfinite(point[1])
        and -90 <= point[0] <= 90 and -180 <= point[1] <= 180
        for point in points
    )


def _resolve_explicit_places(goal: str, places: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Resolve the explicit route pair, even when other trip places are mentioned.

    A compound request often names every place to schedule and then names only
    two endpoints for a route (for example, "安排 A、B、C，并查 A 到 B 的路线").
    Treating every mentioned place as a route endpoint makes that clear request
    look ambiguous. Prefer an explicit connector pair, then retain the previous
    exact-two-name behavior for simple requests.
    """
    folded = goal.casefold()
    named_places = [
        (str(place.get("name", "")).strip(), place)
        for place in places
        if str(place.get("name", "")).strip() and _coordinate(place) is not None
    ]
    named_places.sort(key=lambda item: len(item[0]), reverse=True)
    if named_places:
        alternatives = "|".join(re.escape(name.casefold()) for name, _ in named_places)
        pair = re.compile(
            rf"(?P<origin>{alternatives})\s*(?:到|至|前往|去)\s*"
            rf"(?P<destination>{alternatives})"
        )
        route_cues = ("路线", "怎么走", "交通", "路程", "驾车", "开车", "步行", "地铁", "公交")
        by_name = {name.casefold(): place for name, place in named_places}
        for match in pair.finditer(folded):
            clause_start = max(
                (folded.rfind(mark, 0, match.start()) + 1 for mark in "，。；！？,;!?\n"),
                default=0,
            )
            clause_end_candidates = [folded.find(mark, match.end()) for mark in "，。；！？,;!?\n"]
            clause_end = min((position for position in clause_end_candidates if position >= 0), default=len(folded))
            clause = folded[clause_start:clause_end]
            if not any(cue in clause for cue in route_cues):
                continue
            origin = by_name.get(match.group("origin"))
            destination = by_name.get(match.group("destination"))
            if origin is not None and destination is not None and origin.get("id") != destination.get("id"):
                return [origin, destination]

    mentions = []
    for place in places:
        name = str(place.get("name", "")).strip()
        if name and name.casefold() in folded and _coordinate(place) is not None:
            mentions.append((folded.index(name.casefold()), place))
    mentions.sort(key=lambda item: item[0])
    unique = []
    seen = set()
    for _, place in mentions:
        place_id = str(place.get("id", ""))
        if place_id and place_id not in seen:
            seen.add(place_id)
            unique.append(place)
    return unique


def _verified_route(value: Any, approved_places: dict[str, dict[str, Any]]) -> bool:
    if not isinstance(value, dict) or value.get("status") not in {"VERIFIED", "SIMULATED"}:
        return False
    origin_id, destination_id = value.get("origin_place_id"), value.get("destination_place_id")
    if not isinstance(origin_id, str) or not isinstance(destination_id, str):
        return False
    origin = approved_places.get(origin_id)
    destination = approved_places.get(destination_id)
    distance, duration = value.get("distance_m"), value.get("duration_min")
    points = value.get("points")
    polyline = value.get("polyline")
    source = value.get("source")
    retrieved_at = value.get("retrieved_at")
    if not isinstance(retrieved_at, str):
        return False
    try:
        retrieved = datetime.fromisoformat(retrieved_at)
    except ValueError:
        return False
    if retrieved.tzinfo is None or retrieved.utcoffset() is None:
        return False
    status = value["status"]
    if (status == "SIMULATED" and source != "Local simulated maps") or (
        status == "VERIFIED" and source != "AMap Web Service"
    ):
        return False
    origin_point, destination_point = _coordinate(origin), _coordinate(destination)
    return bool(
        origin and destination and origin_id != destination_id
        and value.get("mode") in {"DRIVING", "WALKING", "TRANSIT"}
        and isinstance(distance, int) and not isinstance(distance, bool) and distance > 0
        and isinstance(duration, int) and not isinstance(duration, bool) and 0 < duration <= 2880
        and isinstance(points, list) and len(points) == 2 and _valid_path(points)
        and origin_point is not None and destination_point is not None
        and points == [list(origin_point), list(destination_point)]
        and isinstance(polyline, list) and len(polyline) >= 2 and _valid_path(polyline)
    )


async def run_advisor_route_query(
    *, maps, places: list[dict[str, Any]], user_goal: str,
    api_key: str = "", model: str = "", base_url: str = "", token_budget: int = 256,
    task_context: AgentTaskContext | None = None,
    checkpoint: Callable[[dict[str, Any]], Awaitable[None]] | None = None,
    cached_route: dict[str, Any] | None = None,
    route_observation_callback: Callable[[dict[str, Any]], Awaitable[None]] | None = None,
    load_decision_checkpoint: Callable[..., Awaitable[dict[str, str] | None]] | None = None,
    save_decision_checkpoint: Callable[..., Awaitable[None]] | None = None,
) -> RouteQueryResult:
    approved_places = {
        str(place.get("id")): place for place in places[:40]
        if isinstance(place, dict) and place.get("id") and place.get("name")
        and _coordinate(place) is not None
    }
    tool = ToolSpec(
        "get_trip_route", RouteQueryInput,
        _route_handler(maps, approved_places),
        timeout_seconds=8, retry_limit=0,
    )
    runtime = AgentRuntime(
        [tool], _trace, max_steps=1, max_attempts=1,
        token_budget=max(0, token_budget),
    )
    decision_source = "RULE"
    requested_mode = _requested_mode(user_goal)
    if (
        isinstance(cached_route, dict)
        and cached_route.get("mode") == requested_mode
        and cached_route.get("source") == map_evidence_source(maps)
        and _verified_route(cached_route, approved_places)
    ):
        cached_route = {
            **cached_route,
            "origin_name": str(approved_places[cached_route["origin_place_id"]]["name"])[:100],
            "destination_name": str(approved_places[cached_route["destination_place_id"]]["name"])[:100],
        }
    else:
        cached_route = None
    mode_label = {"WALKING": "步行", "TRANSIT": "公交/地铁", "DRIVING": "驾车"}[requested_mode]
    decision_summary = f"只在旅行中已核验的地点之间查询{mode_label}路线"
    catalog = [{"id": place_id, "name": str(place["name"])[:100]}
               for place_id, place in approved_places.items()]

    decision_observation = {
        "task": "route_between_known_trip_places",
        "user_goal": user_goal[:800],
        "known_places": catalog,
        "supported_mode": requested_mode,
    }
    action_by_ref: dict[str, AgentAction] = {}
    place_ids = list(approved_places)
    for origin_id in place_ids:
        for destination_id in place_ids:
            if origin_id == destination_id:
                continue
            endpoints = [approved_places[origin_id], approved_places[destination_id]]
            if requested_mode == "TRANSIT" and not _transit_city_codes_available(maps, endpoints):
                continue
            action = AgentAction(
                kind="CALL_TOOL", tool="get_trip_route",
                arguments={"origin_place_id": origin_id,
                           "destination_place_id": destination_id,
                           "mode": requested_mode},
            )
            action_by_ref[readonly_action_ref(action.tool, action.arguments)] = action

    async def recover_decision(observation, allowed_tools, decision_number):
        if (
            load_decision_checkpoint is None or decision_number != 1
            or "get_trip_route" not in allowed_tools
        ):
            return None
        recovered = await load_decision_checkpoint(
            observation_ref=stage_observation_ref({**decision_observation,
                                                   "loop_observation": observation}),
            decision_number=decision_number,
            approved_actions={key: value.tool for key, value in action_by_ref.items()},
        )
        if recovered is None:
            return None
        action = action_by_ref.get(recovered.get("action_ref", ""))
        if action is None or action.tool != recovered.get("tool"):
            return None
        nonlocal decision_source, decision_summary
        decision_source = "RECOVERED"
        decision_summary = "恢复中断前已批准的路线查询"
        return action.model_copy(update={"summary": decision_summary})

    async def persist_decision(observation, action, decision_number):
        if save_decision_checkpoint is None or decision_number != 1:
            return
        action_ref = readonly_action_ref(action.tool or "", action.arguments)
        approved = action_by_ref.get(action_ref)
        if approved is None or approved.tool != action.tool:
            return
        await save_decision_checkpoint(
            observation_ref=stage_observation_ref({**decision_observation,
                                                   "loop_observation": observation}),
            decision_number=decision_number,
            approved_actions={key: value.tool for key, value in action_by_ref.items()},
            tool=approved.tool,
            action_ref=action_ref,
        )

    async def policy(observation: dict[str, Any], allowed: list[str]) -> AgentAction:
        nonlocal decision_source, decision_summary
        if "get_trip_route" in observation:
            return AgentAction(kind="FINISH", summary=f"已取得{mode_label}路线")
        if "get_trip_route_failure" in observation:
            decision_summary = f"{mode_label}路线服务暂不可用；不以直线距离替代路线"
            return AgentAction(kind="FINISH", summary=decision_summary)
        if "get_trip_route_verification_failure" in observation:
            decision_summary = "地图返回的路线数据未通过验证；停止重复查询"
            return AgentAction(kind="FINISH", summary=decision_summary)

        explicit = _resolve_explicit_places(user_goal, list(approved_places.values()))
        if len(explicit) == 2:
            if requested_mode == "TRANSIT" and not _transit_city_codes_available(maps, explicit):
                return AgentAction(
                    kind="ASK_USER",
                    question="公交/地铁路线需要高德地点城市编码；当前保存的地点没有编码。请重新通过地点搜索添加这两个地点，或明确告诉我改查驾车路线。",
                )
            decision_source = "RULE"
            return AgentAction(kind="CALL_TOOL", tool="get_trip_route", arguments={
                "origin_place_id": str(explicit[0]["id"]),
                "destination_place_id": str(explicit[1]["id"]),
                "mode": requested_mode,
            })
        if not api_key.strip() or not model.strip():
            return AgentAction(
                kind="ASK_USER",
                question=f"请从这段旅行已保存的地点中说出出发点和目的地；目前可核验{mode_label}路线。",
            )

        reservation = runtime.reserve_output_tokens(min(128, max(1, token_budget)))
        try:
            action, usage = await decide_next_action(
                {"task": "route_between_known_trip_places", "user_goal": user_goal[:800],
                 "known_places": catalog, "supported_mode": requested_mode},
                {"get_trip_route": {"input_schema": RouteQueryInput.model_json_schema()}},
                api_key=api_key, model=model, base_url=base_url, token_budget=reservation,
            )
        except PlannerDecisionFailure as exc:
            runtime.record_token_usage(
                input_tokens=exc.usage.prompt_tokens, output_tokens=exc.usage.completion_tokens,
                total_tokens=exc.usage.total_tokens, reserved=reservation,
            )
            decision_source = "RULE_FALLBACK"
            decision_summary = "模型动作未通过校验，先确认两个已保存地点"
            if len(explicit) == 2:
                return AgentAction(kind="CALL_TOOL", tool="get_trip_route", arguments={
                    "origin_place_id": str(explicit[0]["id"]),
                    "destination_place_id": str(explicit[1]["id"]),
                    "mode": requested_mode,
                })
            return AgentAction(kind="ASK_USER", question="请明确选择这段旅行中两个已保存地点作为起点和终点。")
        except ToolFailure:
            runtime.release_output_tokens(reservation)
            decision_source = "RULE_FALLBACK"
            return AgentAction(kind="ASK_USER", question="请明确选择这段旅行中两个已保存地点作为起点和终点。")
        runtime.record_token_usage(
            input_tokens=usage.prompt_tokens, output_tokens=usage.completion_tokens,
            total_tokens=usage.total_tokens, reserved=reservation,
        )
        decision_source = "MODEL"
        decision_summary = action.summary[:180] or "根据当前旅行地点判断路线查询或补充问题"
        if action.kind == "ASK_USER":
            return AgentAction(kind="ASK_USER", question=action.question or action.summary)
        if action.kind == "CALL_TOOL" and action.tool == "get_trip_route":
            arguments = action.arguments if isinstance(action.arguments, dict) else {}
            origin_id = str(arguments.get("origin_place_id", ""))
            destination_id = str(arguments.get("destination_place_id", ""))
            selected = [approved_places.get(origin_id), approved_places.get(destination_id)]
            if origin_id in approved_places and destination_id in approved_places and origin_id != destination_id and arguments.get("mode") == requested_mode:
                if requested_mode == "TRANSIT" and not _transit_city_codes_available(maps, selected):
                    return AgentAction(kind="ASK_USER", question="公交/地铁路线需要地点包含高德城市编码；请通过地点搜索重新添加起点和终点。")
                return AgentAction(kind="CALL_TOOL", tool="get_trip_route", arguments={
                    "origin_place_id": origin_id, "destination_place_id": destination_id,
                    "mode": requested_mode,
                }, summary=decision_summary)
        if action.kind == "FINISH":
            return AgentAction(kind="ASK_USER", question="请说出你想查询的两个已保存地点。")
        decision_source = "RULE_RECOVERY"
        return AgentAction(kind="ASK_USER", question="请从当前旅行已保存地点中选择起点和终点。")

    async def verifier(name: str, value: dict[str, Any]) -> bool:
        valid = (
            name == "get_trip_route"
            and value.get("mode") == requested_mode
            and _verified_route(value, approved_places)
        )
        if valid and route_observation_callback is not None:
            await route_observation_callback(value)
        return valid

    async def on_checkpoint(state: dict[str, Any]) -> None:
        if checkpoint is not None:
            await checkpoint(state)

    loop = await run_constrained_loop(
        runtime=runtime, policy=policy, verifier=verifier, checkpoint=on_checkpoint,
        task_context=task_context or build_agent_task_context(
            task_type="TRANSPORT",
            goal=user_goal,
            known_facts=["saved_places"] if approved_places else [],
            missing_facts=["origin_or_destination"] if len(approved_places) < 2 else [],
            available_tools=["get_trip_route"],
        ),
        initial_observation={
            "user_goal": user_goal[:800], "known_place_count": len(approved_places),
            **({"get_trip_route": cached_route} if cached_route is not None else {}),
        },
        max_decisions=2, allowed_tool_names={"get_trip_route"},
        continue_on_tool_failure=True, continue_on_verifier_failure=True,
        recover_decision=recover_decision,
        persist_decision=persist_decision if save_decision_checkpoint is not None else None,
    )
    observed_route = loop.observation.get("get_trip_route")
    return RouteQueryResult(
        observed_route if isinstance(observed_route, dict) else None,
        loop, runtime, decision_source, decision_summary,
    )


def _route_handler(maps, approved_places: dict[str, dict[str, Any]]):
    async def get_route(arguments: RouteQueryInput) -> dict[str, Any]:
        origin = approved_places.get(arguments.origin_place_id)
        destination = approved_places.get(arguments.destination_place_id)
        if origin is None or destination is None or origin is destination:
            raise ToolFailure("PLACE_SCOPE_INVALID")
        origin_point, destination_point = _coordinate(origin), _coordinate(destination)
        if origin_point is None or destination_point is None:
            raise ToolFailure("PLACE_COORDINATES_INVALID")
        try:
            if arguments.mode == "TRANSIT":
                route = await maps.get_route(
                    origin_point, destination_point, arguments.mode,
                    origin_city_code=_city_code(origin),
                    destination_city_code=_city_code(destination),
                )
            else:
                route = await maps.get_route(origin_point, destination_point, arguments.mode)
        except MapsProviderError as exc:
            raise ToolFailure("MAPS_PROVIDER_UNAVAILABLE") from exc
        source = "Local simulated maps" if route.provider == "fake" else "AMap Web Service" if route.provider == "amap" else route.provider
        result = {
            "status": "SIMULATED" if route.provider == "fake" else "VERIFIED",
            "origin_place_id": arguments.origin_place_id,
            "origin_name": str(origin["name"])[:100],
            "destination_place_id": arguments.destination_place_id,
            "destination_name": str(destination["name"])[:100],
            "points": [list(origin_point), list(destination_point)],
            "distance_m": int(route.distance_m), "duration_min": int(route.duration_min),
            "mode": route.mode, "source": source,
            "retrieved_at": datetime.now(UTC).isoformat(),
            "polyline": [[float(lat), float(lon)] for lat, lon in route.polyline[:500]],
            "segments": [
                {"mode": str(segment.get("mode", ""))[:24], "label": str(segment.get("label", ""))[:240]}
                for segment in route.segments[:30] if isinstance(segment, dict)
            ],
        }
        return result

    return get_route


def _city_code(place: dict[str, Any]) -> str | None:
    value = place.get("city_code")
    return value.strip()[:16] if isinstance(value, str) and value.strip() else None


def _transit_city_codes_available(maps, places: list[dict[str, Any] | None]) -> bool:
    provider = getattr(maps, "provider", maps)
    if type(provider).__name__.casefold().startswith("fake"):
        return True
    return len(places) == 2 and all(isinstance(place, dict) and _city_code(place) for place in places)


async def _trace(_event: dict[str, Any]) -> None:
    return None
