import re
from datetime import UTC, datetime, timedelta
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from travelpilot_core.agents.loop import (
    AgentAction,
    AgentTaskContext,
    LoopResult,
    run_constrained_loop,
)
from travelpilot_core.agents.planner import (
    PlannerDecisionFailure,
    decide_next_action,
    decide_preferences,
)
from travelpilot_core.agents.runtime import AgentResult, AgentRuntime, redact_tool_trace
from travelpilot_core.agents.verifiers import verify_distance_matrix, verify_trip_context
from travelpilot_core.application.plans import PlanService
from travelpilot_core.ports.tool_schemas import GetTripContextInput
from travelpilot_core.ports.tools import ToolFailure, ToolSpec
from travelpilot_infra.db.models.poi import PoiModel
from travelpilot_infra.db.models.trip import TripModel
from travelpilot_infra.db.models.wishlist import WishlistItemModel
from travelpilot_infra.db.models.workflow import AgentRunModel, MemoryModel, ToolCallModel
from travelpilot_infra.db.session import get_session
from travelpilot_infra.providers.maps.errors import MapsProviderError
from travelpilot_infra.providers.tool_adapters import map_tool_specs

from travelpilot_api.api.v1.plans import to_read
from travelpilot_api.dependencies import get_maps_provider, get_owned_trip
from travelpilot_api.settings import settings

router = APIRouter(tags=["agents"])


def _distance_matrix_fallback(loop_result: LoopResult) -> str | None:
    if loop_result.status in {"FAILED", "REJECTED"}:
        failed_tool = next((item.get("tool") for item in loop_result.transitions
                            if item.get("status") in {"FAILED", "REJECTED"}), None)
        if failed_tool != "get_distance_matrix":
            raise ToolFailure(loop_result.error or "AGENT_LOOP_FAILED")
        if loop_result.status == "REJECTED":
            return "地图矩阵输出未通过校验，使用本地距离估算"
        return "地图矩阵暂不可用，使用本地距离估算"
    if loop_result.status != "SUCCEEDED":
        raise ToolFailure(loop_result.error or "AGENT_LOOP_FAILED")
    return None


class PlannerRequest(BaseModel):
    preference: str = Field(default="", max_length=2000)


