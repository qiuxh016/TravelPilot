"""Bounded, observation-driven loop for the Advisor's read-only map searches."""

import math
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from travelpilot_core.agents.loop import (
    AgentAction,
    AgentTaskContext,
    LoopResult,
    run_constrained_loop,
)
from travelpilot_core.agents.planner import PlannerDecisionFailure, decide_next_action
from travelpilot_core.agents.runtime import AgentRuntime
from travelpilot_core.ports.tools import ToolFailure

from travelpilot_api.advisor.orchestration_state import readonly_action_ref, stage_observation_ref
from travelpilot_api.advisor.task_contract import build_agent_task_context
from travelpilot_api.advisor.task_taxonomy import AdvisorTaskType
from travelpilot_api.advisor.weather_evidence import valid_weather_evidence


@dataclass(frozen=True)
class SearchLoopResult:
    results: list[tuple[dict[str, Any], dict[str, Any]]]
    weather: dict[str, Any] | None
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

    @property
    def pending_question(self) -> str | None:
        return self.loop.pending_question


def _verified_search_result(output: dict[str, Any]) -> bool:
    status = output.get("status")
    if status not in {"SIMULATED", "VERIFIED", "NO_RESULTS"}:
        return False
    places = output.get("places")
    if not isinstance(places, list) or len(places) > 100:
        return False
    if status == "NO_RESULTS":
        return not places
    if not places:
        return False
    if status == "SIMULATED" and any(
        not isinstance(place, dict) or place.get("provider") != "fake" for place in places
    ):
        return False
    if status == "VERIFIED" and any(
        not isinstance(place, dict) or place.get("provider") != "amap" for place in places
    ):
        return False
    return all(
        isinstance(place, dict)
        and isinstance(place.get("provider_id"), str) and 0 < len(place["provider_id"]) <= 120
        and isinstance(place.get("name"), str) and 0 < len(place["name"]) <= 160
        and isinstance(place.get("latitude"), (int, float))
        and not isinstance(place.get("latitude"), bool)
        and math.isfinite(place["latitude"])
        and isinstance(place.get("longitude"), (int, float))
        and not isinstance(place.get("longitude"), bool)
        and math.isfinite(place["longitude"])
        and -90 <= place["latitude"] <= 90
        and -180 <= place["longitude"] <= 180
        for place in places
    )


