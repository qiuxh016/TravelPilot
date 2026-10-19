from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from travelpilot_infra.db.models.plan import TripPlanModel, TripPlanNodeModel
from travelpilot_infra.db.models.plan_change import PlanChangeModel
from travelpilot_infra.db.models.poi import PoiModel
from travelpilot_infra.db.models.trip import TripModel
from travelpilot_infra.db.models.wishlist import WishlistItemModel
from travelpilot_infra.db.repositories.plans import PlanRepository

from travelpilot_core.engines.constraints.validator import validate_visits
from travelpilot_core.engines.optimizer.itinerary import Candidate, build_itinerary


class PlanService:
    def __init__(self, session: AsyncSession):
        self.session = session
        self.repository = PlanRepository(session)

    async def generate(self, trip_id: UUID, maps=None, constraints: dict | None = None,
                       *, commit: bool = True) -> TripPlanModel:
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

        candidates = [Candidate(
            poi_id=poi.id, name=poi.name, latitude=float(poi.latitude),
            longitude=float(poi.longitude), duration_min=poi.suggested_duration_min,
            priority=item.priority, interest_score=float(item.interest_score or 0),
            opening_hours=poi.opening_hours, note=item.notes,
        ) for item, poi in items]
        matrix = None
        if maps is not None and len(candidates) > 1:
            matrix = await maps.get_distance_matrix(
                [(candidate.latitude, candidate.longitude) for candidate in candidates]
            )
        baseline = {1: 3, 2: 4, 3: 5, 4: 6, 5: 6}.get(trip.pace_level, 5)
        options = {"max_pois_per_day": baseline, "buffer_min": 90 if trip.pace_level <= 2 else 60}
        options.update(constraints or {})
        visits, warnings = build_itinerary(trip.start_date, trip.end_date, candidates, matrix, **options)
        warnings.extend(validate_visits(visits))
        version = await self.repository.next_version(trip_id)
        plan = TripPlanModel(trip_id=trip_id, version=version, status="READY", warnings=warnings)
        nodes = [TripPlanNodeModel(
            poi_id=visit.candidate.poi_id, day=visit.day,
            start_time=visit.start_time, end_time=visit.end_time,
            priority=visit.candidate.priority, status="PLANNED", note=visit.candidate.note,
        ) for visit in visits]
        return await self.repository.create(plan, nodes, commit=commit)

    async def list(self, trip_id: UUID):
        return await self.repository.list_by_trip(trip_id)

    async def get(self, trip_id: UUID, plan_id: UUID):
        return await self.repository.get_with_nodes(trip_id, plan_id)

    async def _revise_node(self, plan_id: UUID, node_id: UUID,
                           changes: dict | None, reason: str | None):
        source = await self.session.get(TripPlanModel, plan_id)
        if source is None:
            return None
        latest = await self.repository.next_version(source.trip_id) - 1
        if source.version != latest:
            raise ValueError("PLAN_NOT_LATEST")
        rows = (await self.repository.get_with_nodes(source.trip_id, plan_id))[1]
        if not any(node.id == node_id for node, _ in rows):
            return None
        if changes is not None and not changes:
            raise ValueError("NO_CHANGES")
        trip = await self.session.get(TripModel, source.trip_id)
        if changes is not None:
            if changes.get("day") is not None and not trip.start_date <= changes["day"] <= trip.end_date:
                raise ValueError("DAY_OUTSIDE_TRIP")
            if any(changes.get(key) is None for key in changes):
                raise ValueError("NULL_PLAN_FIELD")
        revised = TripPlanModel(trip_id=source.trip_id, version=latest + 1,
                                status=source.status, warnings=list(source.warnings))
        self.session.add(revised)
        await self.session.flush()
        original = None
        updated = None
        for node, _ in rows:
            if node.id == node_id:
                original = node
                if changes is None:
                    continue
            clone = TripPlanNodeModel(
                plan_id=revised.id, poi_id=node.poi_id, day=node.day,
                start_time=node.start_time, end_time=node.end_time,
                priority=node.priority, status=node.status, note=node.note,
            )
            if node.id == node_id:
                for field, value in changes.items():
                    setattr(clone, field, value)
                if clone.end_time <= clone.start_time:
                    raise ValueError("INVALID_NODE_TIME")
                updated = clone
            self.session.add(clone)
        before = {
            "stable_key": str(original.poi_id), "day": str(original.day),
            "start_time": str(original.start_time), "end_time": str(original.end_time),
            "priority": original.priority, "status": original.status, "note": original.note,
        }
        after = None if updated is None else {
            "stable_key": str(updated.poi_id), "day": str(updated.day),
            "start_time": str(updated.start_time), "end_time": str(updated.end_time),
            "priority": updated.priority, "status": updated.status, "note": updated.note,
        }
        self.session.add(PlanChangeModel(
            plan_id=revised.id, change_type="REMOVE" if changes is None else "UPDATE",
            stable_key=str(original.poi_id), reason_code="USER_EDIT",
            change_cost=3 if changes is None else 1,
            before_data=before, after_data=after, reason=reason or "USER_EDIT",
        ))
        await self.session.commit()
        await self.session.refresh(revised)
        return revised

    async def update_node(self, plan_id: UUID, node_id: UUID,
                          changes: dict, reason: str | None):
        return await self._revise_node(plan_id, node_id, changes, reason)

    async def delete_node(self, plan_id: UUID, node_id: UUID, reason: str | None):
        return await self._revise_node(plan_id, node_id, None, reason)

    async def remove_poi_from_latest(self, trip_id: UUID, poi_id: UUID) -> bool:
        """Create a latest-plan revision without nodes for a removed wishlist POI."""
        next_version = await self.repository.next_version(trip_id)
        plans = await self.repository.list_by_trip(trip_id)
        if not plans:
            return False
        source = plans[0]
        _, rows = await self.repository.get_with_nodes(trip_id, source.id)
        removed = [node for node, _ in rows if node.poi_id == poi_id]
        if not removed:
            return False

        revised = TripPlanModel(
            trip_id=trip_id,
            version=next_version,
            status=source.status,
            warnings=list(source.warnings),
        )
        self.session.add(revised)
        await self.session.flush()
        for node, _ in rows:
            if node.poi_id == poi_id:
                before = {
                    "stable_key": str(node.poi_id), "day": str(node.day),
                    "start_time": str(node.start_time), "end_time": str(node.end_time),
                    "priority": node.priority, "status": node.status, "note": node.note,
                }
                self.session.add(PlanChangeModel(
                    plan_id=revised.id, change_type="REMOVE", stable_key=str(node.poi_id),
                    reason_code="USER_EDIT", change_cost=3, before_data=before,
                    after_data=None, reason="REMOVED_FROM_WISHLIST",
                ))
                continue
            self.session.add(TripPlanNodeModel(
                plan_id=revised.id, poi_id=node.poi_id, day=node.day,
                start_time=node.start_time, end_time=node.end_time,
                priority=node.priority, status=node.status, note=node.note,
            ))
        await self.session.flush()
        return True

    async def changes(self, plan_id: UUID):
        return await self.repository.list_changes(plan_id)
