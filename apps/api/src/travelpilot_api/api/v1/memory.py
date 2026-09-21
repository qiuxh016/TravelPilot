from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from travelpilot_infra.db.models.trip import TripModel
from travelpilot_infra.db.models.workflow import MemoryModel, UserFeedbackModel
from travelpilot_infra.db.session import get_session

from travelpilot_api.schemas.memory import FeedbackCreate, FeedbackRead, MemoryRead, MemoryUpdate

router = APIRouter(tags=["memory"])


def _memory_type(text: str) -> str:
    if any(word in text for word in ("喜欢", "偏好", "不喜欢", "轻松", "很累", "疲劳")):
        return "SEMANTIC_PREFERENCE"
    return "EPISODIC"


@router.post("/trips/{trip_id}/feedback", response_model=FeedbackRead, status_code=201)
async def create_feedback(trip_id: UUID, payload: FeedbackCreate,
                          session: AsyncSession = Depends(get_session)):
    if await session.get(TripModel, trip_id) is None:
        raise HTTPException(404, detail="Trip not found")
    feedback = UserFeedbackModel(trip_id=trip_id, text=payload.text.strip(), rating=payload.rating)
    session.add(feedback)
    await session.flush()
    normalized = " ".join(payload.text.strip().split())
    previous = await session.scalar(select(MemoryModel).where(
        MemoryModel.trip_id == trip_id, MemoryModel.text == normalized))
    candidate = None
    if previous is None and len(normalized) >= 4:
        candidate = MemoryModel(trip_id=trip_id, feedback_id=feedback.id,
                                memory_type=_memory_type(normalized), text=normalized,
                                importance=0.7 if payload.rating and payload.rating <= 2 else 0.5,
                                confidence=0.6, confirmed=False)
        session.add(candidate)
    await session.commit()
    await session.refresh(feedback)
    if candidate is not None:
        await session.refresh(candidate)
    return FeedbackRead(id=feedback.id, trip_id=trip_id, text=feedback.text,
                        rating=feedback.rating, created_at=feedback.created_at,
                        candidate_memory=MemoryRead.model_validate(candidate) if candidate else None)


@router.get("/trips/{trip_id}/memories", response_model=list[MemoryRead])
async def list_memories(trip_id: UUID, session: AsyncSession = Depends(get_session)):
    if await session.get(TripModel, trip_id) is None:
        raise HTTPException(404, detail="Trip not found")
    return (await session.scalars(select(MemoryModel).where(
        MemoryModel.trip_id == trip_id).order_by(MemoryModel.created_at.desc()))).all()


@router.patch("/trips/{trip_id}/memories/{memory_id}", response_model=MemoryRead)
async def update_memory(trip_id: UUID, memory_id: UUID, payload: MemoryUpdate,
                        session: AsyncSession = Depends(get_session)):
    memory = await session.get(MemoryModel, memory_id)
    if memory is None or memory.trip_id != trip_id:
        raise HTTPException(404, detail="Memory not found")
    if payload.text is not None:
        memory.text = payload.text.strip()
    if payload.confirmed is not None:
        memory.confirmed = payload.confirmed
    await session.commit()
    await session.refresh(memory)
    return memory


@router.delete("/trips/{trip_id}/memories/{memory_id}", status_code=204)
async def delete_memory(trip_id: UUID, memory_id: UUID,
                        session: AsyncSession = Depends(get_session)):
    memory = await session.get(MemoryModel, memory_id)
    if memory is None or memory.trip_id != trip_id:
        raise HTTPException(404, detail="Memory not found")
    await session.delete(memory)
    await session.commit()
