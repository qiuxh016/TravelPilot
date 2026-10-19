from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from travelpilot_infra.db.models.trip import TripModel
from travelpilot_infra.db.models.user import UserModel
from travelpilot_infra.db.models.workflow import (
    MemoryModel,
    UserFeedbackModel,
    UserMemorySettingsModel,
    UserPreferenceMemoryModel,
)
from travelpilot_infra.db.session import get_session

from travelpilot_api.auth import get_current_user
from travelpilot_api.schemas.memory import (
    FeedbackCreate,
    FeedbackRead,
    MemoryRead,
    MemoryUpdate,
    UserMemorySettingsRead,
    UserMemorySettingsUpdate,
    UserPreferenceMemoryCreate,
    UserPreferenceMemoryRead,
    UserPreferenceMemorySuggestionConfirm,
    UserPreferenceMemoryUpdate,
)

router = APIRouter(tags=["memory"])


@router.get("/account/memory-settings", response_model=UserMemorySettingsRead)
async def get_user_memory_settings(session: AsyncSession = Depends(get_session),
                                  user: UserModel = Depends(get_current_user)):
    row = await session.get(UserMemorySettingsModel, user.id)
    return UserMemorySettingsRead(enabled=row.enabled if row else False)


@router.put("/account/memory-settings", response_model=UserMemorySettingsRead)
async def update_user_memory_settings(payload: UserMemorySettingsUpdate,
                                      session: AsyncSession = Depends(get_session),
                                      user: UserModel = Depends(get_current_user)):
    row = await session.get(UserMemorySettingsModel, user.id)
    if row is None:
        row = UserMemorySettingsModel(owner_id=user.id, enabled=payload.enabled)
        session.add(row)
    else:
        row.enabled = payload.enabled
    await session.commit()
    return UserMemorySettingsRead(enabled=row.enabled)


@router.get("/account/memories", response_model=list[UserPreferenceMemoryRead])
async def list_user_memories(session: AsyncSession = Depends(get_session),
                             user: UserModel = Depends(get_current_user)):
    return (await session.scalars(select(UserPreferenceMemoryModel).where(
        UserPreferenceMemoryModel.owner_id == user.id
    ).order_by(UserPreferenceMemoryModel.created_at.desc()))).all()


@router.post("/account/memories", response_model=UserPreferenceMemoryRead, status_code=201)
async def create_user_memory(payload: UserPreferenceMemoryCreate,
                             session: AsyncSession = Depends(get_session),
                             user: UserModel = Depends(get_current_user)):
    row = UserPreferenceMemoryModel(
        owner_id=user.id, category=payload.category.strip().upper(), text=payload.text.strip(),
        constraint_type=payload.constraint_type, source="USER_EXPLICIT", confidence=1.0,
        confirmed=True, expires_at=payload.expires_at,
    )
    session.add(row)
    await session.commit()
    await session.refresh(row)
    return row


@router.post("/account/memories/confirm-suggestion", response_model=UserPreferenceMemoryRead, status_code=201)
async def confirm_user_memory_suggestion(
    payload: UserPreferenceMemorySuggestionConfirm,
    session: AsyncSession = Depends(get_session),
    user: UserModel = Depends(get_current_user),
):
    settings_row = await session.get(UserMemorySettingsModel, user.id)
    if settings_row is None or not settings_row.enabled:
        raise HTTPException(409, detail="请先在账户与隐私中开启偏好记忆")
    text = " ".join(payload.text.strip().split())
    if len(text) < 2:
        raise HTTPException(422, detail="记忆内容不能为空")
    existing = await session.scalar(select(UserPreferenceMemoryModel).where(
        UserPreferenceMemoryModel.owner_id == user.id,
        UserPreferenceMemoryModel.category == payload.category,
        UserPreferenceMemoryModel.text == text,
        UserPreferenceMemoryModel.constraint_type == payload.constraint_type,
        UserPreferenceMemoryModel.source == "MODEL_SUGGESTED",
        UserPreferenceMemoryModel.confirmed.is_(True),
    ))
    if existing is not None:
        return existing
    row = UserPreferenceMemoryModel(
        owner_id=user.id,
        category=payload.category,
        text=text,
        constraint_type=payload.constraint_type,
        source="MODEL_SUGGESTED",
        confidence=0.8,
        confirmed=True,
    )
    session.add(row)
    await session.commit()
    await session.refresh(row)
    return row


