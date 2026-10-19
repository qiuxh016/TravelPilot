"""Observation-driven route evidence selection for existing-trip planning."""

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel
from travelpilot_core.agents.loop import (
    AgentAction,
    AgentTaskContext,
    LoopResult,
    run_constrained_loop,
)
from travelpilot_core.agents.planner import PlannerDecisionFailure, decide_next_action
from travelpilot_core.agents.runtime import AgentRuntime
from travelpilot_core.agents.verifiers import verify_distance_matrix
from travelpilot_core.ports.tools import ToolFailure, ToolSpec

from travelpilot_api.advisor.decision_refs import readonly_action_ref, stage_observation_ref
from travelpilot_api.advisor.task_contract import build_agent_task_context


@dataclass(frozen=True)
class RouteEvidenceResult:
    matrix: list[list[float]] | None
    loop: LoopResult
    decision_source: str
    decision_summary: str
    evidence_status: str
    evidence_source: str | None
    evidence_retrieved_at: str | None = None
    draft: dict[str, Any] | None = None
    question: str | None = None


class _ScheduleDraftInput(BaseModel):
    """No model-provided values are needed to build the server-scoped draft."""


ScheduleBuilder = Callable[[list[list[float]] | None], Awaitable[dict[str, Any]]]


def reusable_route_evidence(
    cache: dict | None, candidate_ids: list[str], context_revision: int,
    *, expected_source: str | None = None, now: datetime | None = None,
    max_age_seconds: int = 600,
) -> dict | None:
    """Return a fresh verified matrix reordered to the current candidate order."""
    if not isinstance(cache, dict):
        return None
    try:
        retrieved_at = datetime.fromisoformat(cache["retrieved_at"])
        if retrieved_at.tzinfo is None or retrieved_at.utcoffset() is None:
            return None
        age_seconds = ((now or datetime.now(UTC)) - retrieved_at).total_seconds()
        cached_ids = cache["candidate_ids"]
        cached_matrix = cache["durations_min"]
    except (KeyError, TypeError, ValueError):
        return None
    if (
        not isinstance(cached_ids, list)
        or len(cached_ids) != len(candidate_ids)
        or len(set(cached_ids)) != len(cached_ids)
        or set(cached_ids) != set(candidate_ids)
        or cache.get("context_revision") != context_revision
        or cache.get("status") not in {"VERIFIED", "SIMULATED"}
        or cache.get("status") == "VERIFIED" and cache.get("source") != "AMap Web Service"
        or cache.get("status") == "SIMULATED" and cache.get("source") != "Local simulated maps"
        or expected_source is not None and cache.get("source") != expected_source
        or not 0 <= age_seconds <= max_age_seconds
        or not verify_distance_matrix({"durations_min": cached_matrix}, len(candidate_ids))
    ):
        return None
    cached_index = {candidate_id: index for index, candidate_id in enumerate(cached_ids)}
    order = [cached_index[candidate_id] for candidate_id in candidate_ids]
    return {
        "durations_min": [[cached_matrix[row][column] for column in order] for row in order],
        "status": cache.get("status", "VERIFIED"),
        "source": cache.get("source"),
        "retrieved_at": retrieved_at.isoformat(),
    }