@router.post("/trips/{trip_id}/agent-plan-runs")
async def run_planner_agent(trip_id: UUID, payload: PlannerRequest,
                            session: AsyncSession = Depends(get_session), maps=Depends(get_maps_provider),
                            _trip: TripModel = Depends(get_owned_trip),
                            idempotency_key: str | None = Header(default=None, alias="Idempotency-Key", max_length=80)):
    if idempotency_key and not re.fullmatch(r"[A-Za-z0-9._:-]{8,80}", idempotency_key):
        raise HTTPException(422, detail="Idempotency-Key 格式无效")
    run = None
    recovering = False
    if idempotency_key:
        await session.scalar(select(TripModel.id).where(TripModel.id == trip_id).with_for_update())
        run = await session.scalar(select(AgentRunModel).where(
            AgentRunModel.trip_id == trip_id,
            AgentRunModel.agent == "planner",
            AgentRunModel.idempotency_key == idempotency_key,
        ).with_for_update())
        if run is not None:
            if (run.input_data or {}).get("preference", "") != payload.preference:
                raise HTTPException(409, detail="Idempotency-Key 已用于另一组规划输入")
            if run.status == "RUNNING":
                checkpoint_data = (run.output_data or {}).get("checkpoint", {})
                checkpoint_at = checkpoint_data.get("updated_at")
                if checkpoint_at:
                    last_progress = datetime.fromisoformat(checkpoint_at)
                else:
                    last_progress = run.created_at
                if last_progress.tzinfo is None:
                    last_progress = last_progress.replace(tzinfo=UTC)
                if datetime.now(UTC) - last_progress < timedelta(minutes=2):
                    raise HTTPException(409, detail="同一幂等键的规划仍在运行，请稍后重试")
            existing_plan_id = (run.output_data or {}).get("plan_id")
            if run.status == "SUCCEEDED" and existing_plan_id:
                full = await PlanService(session).get(trip_id, UUID(existing_plan_id))
                if full is not None:
                    return {"run_id": run.id, "status": run.status, "plan": to_read(*full),
                            "explanation": (run.output_data or {}).get("explanation", ""),
                            "tool_calls": [], "execution_metrics": (run.output_data or {}).get("execution_metrics", {})}
            run.status = "RUNNING"
            run.error = None
            run.output_data = {**(run.output_data or {}), "checkpoint": {
                "state": "RECOVERING", "transition": 0,
                "updated_at": datetime.now(UTC).isoformat(),
            }}
            recovering = True
        else:
            run = AgentRunModel(trip_id=trip_id, agent="planner", status="RUNNING", token_usage=0,
                                idempotency_key=idempotency_key, input_data={"preference": payload.preference},
                                output_data={"checkpoint": {"state": "STARTED", "transition": 0,
                                                             "updated_at": datetime.now(UTC).isoformat()}})
            session.add(run)
    else:
        run = AgentRunModel(trip_id=trip_id, agent="planner", status="RUNNING", token_usage=0,
                            input_data={"preference": payload.preference},
                            output_data={"checkpoint": {"state": "STARTED", "transition": 0,
                                                         "updated_at": datetime.now(UTC).isoformat()}})
        session.add(run)
    await session.commit()

    def checkpoint_data(state: str, **details) -> dict:
        entry = {"state": state, "updated_at": datetime.now(UTC).isoformat(), **details}
        previous = run.output_data or {}
        history = [*previous.get("checkpoints", []), entry][-32:]
        return {**previous, "checkpoint": entry, "checkpoints": history}

    async def checkpoint(state: str, **details) -> None:
        run.output_data = checkpoint_data(state, **details)
        await session.commit()

    await checkpoint("RECOVERING" if recovering else "STARTED", transition=0)

    async def get_context(arguments: GetTripContextInput) -> dict:
        if arguments.trip_id != trip_id:
            raise ToolFailure("TRIP_SCOPE_MISMATCH")
        rows = (await session.execute(select(WishlistItemModel, PoiModel)
               .join(PoiModel, WishlistItemModel.poi_id == PoiModel.id)
               .where(WishlistItemModel.trip_id == trip_id)
               .order_by(WishlistItemModel.priority.asc(), WishlistItemModel.created_at.asc()))).all()
        memories = (await session.scalars(select(MemoryModel).where(
            MemoryModel.trip_id == trip_id, MemoryModel.confirmed.is_(True)))).all()
        return {"trip_id": str(trip_id), "wishlist": [poi.name for _, poi in rows],
                "points": [(float(poi.latitude), float(poi.longitude)) for _, poi in rows],
                "preference": payload.preference, "memories": [memory.text for memory in memories]}

    async def trace(data: dict) -> None:
        safe_trace = redact_tool_trace(data)
        runtime_fields = {
            field: safe_trace.pop(field)
            for field in ("logical_step", "attempt", "tool_attempts_total", "retryable")
            if field in safe_trace
        }
        if runtime_fields:
            safe_trace["input_data"] = {**(safe_trace.get("input_data") or {}), "runtime": runtime_fields}
        session.add(ToolCallModel(run_id=run.id, **safe_trace))
        await session.commit()

    runtime = AgentRuntime([ToolSpec("get_trip_context", GetTripContextInput, get_context),
                            *map_tool_specs(maps)], trace)

    async def planner_policy(observation: dict, allowed_tools: list[str]) -> AgentAction:
        trip_context = observation.get("get_trip_context")
        if trip_context is None:
            return AgentAction(kind="CALL_TOOL", tool="get_trip_context", arguments={"trip_id": trip_id})
        points = trip_context.get("points", [])
        if len(points) < 2 or len(points) > 30 or "get_distance_matrix" in observation:
            return AgentAction(kind="FINISH", summary="required read-only evidence collected")
        if not settings.llm_api_key or not settings.llm_model:
            return AgentAction(kind="CALL_TOOL", tool="get_distance_matrix", arguments={"points": points})
        specs = {name: {"input_schema": runtime.tools[name].input_schema.model_json_schema()}
                 for name in allowed_tools if name == "get_distance_matrix"}
        reservation = runtime.reserve_output_tokens(128)
        try:
            action, usage = await decide_next_action(
                {"wishlist_count": len(trip_context.get("wishlist", [])),
                 "matrix_observed": False}, specs,
                api_key=settings.llm_api_key, model=settings.llm_model,
                base_url=settings.llm_base_url, token_budget=reservation,
            )
        except PlannerDecisionFailure as exc:
            runtime.record_token_usage(input_tokens=exc.usage.prompt_tokens,
                                       output_tokens=exc.usage.completion_tokens,
                                       reserved=reservation, total_tokens=exc.usage.total_tokens)
            return AgentAction(kind="CALL_TOOL", tool="get_distance_matrix", arguments={"points": points})
        except ToolFailure:
            runtime.release_output_tokens(reservation)
            return AgentAction(kind="CALL_TOOL", tool="get_distance_matrix", arguments={"points": points})
        runtime.record_token_usage(input_tokens=usage.prompt_tokens, output_tokens=usage.completion_tokens,
                                   reserved=reservation, total_tokens=usage.total_tokens)
        if action.kind == "CALL_TOOL" and action.tool == "get_distance_matrix":
            return AgentAction(kind="CALL_TOOL", tool=action.tool, arguments={"points": points})
        if action.kind == "FINISH":
            return action
        return AgentAction(kind="CALL_TOOL", tool="get_distance_matrix", arguments={"points": points})

    expected_matrix_size = 0

    async def verify_read_only_output(name: str, output: dict) -> bool:
        nonlocal expected_matrix_size
        if name == "get_trip_context":
            if not verify_trip_context(output, str(trip_id)):
                return False
            expected_matrix_size = len(output["points"])
            return True
        if name == "get_distance_matrix":
            return verify_distance_matrix(output, expected_matrix_size)
        return False

    try:
        loop_result = await run_constrained_loop(
            runtime=runtime, policy=planner_policy, verifier=verify_read_only_output,
            checkpoint=lambda state: checkpoint(state["state"],
                                                transition=state.get("transition", 0),
                                                tool=state.get("tool"), error=state.get("error")),
            task_context=AgentTaskContext(
                task_type="SCHEDULE",
                goal="Build a schedule proposal from the selected trip places and current preferences",
                known_facts=["trip_id"],
                missing_facts=[],
                success_criteria=["load verified trip context", "verify travel times when available"],
                allowed_tools=["get_trip_context", "get_distance_matrix"],
                approval_boundary="DRAFT_ONLY",
            ),
            initial_observation={"trip_id": str(trip_id)}, max_decisions=3,
            allowed_tool_names={"get_trip_context", "get_distance_matrix"},
        )
        if "get_trip_context" not in loop_result.observation:
            raise ToolFailure(loop_result.error or "AGENT_LOOP_FAILED")
        context = loop_result.observation["get_trip_context"]
        matrix_result = loop_result.observation.get("get_distance_matrix")
        distance_fallback = _distance_matrix_fallback(loop_result)
        if len(context.get("points", [])) > 30:
            distance_fallback = "收藏地点超过地图矩阵的 30 点限制，使用本地距离估算"
        elif len(context.get("points", [])) > 1 and matrix_result is None and distance_fallback is None:
            distance_fallback = "本轮未请求地图矩阵，使用本地距离估算"
        await checkpoint("CONTEXT_VERIFIED", transition=1, wishlist_count=len(context["wishlist"]))
        output_token_reservation = runtime.reserve_output_tokens(256)
        try:
            decision, usage = await decide_preferences(
                "\n".join([context["preference"], *context["memories"]]),
                api_key=settings.llm_api_key, model=settings.llm_model,
                base_url=settings.llm_base_url, token_budget=output_token_reservation,
            )
        except BaseException as exc:
            if isinstance(exc, PlannerDecisionFailure):
                runtime.record_token_usage(
                    input_tokens=exc.usage.prompt_tokens,
                    output_tokens=exc.usage.completion_tokens,
                    reserved=output_token_reservation,
                    total_tokens=exc.usage.total_tokens,
                )
            else:
                runtime.release_output_tokens(output_token_reservation)
            raise
        runtime.record_token_usage(
            input_tokens=usage.prompt_tokens,
            output_tokens=usage.completion_tokens,
            reserved=output_token_reservation,
            total_tokens=usage.total_tokens,
        )
        await checkpoint("DECISION_VERIFIED", transition=2, pace=decision.pace)
        class TracedMaps:
            async def get_distance_matrix(self, _points):
                return matrix_result["durations_min"]

        plan = await PlanService(session).generate(
            trip_id, TracedMaps() if matrix_result is not None else None,
            decision.constraints(), commit=False,
        )
        if distance_fallback:
            plan.warnings = [*plan.warnings, distance_fallback]
        full = await PlanService(session).get(trip_id, plan.id)
        result = AgentResult(status="SUCCEEDED", output={
            "plan_id": str(plan.id), "explanation": f"根据 {len(context['wishlist'])} 个收藏地点生成计划；{decision.explanation}；已执行时间与营业约束检查。" + (f" {distance_fallback}。" if distance_fallback else ""),
            "pace": decision.pace,
            "execution_metrics": runtime.execution_metrics,
        }, tool_calls=runtime.calls)
        run.status = result.status
        run.token_usage = runtime.execution_metrics["total_tokens"]
        run.output_data = {**checkpoint_data("SUCCEEDED", transition=4, plan_id=str(plan.id)),
                           **result.output}
        await session.commit()
        return {"run_id": run.id, "status": result.status, "plan": to_read(*full),
                "explanation": result.output["explanation"], "tool_calls": runtime.calls,
                "execution_metrics": runtime.execution_metrics}
    except (ToolFailure, MapsProviderError, ValueError) as exc:
        await session.rollback()
        run = await session.get(AgentRunModel, run.id)
        run.status = "FAILED"
        run.error = str(exc) if isinstance(exc, ToolFailure) else "PLANNING_FAILED"
        run.token_usage = runtime.execution_metrics["total_tokens"]
        run.output_data = {**checkpoint_data("FAILED", error=run.error),
                           "execution_metrics": runtime.execution_metrics}
        await session.commit()
        raise HTTPException(400 if isinstance(exc, ValueError) else 503,
                            detail=run.error) from exc
