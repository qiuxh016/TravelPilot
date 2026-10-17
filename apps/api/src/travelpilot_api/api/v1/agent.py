from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from travelpilot_core.agents.planner import decide_preferences
from travelpilot_core.agents.runtime import AgentResult, AgentRuntime, redact_tool_trace
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


class PlannerRequest(BaseModel):
    preference: str = Field(default="", max_length=2000)


@router.post("/trips/{trip_id}/agent-plan-runs")
async def run_planner_agent(trip_id: UUID, payload: PlannerRequest,
                            session: AsyncSession = Depends(get_session), maps=Depends(get_maps_provider),
                            _trip: TripModel = Depends(get_owned_trip)):
    run = AgentRunModel(trip_id=trip_id, agent="planner", status="RUNNING", token_usage=0)
    session.add(run)
    await session.commit()

    async def get_context(arguments: GetTripContextInput) -> dict:
        if arguments.trip_id != trip_id:
            raise ToolFailure("TRIP_SCOPE_MISMATCH")
        rows = (await session.execute(select(WishlistItemModel, PoiModel)
               .join(PoiModel, WishlistItemModel.poi_id == PoiModel.id)
               .where(WishlistItemModel.trip_id == trip_id))).all()
        memories = (await session.scalars(select(MemoryModel).where(
            MemoryModel.trip_id == trip_id, MemoryModel.confirmed.is_(True)))).all()
        return {"trip_id": str(trip_id), "wishlist": [poi.name for _, poi in rows],
                "preference": payload.preference, "memories": [memory.text for memory in memories]}

    async def trace(data: dict) -> None:
        session.add(ToolCallModel(run_id=run.id, **redact_tool_trace(data)))
        await session.commit()

    runtime = AgentRuntime([ToolSpec("get_trip_context", GetTripContextInput, get_context),
                            *map_tool_specs(maps)], trace)
    try:
        context = await runtime.call("get_trip_context", {"trip_id": trip_id})
        decision, tokens = await decide_preferences(
            "\n".join([context["preference"], *context["memories"]]),
            api_key=settings.llm_api_key, model=settings.llm_model,
            base_url=settings.llm_base_url, token_budget=runtime.token_budget,
        )
        class TracedMaps:
            async def get_distance_matrix(self, points):
                result = await runtime.call("get_distance_matrix", {"points": points})
                return result["durations_min"]

        plan = await PlanService(session).generate(trip_id, TracedMaps(), decision.constraints())
        full = await PlanService(session).get(trip_id, plan.id)
        result = AgentResult(status="SUCCEEDED", output={
            "plan_id": str(plan.id), "explanation": f"根据 {len(context['wishlist'])} 个收藏地点生成计划；{decision.explanation}；已执行时间与营业约束检查。",
            "pace": decision.pace,
        }, tool_calls=runtime.calls)
        run.status = result.status
        run.token_usage = tokens
        run.output_data = result.output
        await session.commit()
        return {"run_id": run.id, "status": result.status, "plan": to_read(*full),
                "explanation": result.output["explanation"], "tool_calls": runtime.calls}
    except (ToolFailure, MapsProviderError, ValueError) as exc:
        await session.rollback()
        run = await session.get(AgentRunModel, run.id)
        run.status = "FAILED"
        run.error = str(exc) if isinstance(exc, ToolFailure) else "PLANNING_FAILED"
        await session.commit()
        raise HTTPException(400 if isinstance(exc, ValueError) else 503,
                            detail=run.error) from exc
