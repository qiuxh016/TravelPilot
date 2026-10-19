"""Advisor message API and turn orchestration."""

import asyncio
import hashlib
import re
from datetime import UTC, datetime, timedelta
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException
from sqlalchemy import select, tuple_
from sqlalchemy.ext.asyncio import AsyncSession
from travelpilot_infra.db.models.advisor import (
    AdvisorMessageModel,
    AdvisorRunEventModel,
)
from travelpilot_infra.db.models.user import UserModel
from travelpilot_infra.db.session import AsyncSessionFactory, get_session

from travelpilot_api.advisor.checkpoints import checkpoint_writer
from travelpilot_api.advisor.sessions import owned_session, read_session
from travelpilot_api.auth import get_current_user
from travelpilot_api.dependencies import get_maps_provider, get_weather_provider
from travelpilot_api.schemas.advisor import (
    AdvisorMessageCreate,
    AdvisorMessageFeedbackCreate,
    AdvisorMessageFeedbackRead,
    AdvisorProgressEventRead,
    AdvisorSessionRead,
)

messages_router = APIRouter(tags=["advisor"])
_TURN_HEARTBEAT_INTERVAL_SECONDS = 15
_TURN_INTERRUPTION_AFTER = timedelta(seconds=60)


def _latest_attempt_events(rows: list[AdvisorRunEventModel]) -> list[AdvisorRunEventModel]:
    """Discard stale progress from earlier safe restarts of the same task phase."""
    latest_by_phase: dict[str, str | None] = {}
    for row in rows:
        metadata = row.metadata_json if isinstance(row.metadata_json, dict) else {}
        latest_by_phase[row.phase] = metadata.get("attempt_ref")
    selected = []
    for row in rows:
        metadata = row.metadata_json if isinstance(row.metadata_json, dict) else {}
        if metadata.get("attempt_ref") == latest_by_phase[row.phase]:
            selected.append(row)
    return selected[-40:]


def _mark_interrupted_turn(events: list[dict], *, now: datetime | None = None) -> list[dict]:
    """Expose an abandoned running turn after its independent heartbeat goes stale."""
    now = now or datetime.now(UTC)
    for event in reversed(events):
        if event.get("phase") != "turn":
            continue
        if event.get("status") not in {"TURN_STARTED", "TURN_HEARTBEAT"}:
            return events
        updated_at = event.get("created_at")
        if not isinstance(updated_at, datetime):
            return events
        if updated_at.tzinfo is None:
            updated_at = updated_at.replace(tzinfo=UTC)
        if now - updated_at > _TURN_INTERRUPTION_AFTER:
            event["status"] = "TURN_INTERRUPTED"
            metadata = event.get("metadata") if isinstance(event.get("metadata"), dict) else {}
            event["metadata"] = {
                **metadata,
                "state": "TURN_INTERRUPTED",
                "recovery": "RETRY_SAME_MESSAGE_SAFELY",
            }
        return events
    return events


async def _keep_turn_heartbeat(write, interval: float = _TURN_HEARTBEAT_INTERVAL_SECONDS) -> None:
    while True:
        await asyncio.sleep(interval)
        await write({"state": "TURN_HEARTBEAT"})


async def _persist_idempotent_user_message(
    *, session_id: UUID, owner_id: UUID, idempotency_key: str, content: str,
) -> None:
    """Commit the retry source before work that may outlive a process.

    The caller holds the session's ``NO KEY UPDATE`` row lock. This serializes
    same-session message requests while remaining compatible with the
    foreign-key key-share lock required by this independent insert.
    """
    async with AsyncSessionFactory() as message_db:
        message_db.add(AdvisorMessageModel(
            session_id=session_id,
            owner_id=owner_id,
            role="user",
            content=content,
            idempotency_key=idempotency_key,
        ))
        await message_db.commit()


@messages_router.get(
    "/sessions/{session_id}/progress", response_model=list[AdvisorProgressEventRead],
)
async def get_message_progress(
    session_id: UUID,
    db: AsyncSession = Depends(get_session),
    user: UserModel = Depends(get_current_user),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key", max_length=80),
):
    await owned_session(db, session_id, user.id)
    if not idempotency_key or not re.fullmatch(r"[A-Za-z0-9._:-]{8,80}", idempotency_key):
        raise HTTPException(422, detail="Idempotency-Key 格式无效")
    task_ref = hashlib.sha256(idempotency_key.encode("utf-8")).hexdigest()[:24]
    newest_rows = (await db.scalars(
        select(AdvisorRunEventModel).where(
            AdvisorRunEventModel.owner_id == user.id,
            AdvisorRunEventModel.session_id == session_id,
            AdvisorRunEventModel.operation.in_((
                "advisor_agent_loop", "stage_order_selected",
            )),
            AdvisorRunEventModel.metadata_json["task_ref"].as_string() == task_ref,
        ).order_by(
            AdvisorRunEventModel.created_at.desc(), AdvisorRunEventModel.id.desc(),
        ).limit(200)
    )).all()
    rows = list(reversed(newest_rows))
    events = [{
        "id": row.id, "phase": row.phase, "operation": row.operation,
        "status": row.status, "metadata": row.metadata_json, "created_at": row.created_at,
    } for row in _latest_attempt_events(rows)]
    return _mark_interrupted_turn(events)