async def gather_route_evidence(
    runtime: AgentRuntime,
    points: list[tuple[float, float]],
    *,
    trip_context: dict[str, Any],
    user_goal: str = "",
    api_key: str,
    model: str,
    base_url: str,
    token_budget: int,
    cached_evidence: dict | None = None,
    task_context: AgentTaskContext | None = None,
    schedule_builder: ScheduleBuilder | None = None,
    checkpoint: Callable[[dict[str, Any]], Awaitable[None]] | None = None,
    route_observation_callback: Callable[[dict[str, Any]], Awaitable[None]] | None = None,
    load_decision_checkpoint: Callable[..., Awaitable[dict[str, str] | None]] | None = None,
    save_decision_checkpoint: Callable[..., Awaitable[None]] | None = None,
) -> RouteEvidenceResult:
    """Let the model select route evidence or a deterministic draft proposal.

    Tool arguments are always reconstructed from server-owned coordinates. The
    model only chooses an allowlisted action; it cannot change the trip scope or
    persist a plan. A deterministic policy takes over when the model is absent,
    invalid, or a tool observation requires a safe recovery.
    """
    matrix_tool = runtime.tools["get_distance_matrix"]
    schemas = {
        "get_distance_matrix": {
            "input_schema": matrix_tool.input_schema.model_json_schema(),
        },
    }
    matrix_holder = [cached_evidence.get("durations_min") if cached_evidence else None]
    if schedule_builder:
        async def build_schedule(_: _ScheduleDraftInput) -> dict[str, Any]:
            return await schedule_builder(matrix_holder[0])

        runtime.tools["build_schedule_draft"] = ToolSpec(
            "build_schedule_draft", _ScheduleDraftInput, build_schedule,
            timeout_seconds=runtime.tools["get_distance_matrix"].timeout_seconds,
            retry_limit=0,
        )
        schemas["build_schedule_draft"] = {
            "input_schema": _ScheduleDraftInput.model_json_schema(),
        }
        runtime.max_steps = max(runtime.max_steps, 4)
        runtime.max_attempts = max(runtime.max_attempts, 4)
    initial_observation: dict[str, Any] = {
        "task": "schedule_existing_trip_places",
        "user_goal": user_goal.strip()[:1200],
        "trip_context": {
            "destination": str(trip_context.get("destination") or "")[:100],
            "start_date": str(trip_context.get("start_date") or "")[:10],
            "end_date": str(trip_context.get("end_date") or "")[:10],
            "pace_level": trip_context.get("pace_level"),
            "stop_count": len(points),
            "has_dates": bool(trip_context.get("has_dates")),
            "fixed_stop_count": int(trip_context.get("fixed_stop_count", 0)),
            "route_evidence_needed_for_multi_stop_schedule": len(points) > 1,
        },
    }
    decision_source = "RULE"
    decision_summary = "按多地点排程规则检查路线证据"
    if cached_evidence:
        initial_observation["get_distance_matrix"] = cached_evidence
        decision_source = "OBSERVATION"
        decision_summary = "复用同一地点集合和旅行版本下仍有效的路线证据"

    def decision_scope(observation: dict[str, Any], allowed: list[str], pending_action=None):
        decision_observation = {
            "task_context": task_context.model_dump(mode="json") if task_context else None,
            "trip_context": initial_observation["trip_context"],
            "candidate_scope": trip_context.get("candidate_ids", []),
            "route_points": points,
            "loop_observation": observation,
        }
        action_by_ref: dict[str, AgentAction] = {}
        if "get_distance_matrix" in allowed and not matrix_holder[0] and len(points) > 1:
            action = AgentAction(kind="CALL_TOOL", tool="get_distance_matrix", arguments={"points": points})
            action_by_ref[readonly_action_ref(action.tool, action.arguments)] = action
        route_failed = any(key in observation for key in (
            "get_distance_matrix_failure", "get_distance_matrix_verification_failure",
        ))
        draft_ready = matrix_holder[0] is not None or len(points) <= 1 or route_failed
        if "build_schedule_draft" in allowed and schedule_builder and draft_ready:
            action = AgentAction(kind="CALL_TOOL", tool="build_schedule_draft", arguments={})
            action_by_ref[readonly_action_ref(action.tool, action.arguments)] = action
        if pending_action is not None and pending_action.kind == "CALL_TOOL":
            action_ref = readonly_action_ref(pending_action.tool or "", pending_action.arguments)
            if action_ref in action_by_ref:
                action_by_ref[action_ref] = pending_action
        return stage_observation_ref(decision_observation), action_by_ref

    async def recover_decision(observation: dict[str, Any], allowed: list[str], decision_number: int):
        nonlocal decision_source, decision_summary
        if load_decision_checkpoint is None:
            return None
        observation_ref, action_by_ref = decision_scope(observation, allowed)
        recovered = await load_decision_checkpoint(
            observation_ref=observation_ref,
            decision_number=decision_number,
            approved_actions={key: action.tool for key, action in action_by_ref.items()},
        )
        if recovered is None:
            return None
        action = action_by_ref.get(recovered.get("action_ref", ""))
        if action is None or action.tool != recovered.get("tool"):
            return None
        decision_source = "RECOVERED"
        decision_summary = "恢复中断前已批准的排程步骤"
        return action.model_copy(update={"summary": decision_summary})

    async def persist_decision(observation: dict[str, Any], action: AgentAction, decision_number: int):
        if save_decision_checkpoint is None or action.kind != "CALL_TOOL":
            return
        allowed = sorted(set(schemas) & set(task_context.allowed_tools if task_context else schemas))
        observation_ref, action_by_ref = decision_scope(observation, allowed, pending_action=action)
        action_ref = readonly_action_ref(action.tool or "", action.arguments)
        approved = action_by_ref.get(action_ref)
        if approved is None or approved.tool != action.tool:
            return
        await save_decision_checkpoint(
            observation_ref=observation_ref,
            decision_number=decision_number,
            approved_actions={key: item.tool for key, item in action_by_ref.items()},
            tool=approved.tool,
            action_ref=action_ref,
        )

    async def policy(observation: dict, allowed_tools: list[str]) -> AgentAction:
        nonlocal decision_source, decision_summary
        if "build_schedule_draft" in observation:
            return AgentAction(kind="FINISH", summary="待确认排程草案已生成")
        if "build_schedule_draft_failure" in observation or "build_schedule_draft_verification_failure" in observation:
            decision_summary = "草案生成或验证失败；停止本轮操作并保留正式行程不变"
            return AgentAction(kind="FINISH", summary=decision_summary)
        route_available = "get_distance_matrix" in observation
        route_failed = any(key in observation for key in (
            "get_distance_matrix_failure", "get_distance_matrix_verification_failure",
        ))
        if route_available:
            matrix_holder[0] = observation["get_distance_matrix"].get("durations_min")
        if route_failed and schedule_builder:
            failure = (
                observation.get("get_distance_matrix_failure", {}).get("error")
                or observation.get("get_distance_matrix_verification_failure", {}).get("error")
                or "VERIFICATION_FAILED"
            )
            decision_source = f"{decision_source}_RECOVERY"
            decision_summary = f"路线证据不可用（{failure}）；使用本地距离估算生成带警告的草案"
            return AgentAction(kind="CALL_TOOL", tool="build_schedule_draft", arguments={})
        if route_available and not schedule_builder:
            return AgentAction(kind="FINISH", summary="路线证据已取得并验证")
        if route_available and cached_evidence and schedule_builder:
            # A retry that restored a verified matrix is already past the route
            # decision. Continue at the next deterministic stage instead of
            # spending another model call to choose the same schedule builder.
            decision_source = "RECOVERED"
            decision_summary = "恢复已验证的地点间路线证据，继续构建待确认草案"
            return AgentAction(
                kind="CALL_TOOL", tool="build_schedule_draft", arguments={},
                summary=decision_summary,
            )
        if route_failed and not schedule_builder:
            failure = observation["get_distance_matrix_failure"].get("error", "UNKNOWN")
            decision_source = f"{decision_source}_RECOVERY"
            decision_summary = f"路线工具返回失败（{failure}）；停止重复调用"
            return AgentAction(kind="FINISH", summary=decision_summary)
        if schedule_builder and route_failed:
            return AgentAction(kind="CALL_TOOL", tool="build_schedule_draft", arguments={})
        if not api_key.strip() or not model.strip():
            if len(points) > 1 and not route_available and "get_distance_matrix" in allowed_tools:
                return AgentAction(
                    kind="CALL_TOOL", tool="get_distance_matrix",
                    arguments={"points": points},
                )
            if schedule_builder:
                return AgentAction(kind="CALL_TOOL", tool="build_schedule_draft", arguments={})
            return AgentAction(kind="FINISH", summary="单地点无需路线矩阵")

        reservation = runtime.reserve_output_tokens(min(128, max(1, token_budget)))
        draft_ready = route_available or len(points) <= 1 or route_failed
        decision_schemas = {
            name: schemas[name] for name in allowed_tools
            if name in schemas and not (
                name == "get_distance_matrix" and (route_available or len(points) < 2)
            ) and not (name == "build_schedule_draft" and not draft_ready)
        }
        try:
            action, usage = await decide_next_action(
                observation,
                decision_schemas,
                api_key=api_key,
                model=model,
                base_url=base_url,
                token_budget=reservation,
            )
        except PlannerDecisionFailure as exc:
            runtime.record_token_usage(
                input_tokens=exc.usage.prompt_tokens,
                output_tokens=exc.usage.completion_tokens,
                reserved=reservation,
                total_tokens=exc.usage.total_tokens,
            )
            decision_source = "RULE_FALLBACK"
            decision_summary = "模型动作未通过结构校验，回退到确定性路线检查规则"
            if len(points) > 1 and not route_available:
                return AgentAction(kind="CALL_TOOL", tool="get_distance_matrix",
                                   arguments={"points": points}, summary=decision_summary)
            if schedule_builder:
                return AgentAction(kind="CALL_TOOL", tool="build_schedule_draft", arguments={},
                                   summary=decision_summary)
            return AgentAction(kind="FINISH", summary=decision_summary)
        except ToolFailure:
            runtime.release_output_tokens(reservation)
            decision_source = "RULE_FALLBACK"
            decision_summary = "模型决策暂不可用，回退到确定性路线检查规则"
            if len(points) > 1 and not route_available:
                return AgentAction(kind="CALL_TOOL", tool="get_distance_matrix",
                                   arguments={"points": points}, summary=decision_summary)
            if schedule_builder:
                return AgentAction(kind="CALL_TOOL", tool="build_schedule_draft", arguments={},
                                   summary=decision_summary)
            return AgentAction(kind="FINISH", summary=decision_summary)

        runtime.record_token_usage(
            input_tokens=usage.prompt_tokens,
            output_tokens=usage.completion_tokens,
            reserved=reservation,
            total_tokens=usage.total_tokens,
        )
        decision_source = "MODEL"
        decision_summary = action.summary[:180] or (
            "模型判断需要核验地点间交通时间"
            if action.kind == "CALL_TOOL"
            else "模型判断当前无需调用更多只读工具"
        )
        if action.kind == "CALL_TOOL" and action.tool == "get_distance_matrix":
            # Never trust model-provided coordinates or arguments.
            return AgentAction(kind="CALL_TOOL", tool="get_distance_matrix", arguments={"points": points},
                               summary=decision_summary)
        if action.kind == "CALL_TOOL" and action.tool == "build_schedule_draft" and schedule_builder:
            # The model cannot skip required route evidence. The runtime tool
            # remains available as a warned fallback only after a route failure.
            if len(points) > 1 and not draft_ready:
                decision_source = "RUNTIME_GUARD"
                decision_summary = "多地点排程必须先检查路线；已忽略提前生成草案的动作"
                return AgentAction(
                    kind="CALL_TOOL", tool="get_distance_matrix", arguments={"points": points},
                    summary=decision_summary,
                )
            return AgentAction(kind="CALL_TOOL", tool="build_schedule_draft", arguments={},
                               summary=decision_summary)
        if action.kind == "ASK_USER":
            question = (action.question or action.summary).strip()[:500]
            decision_summary = question or "还缺少决定排程所需的信息，需要先向用户确认"
            return AgentAction(kind="ASK_USER", question=decision_summary, summary=decision_summary)
        if action.kind == "FINISH":
            if schedule_builder:
                if len(points) > 1 and not draft_ready:
                    decision_source = "RUNTIME_GUARD"
                    decision_summary = "多地点排程尚缺路线检查；先取得路线证据再生成草案"
                    return AgentAction(
                        kind="CALL_TOOL", tool="get_distance_matrix", arguments={"points": points},
                        summary=decision_summary,
                    )
                return AgentAction(kind="CALL_TOOL", tool="build_schedule_draft", arguments={},
                                   summary=decision_summary)
            return AgentAction(kind="FINISH", summary=decision_summary)
        decision_source = "RULE_FALLBACK"
        decision_summary = "模型选择了不支持的动作，回退到确定性路线检查规则"
        if len(points) > 1 and not route_available:
            return AgentAction(kind="CALL_TOOL", tool="get_distance_matrix",
                               arguments={"points": points}, summary=decision_summary)
        if schedule_builder:
            return AgentAction(kind="CALL_TOOL", tool="build_schedule_draft", arguments={},
                               summary=decision_summary)
        return AgentAction(kind="FINISH", summary=decision_summary)

    async def verifier(name: str, output: dict) -> bool:
        if name == "get_distance_matrix":
            valid = verify_distance_matrix(output, len(points))
            if valid and route_observation_callback is not None:
                await route_observation_callback(output)
            return valid
        if name == "build_schedule_draft":
            if output.get("feasible") is False:
                return bool(str(output.get("reason", "")).strip())
            schedules = output.get("schedules")
            expected_ids = output.get("candidate_ids")
            actual_ids = [item.get("candidate_id") for item in schedules or [] if isinstance(item, dict)]
            return (
                output.get("feasible") is True
                and isinstance(schedules, list)
                and len(schedules) == len(points)
                and isinstance(expected_ids, list)
                and len(actual_ids) == len(expected_ids)
                and set(actual_ids) == set(expected_ids)
            )
        return False

    async def on_checkpoint(state: dict) -> None:
        if checkpoint is not None:
            await checkpoint(state)

    result = await run_constrained_loop(
        runtime=runtime,
        policy=policy,
        verifier=verifier,
        checkpoint=on_checkpoint,
        task_context=task_context or build_agent_task_context(
            task_type="SCHEDULE",
            goal=user_goal or "Create a schedule proposal from selected trip places",
            known_facts=["selected_places"] if points else [],
            missing_facts=["selected_places"] if not points else [],
            available_tools=sorted(schemas),
        ),
        initial_observation=initial_observation,
        max_decisions=4,
        allowed_tool_names=set(schemas),
        continue_on_tool_failure=True,
        continue_on_verifier_failure=True,
        recover_decision=recover_decision if load_decision_checkpoint is not None else None,
        persist_decision=persist_decision if save_decision_checkpoint is not None else None,
    )
    observed = result.observation.get("get_distance_matrix")
    proposed_draft = result.observation.get("build_schedule_draft")
    return RouteEvidenceResult(
        matrix=observed.get("durations_min") if isinstance(observed, dict) else None,
        loop=result,
        decision_source=decision_source,
        decision_summary=decision_summary,
        evidence_status=(
            str(observed.get("status", "UNKNOWN")) if isinstance(observed, dict)
            else "REJECTED" if "get_distance_matrix_verification_failure" in result.observation
            else "UNAVAILABLE" if "get_distance_matrix_failure" in result.observation
            else "NOT_USED"
        ),
        evidence_source=str(observed.get("source")) if isinstance(observed, dict) and observed.get("source") else None,
        evidence_retrieved_at=(
            str(observed.get("retrieved_at"))
            if isinstance(observed, dict) and observed.get("retrieved_at") else None
        ),
        draft=proposed_draft if isinstance(proposed_draft, dict) else None,
        question=result.pending_question,
    )