async def run_advisor_search_loop(
    *,
    runtime: AgentRuntime,
    requests: list[dict[str, Any]],
    city: str | None,
    user_goal: str,
    task_type: AdvisorTaskType = "DISCOVERY",
    task_context: AgentTaskContext | None = None,
    weather_requested: bool = False,
    weather_source: str = "Open-Meteo",
    api_key: str = "",
    model: str = "",
    base_url: str = "",
    token_budget: int = 384,
    checkpoint: Callable[[dict[str, Any]], Awaitable[None]] | None = None,
    cached_weather: dict[str, Any] | None = None,
    weather_observation_callback: Callable[[dict[str, Any]], Awaitable[None]] | None = None,
    cached_searches: dict[str, dict[str, Any]] | None = None,
    search_observation_callback: Callable[[dict[str, Any], dict[str, Any]], Awaitable[None]] | None = None,
    load_decision_checkpoint: Callable[..., Awaitable[dict[str, str] | None]] | None = None,
    save_decision_checkpoint: Callable[..., Awaitable[None]] | None = None,
) -> SearchLoopResult:
    """Search only user-grounded query candidates, adapting after each result.

    The model may select the next approved keyword, ask one question, or finish.
    Query scope and city are reconstructed from the server-owned request plan.
    In fallback mode every approved query is searched in deterministic order.
    """
    plan: list[dict[str, Any]] = []
    seen: set[str] = set()
    for request in requests[:8]:
        keyword = str(request.get("keyword", "")).strip()
        normalized = keyword.casefold()
        if keyword and normalized not in seen:
            plan.append({**request, "keyword": keyword})
            seen.add(normalized)

    results: list[tuple[dict[str, Any], dict[str, Any]]] = []
    attempted: set[str] = set()
    evidence: list[dict[str, Any]] = []
    current_request: list[dict[str, Any] | None] = [None]
    cached_weather = (
        cached_weather
        if city and valid_weather_evidence(cached_weather, expected_source=weather_source)
        and cached_weather["city"].strip().casefold() == city.strip().casefold()
        else None
    )
    weather_result: dict[str, Any] | None = cached_weather
    weather_attempted = cached_weather is not None
    recovered_search_count = 0
    decision_source = "RULE"
    decision_summary = "按已确认的偏好依次查询地点"
    for request in plan:
        recovered = (cached_searches or {}).get(request["keyword"].casefold())
        if recovered is None or not _verified_search_result(recovered):
            continue
        results.append((request, recovered))
        recovered_search_count += 1
        attempted.add(request["keyword"].casefold())
        evidence.append({
            "keyword": request["keyword"],
            "status": recovered["status"],
            "result_count": len(recovered["places"]),
            "place_names": [item["name"][:100] for item in recovered["places"][:5]],
        })
    if recovered_search_count or cached_weather is not None:
        recovered_parts = []
        if recovered_search_count:
            recovered_parts.append(f"地点检索 {recovered_search_count} 项")
        if cached_weather is not None:
            recovered_parts.append("旅行天气预报")
        decision_source = "RECOVERED"
        decision_summary = f"已恢复{ '、'.join(recovered_parts) }的已验证结果"
    tool_schemas = {
        name: {"input_schema": spec.input_schema.model_json_schema()}
        for name, spec in runtime.tools.items()
        if name in {"search_places", "get_trip_weather"}
    }

    async def policy(observation: dict[str, Any], allowed_tools: list[str]) -> AgentAction:
        nonlocal decision_source, decision_summary
        nonlocal weather_attempted, weather_result
        remaining = [item for item in plan if item["keyword"].casefold() not in attempted]
        weather_available = (
            weather_requested and not weather_attempted and "get_trip_weather" in allowed_tools
        )
        if not remaining and not weather_available:
            return AgentAction(kind="FINISH", summary="已完成当前范围的地点检索")

        action = None
        schemas = {
            name: schema for name, schema in tool_schemas.items()
            if (name == "search_places" and remaining)
            or (name == "get_trip_weather" and weather_available)
        }
        if api_key and model and schemas:
            try:
                reserved = runtime.reserve_output_tokens(min(128, max(1, token_budget)))
            except ToolFailure:
                reserved = 0
            if reserved:
                try:
                    action, usage = await decide_next_action(
                        {**observation, "task": "select_next_read_only_advisor_action",
                         "user_goal": user_goal[:500], "destination": city,
                         "approved_queries": [item["keyword"] for item in remaining],
                         "approved_weather_lookup": weather_available,
                         "observed_searches": evidence},
                        schemas,
                        api_key=api_key,
                        model=model,
                        base_url=base_url,
                        token_budget=reserved,
                    )
                except PlannerDecisionFailure as exc:
                    runtime.record_token_usage(
                        input_tokens=exc.usage.prompt_tokens,
                        output_tokens=exc.usage.completion_tokens,
                        total_tokens=exc.usage.total_tokens,
                        reserved=reserved,
                    )
                    action = None
                    decision_source = "RULE_RECOVERY"
                    decision_summary = "模型动作未通过校验，按剩余的已授权搜索词继续"
                except ToolFailure:
                    runtime.release_output_tokens(reserved)
                    action = None
                    decision_source = "RULE_RECOVERY"
                    decision_summary = "模型决策暂不可用，按剩余的已授权搜索词继续"
                else:
                    runtime.record_token_usage(
                        input_tokens=usage.prompt_tokens,
                        output_tokens=usage.completion_tokens,
                        total_tokens=usage.total_tokens,
                        reserved=reserved,
                    )
                    decision_source = "MODEL"
                    decision_summary = action.summary[:180] or "根据地图检索结果选择下一步"

        if action is not None and action.kind == "ASK_USER" and remaining and task_type in {"MEAL", "LODGING"}:
            # A concrete, user-approved read-only lookup should produce evidence
            # before we interrupt the user for optional preferences. The next
            # decision can still ask a focused follow-up after observing results.
            decision_source = "RULE_RECOVERY"
            decision_summary = "先完成本轮已授权的查询，再根据结果追问偏好"
            action = None
        if action is not None and action.kind in {"ASK_USER", "FINISH"}:
            required_remaining = [item for item in remaining if item.get("required") is True]
            if required_remaining:
                # A model may choose how to satisfy a request, but cannot close
                # the search loop while a user-requested domain is still
                # unqueried. Complete the bounded, authorized lookup first.
                decision_source = "RULE_RECOVERY"
                decision_summary = "仍有用户明确要求的类别未查询，先完成已授权搜索"
                action = None
            else:
                return action
        if action is not None and action.kind == "CALL_TOOL" and action.tool == "get_trip_weather":
            if weather_available:
                weather_attempted = True
                return AgentAction(
                    kind="CALL_TOOL", tool="get_trip_weather", arguments={},
                    summary=decision_summary,
                )
            decision_source = "RULE_RECOVERY"
            decision_summary = "天气查询不在本轮已授权范围内"
        if action is not None and action.kind == "CALL_TOOL" and action.tool == "search_places":
            arguments = action.arguments if isinstance(action.arguments, dict) else {}
            requested = str(arguments.get("keyword", "")).strip().casefold()
            match = next((item for item in remaining if item["keyword"].casefold() == requested), None)
            if match is not None:
                current_request[0] = match
                attempted.add(match["keyword"].casefold())
                return AgentAction(
                    kind="CALL_TOOL", tool="search_places",
                    arguments={"keyword": match["keyword"], "city": city},
                    summary=decision_summary,
                )
            decision_source = "RULE_RECOVERY"
            decision_summary = "模型选择了范围外搜索词，已限制为用户确认过的候选词"

        # With no useful model action, continue safely through the bounded server plan.
        if weather_available and (not api_key or not model):
            weather_attempted = True
            decision_source = "RULE"
            decision_summary = "根据你的天气问题查询当前旅行预报"
            return AgentAction(
                kind="CALL_TOOL", tool="get_trip_weather", arguments={},
                summary=decision_summary,
            )
        if not remaining or "search_places" not in allowed_tools:
            return AgentAction(kind="FINISH", summary="已完成当前范围的查询")
        next_request = next(
            (item for item in remaining if item.get("required") is True),
            remaining[0],
        )
        current_request[0] = next_request
        attempted.add(next_request["keyword"].casefold())
        if not api_key or not model:
            decision_source = "RULE"
            decision_summary = "模型未配置，按用户表达的搜索词依次查询"
        return AgentAction(
            kind="CALL_TOOL", tool="search_places",
            arguments={"keyword": next_request["keyword"], "city": city},
            summary=decision_summary,
        )

    def decision_scope(
        observation: dict[str, Any], allowed_tools: list[str],
        pending_action: AgentAction | None = None,
    ):
        remaining = [item for item in plan if item["keyword"].casefold() not in attempted]
        if pending_action is not None and pending_action.tool == "search_places":
            pending_keyword = str(pending_action.arguments.get("keyword", "")).casefold()
            pending_request = next(
                (item for item in plan if item["keyword"].casefold() == pending_keyword), None,
            )
            if pending_request is not None and all(
                item["keyword"].casefold() != pending_keyword for item in remaining
            ):
                remaining.append(pending_request)
                plan_order = {item["keyword"].casefold(): index for index, item in enumerate(plan)}
                remaining.sort(key=lambda item: plan_order[item["keyword"].casefold()])
        weather_available = (
            weather_requested and (not weather_attempted or (
                pending_action is not None and pending_action.tool == "get_trip_weather"
            )) and "get_trip_weather" in allowed_tools
        )
        decision_observation = {
            **observation,
            "task": "select_next_read_only_advisor_action",
            "user_goal": user_goal[:500],
            "destination": city,
            "approved_queries": [item["keyword"] for item in remaining],
            "approved_weather_lookup": weather_available,
            "observed_searches": evidence,
        }
        action_by_ref: dict[str, AgentAction] = {}
        for item in remaining:
            action = AgentAction(
                kind="CALL_TOOL", tool="search_places",
                arguments={"keyword": item["keyword"], "city": city},
            )
            action_by_ref[readonly_action_ref(action.tool, action.arguments)] = action
        if weather_available:
            action = AgentAction(kind="CALL_TOOL", tool="get_trip_weather", arguments={})
            action_by_ref[readonly_action_ref(action.tool, action.arguments)] = action
        return stage_observation_ref(decision_observation), action_by_ref

    async def recover_decision(observation, allowed_tools, decision_number):
        nonlocal weather_attempted
        nonlocal decision_source, decision_summary
        if load_decision_checkpoint is None:
            return None
        observation_ref, action_by_ref = decision_scope(observation, allowed_tools)
        recovered = await load_decision_checkpoint(
            observation_ref=observation_ref,
            decision_number=decision_number,
            approved_actions={key: value.tool for key, value in action_by_ref.items()},
        )
        if recovered is None:
            return None
        action = action_by_ref.get(recovered.get("action_ref", ""))
        if action is None or action.tool != recovered.get("tool"):
            return None
        if action.tool == "search_places":
            request = next(
                (item for item in plan
                 if readonly_action_ref(
                     "search_places", {"keyword": item["keyword"], "city": city},
                 ) == recovered["action_ref"]),
                None,
            )
            if request is None:
                return None
            current_request[0] = request
            attempted.add(request["keyword"].casefold())
        elif action.tool == "get_trip_weather":
            weather_attempted = True
        decision_source = "RECOVERED"
        decision_summary = "恢复并执行了中断前已批准的只读查询"
        return action.model_copy(update={"summary": "恢复中断前已批准的只读查询"})

    async def persist_decision(observation, action, decision_number):
        if save_decision_checkpoint is None or action.kind != "CALL_TOOL":
            return
        effective_tools = sorted(set(tool_schemas) & set(
            task_context.allowed_tools if task_context is not None else tool_schemas
        ))
        observation_ref, action_by_ref = decision_scope(
            observation, effective_tools, pending_action=action,
        )
        action_ref = readonly_action_ref(action.tool or "", action.arguments)
        approved = action_by_ref.get(action_ref)
        if approved is None or approved.tool != action.tool:
            return
        await save_decision_checkpoint(
            observation_ref=observation_ref,
            decision_number=decision_number,
            approved_actions={key: value.tool for key, value in action_by_ref.items()},
            tool=approved.tool,
            action_ref=action_ref,
        )

    async def verifier(name: str, output: dict[str, Any]) -> bool:
        nonlocal weather_result
        if name == "get_trip_weather":
            valid = weather_requested and valid_weather_evidence(
                output, expected_source=weather_source,
            )
            if valid:
                weather_result = output
                if weather_observation_callback is not None:
                    await weather_observation_callback(output)
            return valid
        if name != "search_places" or not _verified_search_result(output):
            return False
        request = current_request[0]
        if request is None:
            return False
        results.append((request, output))
        evidence.append({
            "keyword": request["keyword"],
            "status": output["status"],
            "result_count": len(output["places"]),
            "place_names": [item["name"][:100] for item in output["places"][:5]],
        })
        if search_observation_callback is not None:
            await search_observation_callback(request, output)
        return True

    async def on_checkpoint(state: dict[str, Any]) -> None:
        if checkpoint is not None:
            await checkpoint(state)

    loop = await run_constrained_loop(
        runtime=runtime,
        policy=policy,
        verifier=verifier,
        checkpoint=on_checkpoint,
        task_context=task_context or build_agent_task_context(
            task_type=task_type,
            goal=user_goal,
            known_facts=["destination"] if city else [],
            missing_facts=["destination"] if not city else [],
            available_tools=sorted(tool_schemas),
            weather_requested=weather_requested,
        ),
        initial_observation={
            "search_evidence": evidence,
            **({"recovered_search_count": len(results)} if results else {}),
            **({"verified_trip_weather": cached_weather} if cached_weather is not None else {}),
        },
        max_decisions=min(len(plan) + int(weather_requested) + 1, 10),
        allowed_tool_names=set(tool_schemas),
        continue_on_tool_failure=True,
        continue_on_verifier_failure=True,
        recover_decision=recover_decision,
        persist_decision=persist_decision if save_decision_checkpoint is not None else None,
    )
    if loop.pending_question:
        decision_source = "MODEL"
        decision_summary = "根据当前检索结果向用户确认下一步"
    elif loop.status == "STEP_LIMIT":
        decision_source = "RULE_RECOVERY"
        decision_summary = "已达到搜索步数上限，停止继续调用地图服务"
    return SearchLoopResult(results, weather_result, loop, runtime, decision_source, decision_summary)
