from uuid import UUID
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from travelpilot_infra.db.models.plan import TripPlanModel, TripPlanNodeModel
from travelpilot_infra.db.models.plan_change import PlanChangeModel
from travelpilot_infra.db.models.poi import PoiModel


class PlanRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def next_version(self, trip_id: UUID) -> int:
        value = await self.session.scalar(
            select(func.coalesce(func.max(TripPlanModel.version), 0)).where(TripPlanModel.trip_id == trip_id)
        )
        return int(value or 0) + 1

    async def create(self, plan: TripPlanModel, nodes: list[TripPlanNodeModel]) -> TripPlanModel:
        self.session.add(plan)
        await self.session.flush()
        for node in nodes:
            node.plan_id = plan.id
        self.session.add_all(nodes)
        await self.session.commit()
        await self.session.refresh(plan)
        return plan

    async def list_by_trip(self, trip_id: UUID) -> list[TripPlanModel]:
        result = await self.session.execute(
            select(TripPlanModel).where(TripPlanModel.trip_id == trip_id).order_by(TripPlanModel.version.desc())
        )
        return list(result.scalars())

    async def get_with_nodes(self, trip_id: UUID, plan_id: UUID) -> tuple[TripPlanModel, list[tuple[TripPlanNodeModel, PoiModel]]] | None:
        plan = await self.session.scalar(select(TripPlanModel).where(TripPlanModel.id == plan_id, TripPlanModel.trip_id == trip_id))
        if plan is None:
            return None
        result = await self.session.execute(
            select(TripPlanNodeModel, PoiModel)
            .join(PoiModel, TripPlanNodeModel.poi_id == PoiModel.id)
            .where(TripPlanNodeModel.plan_id == plan_id)
            .order_by(TripPlanNodeModel.day, TripPlanNodeModel.start_time)
        )
        return plan, list(result.all())

    async def get_node(self, plan_id: UUID, node_id: UUID) -> TripPlanNodeModel | None:
        return await self.session.scalar(select(TripPlanNodeModel).where(TripPlanNodeModel.id == node_id, TripPlanNodeModel.plan_id == plan_id))

    async def add_change(self, change: PlanChangeModel) -> PlanChangeModel:
        self.session.add(change)
        await self.session.flush()
        await self.session.refresh(change)
        return change

    async def list_changes(self, plan_id: UUID) -> list[PlanChangeModel]:
        result = await self.session.execute(select(PlanChangeModel).where(PlanChangeModel.plan_id == plan_id).order_by(PlanChangeModel.created_at.asc()))
        return list(result.scalars())