@router.patch("/account/memories/{memory_id}", response_model=UserPreferenceMemoryRead)
async def update_user_memory(memory_id: UUID, payload: UserPreferenceMemoryUpdate,
                             session: AsyncSession = Depends(get_session),
                             user: UserModel = Depends(get_current_user)):
    row = await session.scalar(select(UserPreferenceMemoryModel).where(
        UserPreferenceMemoryModel.id == memory_id,
        UserPreferenceMemoryModel.owner_id == user.id,
    ))
    if row is None:
        raise HTTPException(404, detail="Memory not found")
    for field in ("category", "text", "constraint_type", "expires_at"):
        if field not in payload.model_fields_set:
            continue
        value = getattr(payload, field)
        if value is not None or field == "expires_at":
            setattr(row, field, value.strip().upper() if field == "category" else value.strip() if field == "text" else value)
    await session.commit()
    await session.refresh(row)
    return row


@router.delete("/account/memories/{memory_id}", status_code=204)
async def delete_user_memory(memory_id: UUID, session: AsyncSession = Depends(get_session),
                             user: UserModel = Depends(get_current_user)):
    row = await session.scalar(select(UserPreferenceMemoryModel).where(
        UserPreferenceMemoryModel.id == memory_id,
        UserPreferenceMemoryModel.owner_id == user.id,
    ))
    if row is None:
        raise HTTPException(404, detail="Memory not found")
    await session.delete(row)
    await session.commit()


def _memory_type(text: str) -> str:
    if any(word in text for word in ("喜欢", "偏好", "不喜欢", "轻松", "很累", "疲劳")):
        return "SEMANTIC_PREFERENCE"
    return "EPISODIC"


@router.post("/trips/{trip_id}/feedback", response_model=FeedbackRead, status_code=201)
async def create_feedback(trip_id: UUID, payload: FeedbackCreate,
                          session: AsyncSession = Depends(get_session),
                          user: UserModel = Depends(get_current_user)):
    if await session.scalar(select(TripModel.id).where(
        TripModel.id == trip_id, TripModel.owner_id == user.id
    )) is None:
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
async def list_memories(trip_id: UUID, session: AsyncSession = Depends(get_session),
                        user: UserModel = Depends(get_current_user)):
    if await session.scalar(select(TripModel.id).where(
        TripModel.id == trip_id, TripModel.owner_id == user.id
    )) is None:
        raise HTTPException(404, detail="Trip not found")
    return (await session.scalars(select(MemoryModel).where(
        MemoryModel.trip_id == trip_id).order_by(MemoryModel.created_at.desc()))).all()


@router.patch("/trips/{trip_id}/memories/{memory_id}", response_model=MemoryRead)
async def update_memory(trip_id: UUID, memory_id: UUID, payload: MemoryUpdate,
                        session: AsyncSession = Depends(get_session),
                        user: UserModel = Depends(get_current_user)):
    memory = await session.scalar(select(MemoryModel).join(
        TripModel, TripModel.id == MemoryModel.trip_id
    ).where(MemoryModel.id == memory_id, MemoryModel.trip_id == trip_id,
            TripModel.owner_id == user.id))
    if memory is None:
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
                        session: AsyncSession = Depends(get_session),
                        user: UserModel = Depends(get_current_user)):
    memory = await session.scalar(select(MemoryModel).join(
        TripModel, TripModel.id == MemoryModel.trip_id
    ).where(MemoryModel.id == memory_id, MemoryModel.trip_id == trip_id,
            TripModel.owner_id == user.id))
    if memory is None:
        raise HTTPException(404, detail="Memory not found")
    await session.delete(memory)
    await session.commit()
