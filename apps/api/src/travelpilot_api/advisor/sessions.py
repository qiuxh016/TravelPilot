"""Owner-scoped Advisor session reads."""

from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import case, select
from sqlalchemy.ext.asyncio import AsyncSession
from travelpilot_infra.db.models.advisor import AdvisorMessageModel, AdvisorSessionModel

from travelpilot_api.advisor.interpretation import _active_personal_memories
from travelpilot_api.advisor.policies import annotate_profile_memory_conflicts
from travelpilot_api.advisor.task_state import restore_task_snapshot
from travelpilot_api.schemas.advisor import AdvisorSessionRead


async def owned_session(
    session: AsyncSession, session_id: UUID, owner_id: UUID, *, lock: bool = False,
) -> AdvisorSessionModel:
    statement = select(AdvisorSessionModel).where(
        AdvisorSessionModel.id == session_id,
        AdvisorSessionModel.owner_id == owner_id,
    )
    if lock:
        # The session primary key is immutable. PostgreSQL's NO KEY UPDATE lock
        # still serializes concurrent session writers but is compatible with the
        # KEY SHARE lock used by foreign-key inserts into advisor_run_events.
        statement = statement.with_for_update(key_share=True)
    row = await session.scalar(statement)
    if row is None:
        raise HTTPException(404, detail="顾问会话不存在")
    return row


async def read_session(
    session: AsyncSession,
    row: AdvisorSessionModel,
    *,
    profile_memories: list[dict] | None = None,
) -> AdvisorSessionRead:
    messages = (await session.scalars(select(AdvisorMessageModel).where(
        AdvisorMessageModel.session_id == row.id,
        AdvisorMessageModel.owner_id == row.owner_id,
    ).order_by(
        AdvisorMessageModel.created_at,
        case((AdvisorMessageModel.role == "user", 0), else_=1),
        AdvisorMessageModel.id,
    ))).all()
    context = dict(row.context or {
        "revision": 0, "selected_candidate_ids": [], "current_plan": None,
        "confirmed_memories": [],
    })
    last_task = restore_task_snapshot(context.get("last_agent_task"))
    if last_task is None:
        context.pop("last_agent_task", None)
    else:
        context["last_agent_task"] = last_task.model_dump(mode="json")
    context.pop("agent_route_evidence", None)
    if profile_memories is None:
        profile_memories = await _active_personal_memories(session, row.owner_id)
    candidates = annotate_profile_memory_conflicts(row.candidates or [], profile_memories)
    draft_stale = bool(
        row.draft
        and row.draft.get("context_revision", context.get("revision", 0))
        != context.get("revision", 0)
    )
    return AdvisorSessionRead.model_validate({
        **{column.name: getattr(row, column.name) for column in row.__table__.columns},
        "context": context,
        "candidates": candidates,
        "draft_stale": draft_stale,
        "messages": messages,
    })