@messages_router.put(
    "/sessions/{session_id}/messages/{message_id}/feedback",
    response_model=AdvisorMessageFeedbackRead,
)
async def submit_message_feedback(
    session_id: UUID,
    message_id: UUID,
    payload: AdvisorMessageFeedbackCreate,
    db: AsyncSession = Depends(get_session),
    user: UserModel = Depends(get_current_user),
):
    """Store the owner's latest usefulness rating on one assistant reply."""
    await owned_session(db, session_id, user.id)
    message = await db.scalar(select(AdvisorMessageModel).where(
        AdvisorMessageModel.id == message_id,
        AdvisorMessageModel.session_id == session_id,
        AdvisorMessageModel.owner_id == user.id,
        AdvisorMessageModel.role == "assistant",
    ))
    if message is None:
        raise HTTPException(404, detail="顾问回复不存在")
    submitted_at = datetime.now(UTC)
    feedback = {
        "rating": payload.rating,
        "reason": payload.reason,
        "submitted_at": submitted_at.isoformat(),
    }
    message.payload = {**(message.payload or {}), "advisor_feedback": feedback}
    await db.commit()
    return AdvisorMessageFeedbackRead(
        message_id=message.id,
        rating=payload.rating,
        reason=payload.reason,
        submitted_at=submitted_at,
    )


@messages_router.post("/sessions/{session_id}/messages", response_model=AdvisorSessionRead)
async def send_message(
    session_id: UUID,
    payload: AdvisorMessageCreate,
    db: AsyncSession = Depends(get_session),
    user: UserModel = Depends(get_current_user),
    maps=Depends(get_maps_provider),
    weather=Depends(get_weather_provider),
    idempotency_key: str = Header(..., alias="Idempotency-Key", max_length=80),
):
    row = await owned_session(db, session_id, user.id, lock=True)
    content = payload.content.strip()
    if not re.fullmatch(r"[A-Za-z0-9._:-]{8,80}", idempotency_key):
        raise HTTPException(422, detail="Idempotency-Key 格式无效")
    previous = await db.scalar(
        select(AdvisorMessageModel).where(
            AdvisorMessageModel.session_id == row.id,
            AdvisorMessageModel.owner_id == user.id,
            AdvisorMessageModel.role == "user",
            AdvisorMessageModel.idempotency_key == idempotency_key,
        )
    )
    if previous is not None:
        if previous.content != content:
            raise HTTPException(409, detail="Idempotency-Key 已用于另一条消息")
        exact_reply = await db.scalar(
            select(AdvisorMessageModel.id).where(
                AdvisorMessageModel.session_id == row.id,
                AdvisorMessageModel.owner_id == user.id,
                AdvisorMessageModel.role == "assistant",
                AdvisorMessageModel.payload["response_to_idempotency_key"].as_string()
                == idempotency_key,
            ).limit(1)
        )
        if exact_reply is not None:
            completed_checkpoint = checkpoint_writer(
                owner_id=user.id, session_id=row.id, task_id=idempotency_key, phase="turn",
            )
            await completed_checkpoint({"state": "TURN_SUCCEEDED"})
            return await read_session(db, row)
        next_message = await db.scalar(
            select(AdvisorMessageModel).where(
                AdvisorMessageModel.session_id == row.id,
                AdvisorMessageModel.owner_id == user.id,
                tuple_(AdvisorMessageModel.created_at, AdvisorMessageModel.id)
                > tuple_(previous.created_at, previous.id),
            ).order_by(
                AdvisorMessageModel.created_at.asc(), AdvisorMessageModel.id.asc(),
            ).limit(1)
        )
        if next_message is not None and next_message.role == "assistant":
            # Older assistant replies predate the explicit response link.
            response_key = (
                next_message.payload.get("response_to_idempotency_key")
                if isinstance(next_message.payload, dict) else None
            )
            if response_key in {None, idempotency_key}:
                completed_checkpoint = checkpoint_writer(
                    owner_id=user.id, session_id=row.id, task_id=idempotency_key, phase="turn",
                )
                await completed_checkpoint({"state": "TURN_SUCCEEDED"})
                return await read_session(db, row)
            raise HTTPException(
                409,
                detail="这条顾问请求尚未完成，但会话已有其他回复；请基于最新会话重新提交咨询。",
            )
        if next_message is not None and next_message.role == "user":
            raise HTTPException(
                409,
                detail="这条顾问请求还没有回复，但会话已有后续消息；请基于最新会话重新提交咨询。",
            )
        resume_existing_user_message = True
    else:
        resume_existing_user_message = False
    if row.status != "ACTIVE":
        raise HTTPException(409, detail="已确认的会话不能继续修改")
    if not resume_existing_user_message:
        # Keep the original request recoverable if the API process exits while
        # a model or provider call is in flight. The retry path detects this
        # row and will not insert the user's message twice.
        await _persist_idempotent_user_message(
            session_id=row.id,
            owner_id=user.id,
            idempotency_key=idempotency_key,
            content=content,
        )
        resume_existing_user_message = True
    from travelpilot_api.advisor.turn_service import execute_advisor_turn
    task_id = idempotency_key or f"{row.id}:{content}"
    write_checkpoint = checkpoint_writer(
        owner_id=user.id, session_id=row.id, task_id=task_id, phase="turn",
    )
    await write_checkpoint({"state": "TURN_STARTED"})
    heartbeat = asyncio.create_task(_keep_turn_heartbeat(write_checkpoint))
    try:
        result = await execute_advisor_turn(
            db, row, content, maps, weather, idempotency_key, user,
            resume_existing_user_message=resume_existing_user_message,
        )
    except Exception:
        await write_checkpoint({"state": "TURN_FAILED"})
        raise
    else:
        await write_checkpoint({"state": "TURN_SUCCEEDED"})
        return result
    finally:
        heartbeat.cancel()
        await asyncio.gather(heartbeat, return_exceptions=True)
