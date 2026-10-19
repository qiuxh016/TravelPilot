"""Database persistence helpers for Advisor turn boundaries."""

from collections.abc import Callable
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from travelpilot_infra.db.models.advisor import AdvisorMessageModel, AdvisorSessionModel
from travelpilot_infra.db.models.user import UserModel

from travelpilot_api.advisor.budgets import aggregate_turn_token_usage
from travelpilot_api.advisor.session_budget import record_session_usage
from travelpilot_api.advisor.sessions import read_session
from travelpilot_api.schemas.advisor import AdvisorSessionRead


async def save_budget_control_message(
    db: AsyncSession,
    row: AdvisorSessionModel,
    user: UserModel,
    context: dict,
    submitted_content: str,
    reply: str,
    payload: dict,
    *,
    idempotency_key: str | None = None,
    resume_existing_user_message: bool = False,
    session_reader: Callable | None = None,
) -> AdvisorSessionRead:
    """Persist an explicit budget prompt/cancel response without executing tools."""
    if not resume_existing_user_message:
        db.add(AdvisorMessageModel(
            session_id=row.id, owner_id=user.id, role="user", content=submitted_content,
            idempotency_key=idempotency_key,
        ))
    if idempotency_key:
        payload = {**payload, "response_to_idempotency_key": idempotency_key}
    db.add(AdvisorMessageModel(
        session_id=row.id, owner_id=user.id, role="assistant", content=reply, payload=payload,
    ))
    row.context = context
    row.updated_at = datetime.now(UTC)
    await db.commit()
    await db.refresh(row)
    return await (session_reader or read_session)(db, row)


def turn_usage_snapshot(
    extraction, reply_tokens: int, reply_output_tokens: int,
    search_runtime, schedule_runtime, route_runtime,
) -> dict[str, int]:
    """Aggregate whatever usage is available, including before a failed turn."""
    return aggregate_turn_token_usage(
        getattr(extraction, "token_usage", 0),
        getattr(extraction, "output_tokens", 0),
        reply_tokens,
        reply_output_tokens,
        search_runtime,
        schedule_runtime,
        route_runtime,
    )


async def persist_failed_turn_usage(
    db: AsyncSession, *, session_id, owner_id, extraction,
    reply_tokens: int, reply_output_tokens: int,
    search_runtime, schedule_runtime, route_runtime,
) -> None:
    """Keep observed model usage when a later tool failure rolls back the turn."""
    usage = turn_usage_snapshot(
        extraction, reply_tokens, reply_output_tokens,
        search_runtime, schedule_runtime, route_runtime,
    )
    if not usage["total_tokens"]:
        return
    locked_row = await db.scalar(
        select(AdvisorSessionModel)
        .where(
            AdvisorSessionModel.id == session_id,
            AdvisorSessionModel.owner_id == owner_id,
        )
        .with_for_update()
    )
    if locked_row is None:
        return
    context = dict(locked_row.context or {})
    record_session_usage(context, usage["input_tokens"], usage["output_tokens"])
    locked_row.context = context
    await db.commit()
