from datetime import datetime, time, timedelta
from uuid import UUID
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from travelpilot_infra.db.models.poi import PoiModel
from travelpilot_infra.db.models.trip import TripModel
from travelpilot_infra.db.models.wishlist import WishlistItemModel
from travelpilot_infra.db.models.plan import TripPlanModel, TripPlanNodeModel
from travelpilot_infra.db.models.plan_change import PlanChangeModel
from travelpilot_infra.db.repositories.plans import PlanRepository


class PlanService:
    def __init__(self, session: AsyncSession):
        self.session = session
        self.repository = PlanRepository(session)

    async def generate(self, trip_id: UUID) -> TripPlanModel:
        trip = await self.session.get(TripModel, trip_id)
        if trip is None:
            raise LookupError("Trip not found")
        result = await self.session.execute(
            select(WishlistItemModel, PoiModel)
            .join(PoiModel, WishlistItemModel.poi_id == PoiModel.id)
            .where(WishlistItemModel.trip_id == trip_id)
            .order_by(WishlistItemModel.priority.asc(), WishlistItemModel.created_at.asc())
        )
        items = list(result.all())
        if not items:
            raise ValueError("WISHLIST_EMPTY")

        version = await self.repository.next_version(trip_id)
        plan = TripPlanModel(trip_id=trip_id, version=version, status="READY", warnings=[])
        nodes = []
        warnings = []
        days = (trip.end_date - trip.start_date).days + 1
        for index, (item, poi) in enumerate(items):
            day = trip.start_date + timedelta(days=index % days)
            start = datetime.combine(day, time(9)) + timedelta(minutes=(index // days) * 30)
            end = start + timedelta(minutes=poi.suggested_duration_min)
            if end.time() > time(20):
                warnings.append(f"{day} 无法容纳：{poi.name}")
                continue
            hours = poi.opening_hours or {}
            if hours.get("open") and start.time() < time.fromisoformat(hours["open"]):
                start = datetime.combine(day, time.fromisoformat(hours["open"]))
                end = start + timedelta(minutes=poi.suggested_duration_min)
            if hours.get("close") and end.time() > time.fromisoformat(hours["close"]):
                warnings.append(f"{poi.name} 超出营业时间")
                continue
            nodes.append(TripPlanNodeModel(
                poi_id=poi.id, day=day, start_time=start.time(), end_time=end.time(),
                priority=item.priority, status="PLANNED", note=item.notes,
            ))
        plan.warnings = warnings
        return await self.repository.create(plan, nodes)

    async def list(self, trip_id: UUID):
        return await self.repository.list_by_trip(trip_id)

    async def get(self, trip_id: UUID, plan_id: UUID):
        return await self.repository.get_with_nodes(trip_id, plan_id)

    async def update_node(self, plan_id: UUID, node_id: UUID, changes: dict, reason: str | None):
        node = await self.repository.get_node(plan_id, node_id)
        if node is None:
            return None
        before = {field: getattr(node, field) for field in changes}
        for field, value in changes.items():
            setattr(node, field, value)
        after = {field: getattr(node, field) for field in changes}
        await self.repository.add_change(PlanChangeModel(
            plan_id=plan_id, node_id=node_id, change_type="UPDATE",
            before_data={key: str(value) for key, value in before.items()},
            after_data={key: str(value) for key, value in after.items()},
            reason=reason,
        ))
        await self.session.commit()
        return node

    async def delete_node(self, plan_id: UUID, node_id: UUID, reason: str | None):
        node = await self.repository.get_node(plan_id, node_id)
        if node is None:
            return False
        before = {"day": str(node.day), "start_time": str(node.start_time), "end_time": str(node.end_time), "poi_id": str(node.poi_id)}
        await self.repository.add_change(PlanChangeModel(plan_id=plan_id, node_id=node_id, change_type="DELETE", before_data=before, reason=reason))
        await self.session.delete(node)
        await self.session.commit()
        return True

    async def changes(self, plan_id: UUID):
        return await self.repository.list_changes(plan_id)
