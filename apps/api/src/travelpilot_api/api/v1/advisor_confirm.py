"""HTTP endpoint for confirming an Advisor draft."""

from uuid import UUID

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession
from travelpilot_infra.db.models.user import UserModel
from travelpilot_infra.db.session import get_session

from travelpilot_api.advisor.confirm_service import confirm_advisor_draft
from travelpilot_api.advisor.sessions import owned_session
from travelpilot_api.auth import get_current_user
from travelpilot_api.schemas.advisor import AdvisorConfirm, AdvisorConfirmRead

confirm_router = APIRouter(tags=["advisor"])


@confirm_router.post("/sessions/{session_id}/confirm", response_model=AdvisorConfirmRead)
async def confirm_draft(
    session_id: UUID,
    payload: AdvisorConfirm,
    db: AsyncSession = Depends(get_session),
    user: UserModel = Depends(get_current_user),
):
    row = await owned_session(db, session_id, user.id, lock=True)
    return await confirm_advisor_draft(db, row, payload, user)
