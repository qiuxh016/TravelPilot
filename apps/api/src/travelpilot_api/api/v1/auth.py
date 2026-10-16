import hashlib
import re
import secrets
from datetime import UTC, datetime, timedelta
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.encoders import jsonable_encoder
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import delete, exists, or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from travelpilot_infra.db.models.advisor import AdvisorMessageModel, AdvisorSessionModel
from travelpilot_infra.db.models.plan import TripPlanModel, TripPlanNodeModel
from travelpilot_infra.db.models.plan_change import PlanChangeModel
from travelpilot_infra.db.models.poi import PoiModel
from travelpilot_infra.db.models.social import (
    FriendshipModel,
    MemorySuggestionModel,
    MomentCommentModel,
    MomentMediaModel,
    MomentReactionModel,
    TravelMomentModel,
    TripShareGrantModel,
)
from travelpilot_infra.db.models.trip import TripModel
from travelpilot_infra.db.models.user import (
    EmailTokenModel,
    LoginAttemptModel,
    UserModel,
    UserSessionModel,
)
from travelpilot_infra.db.models.wishlist import WishlistItemModel
from travelpilot_infra.db.models.workflow import (
    AgentRunModel,
    ContentItemModel,
    ContentSourceModel,
    EntityCandidateModel,
    ExtractedEntityModel,
    MemoryModel,
    ToolCallModel,
    TripEventModel,
    UserFeedbackModel,
    WorkflowRunModel,
)
from travelpilot_infra.db.session import get_session

from travelpilot_api.auth import (
    DUMMY_PASSWORD_HASH,
    clear_session_cookies,
    get_current_user,
    hash_password,
    new_session_token,
    normalize_email,
    set_session_cookies,
    token_digest,
    verify_password,
)
from travelpilot_api.mail import latest_development_email, send_action_email
from travelpilot_api.settings import settings

router = APIRouter(tags=["auth"])


class Credentials(BaseModel):
    email: str = Field(min_length=3, max_length=320)
    password: str = Field(min_length=12, max_length=128)
    invite_code: str | None = None

    @field_validator("email")
    @classmethod
    def validate_email(cls, value: str) -> str:
        normalized = normalize_email(value)
        if not re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", normalized):
            raise ValueError("请输入有效邮箱地址")
        return normalized


class EmailInput(BaseModel):
    email: str = Field(min_length=3, max_length=320)

    @field_validator("email")
    @classmethod
    def validate_email(cls, value: str) -> str:
        normalized = normalize_email(value)
        if not re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", normalized):
            raise ValueError("请输入有效邮箱地址")
        return normalized


class TokenInput(BaseModel):
    token: str = Field(min_length=32, max_length=128)


class PasswordResetInput(TokenInput):
    password: str = Field(min_length=12, max_length=128)


class AuthNotice(BaseModel):
    message: str


class UserRead(BaseModel):
    id: UUID
    email: str
    created_at: datetime
    model_config = ConfigDict(from_attributes=True)


class SessionRead(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_at: datetime
    user: UserRead


async def _issue_session(user: UserModel, session: AsyncSession) -> SessionRead:
    token = new_session_token()
    now = datetime.now(UTC)
    expires_at = now + timedelta(hours=12)
    await session.execute(delete(UserSessionModel).where(UserSessionModel.expires_at <= now))
    session.add(UserSessionModel(user_id=user.id, token_hash=token_digest(token), expires_at=expires_at))
    await session.commit()
    await session.refresh(user)
    return SessionRead(access_token=token, expires_at=expires_at, user=user)


async def _create_email_token(user: UserModel, purpose: str, session: AsyncSession) -> str:
    token = new_session_token()
    now = datetime.now(UTC)
    await session.execute(delete(EmailTokenModel).where(
        EmailTokenModel.user_id == user.id,
        EmailTokenModel.purpose == purpose,
        EmailTokenModel.consumed_at.is_(None),
    ))
    session.add(EmailTokenModel(
        user_id=user.id,
        purpose=purpose,
        token_hash=token_digest(token),
        expires_at=now + timedelta(hours=24 if purpose == "verify_email" else 1),
    ))
    await session.commit()
    return token


async def _send_email_action(user: UserModel, purpose: str, session: AsyncSession) -> None:
    token = await _create_email_token(user, purpose, session)
    route = "/verify-email" if purpose == "verify_email" else "/reset-password"
    subject = "验证 TravelPilot 邮箱" if purpose == "verify_email" else "重置 TravelPilot 密码"
    await send_action_email(user.email, subject, f"{settings.web_base_url.rstrip('/')}{route}?token={token}")


@router.post("/auth/register", response_model=AuthNotice, status_code=202)
async def register(payload: Credentials, session: AsyncSession = Depends(get_session)):
    expected = settings.registration_invite_code
    if expected and not secrets.compare_digest(payload.invite_code or "", expected):
        raise HTTPException(403, detail="邀请码无效")
    user = UserModel(email=payload.email, password_hash=hash_password(payload.password))
    session.add(user)
    try:
        await session.flush()
        await session.commit()
        await _send_email_action(user, "verify_email", session)
    except IntegrityError as exc:
        await session.rollback()
        raise HTTPException(409, detail="邮箱已注册") from exc
    return AuthNotice(message="账户已创建，请检查邮箱并完成验证后登录。")


@router.post("/auth/verify-email", response_model=AuthNotice)
async def verify_email(payload: TokenInput, session: AsyncSession = Depends(get_session)):
    now = datetime.now(UTC)
    consumed = await session.execute(update(EmailTokenModel).where(
        EmailTokenModel.token_hash == token_digest(payload.token),
        EmailTokenModel.purpose == "verify_email",
        EmailTokenModel.consumed_at.is_(None),
        EmailTokenModel.expires_at > now,
    ).values(consumed_at=now).returning(EmailTokenModel.user_id))
    user_id = consumed.scalar_one_or_none()
    if user_id is None:
        raise HTTPException(400, detail="验证链接无效或已过期")
    user = await session.get(UserModel, user_id)
    if user is None or not user.is_active:
        await session.rollback()
        raise HTTPException(400, detail="验证链接无效或已过期")
    user.email_verified = True
    await session.commit()
    return AuthNotice(message="邮箱验证成功，现在可以登录。")


@router.post("/auth/verify-email/resend", response_model=AuthNotice)
async def resend_verification(payload: EmailInput, session: AsyncSession = Depends(get_session)):
    user = await session.scalar(select(UserModel).where(UserModel.email == payload.email))
    if user is not None and user.is_active and not user.email_verified:
        await _send_email_action(user, "verify_email", session)
    return AuthNotice(message="如果该邮箱需要验证，我们已发送验证邮件。")


@router.post("/auth/password-reset/request", response_model=AuthNotice)
async def request_password_reset(payload: EmailInput, session: AsyncSession = Depends(get_session)):
    user = await session.scalar(select(UserModel).where(UserModel.email == payload.email))
    if user is not None and user.is_active:
        await _send_email_action(user, "password_reset", session)
    return AuthNotice(message="如果该邮箱已注册，我们已发送密码重置邮件。")


@router.post("/auth/password-reset/confirm", response_model=AuthNotice)
async def confirm_password_reset(payload: PasswordResetInput, session: AsyncSession = Depends(get_session)):
    now = datetime.now(UTC)
    consumed = await session.execute(update(EmailTokenModel).where(
        EmailTokenModel.token_hash == token_digest(payload.token),
        EmailTokenModel.purpose == "password_reset",
        EmailTokenModel.consumed_at.is_(None),
        EmailTokenModel.expires_at > now,
    ).values(consumed_at=now).returning(EmailTokenModel.user_id))
    user_id = consumed.scalar_one_or_none()
    if user_id is None:
        raise HTTPException(400, detail="重置链接无效或已过期")
    user = await session.get(UserModel, user_id)
    if user is None or not user.is_active:
        await session.rollback()
        raise HTTPException(400, detail="重置链接无效或已过期")
    user.password_hash = hash_password(payload.password)
    await session.execute(delete(UserSessionModel).where(UserSessionModel.user_id == user.id))
    await session.commit()
    return AuthNotice(message="密码已更新，请使用新密码登录。")


@router.get("/auth/dev-mailbox/{email}", response_model=dict, include_in_schema=False)
async def dev_mailbox(email: str, request: Request):
    client_host = request.client.host if request.client else ""
    if settings.app_env.lower() != "development" or client_host not in {"127.0.0.1", "::1", "localhost"}:
        raise HTTPException(404, detail="Not found")
    message = latest_development_email(normalize_email(email))
    return message or {"message": "没有待处理的开发邮件。"}


@router.post("/auth/login", response_model=SessionRead)
async def login(payload: Credentials, request: Request, response: Response, session: AsyncSession = Depends(get_session)):
    now = datetime.now(UTC)
    key_hash = hashlib.sha256(f"{payload.email}\0{request.client.host if request.client else 'unknown'}".encode()).hexdigest()
    attempt = await session.get(LoginAttemptModel, key_hash)
    if attempt and attempt.blocked_until and attempt.blocked_until > now:
        raise HTTPException(429, detail="登录尝试过多，请稍后再试")
    if attempt and attempt.blocked_until and attempt.blocked_until <= now:
        attempt.failed_attempts = 0
        attempt.blocked_until = None
        attempt.window_started_at = now
    user = await session.scalar(select(UserModel).where(UserModel.email == payload.email))
    valid = verify_password(payload.password, user.password_hash if user else DUMMY_PASSWORD_HASH)
    if user is None or not user.is_active or not valid:
        if attempt is None or now - attempt.window_started_at > timedelta(minutes=settings.login_window_minutes):
            attempt = LoginAttemptModel(key_hash=key_hash, failed_attempts=0, window_started_at=now)
            session.add(attempt)
        attempt.failed_attempts += 1
        if attempt.failed_attempts >= settings.login_max_attempts:
            attempt.blocked_until = now + timedelta(minutes=settings.login_lockout_minutes)
        await session.commit()
        if attempt.blocked_until and attempt.blocked_until > now:
            raise HTTPException(429, detail="登录尝试过多，请稍后再试")
        raise HTTPException(401, detail="邮箱或密码错误", headers={"WWW-Authenticate": "Bearer"})
    if not user.email_verified:
        raise HTTPException(403, detail="请先验证邮箱，再登录。")
    if attempt:
        await session.delete(attempt)
    result = await _issue_session(user, session)
    set_session_cookies(response, result.access_token)
    return result


@router.post("/auth/logout", status_code=204)
async def logout(request: Request,
                 response: Response,
                 user: UserModel = Depends(get_current_user),
                 session: AsyncSession = Depends(get_session)):
    del user
    authorization = request.headers.get("authorization", "")
    token = authorization.partition(" ")[2] or request.cookies.get("travelpilot_session", "")
    await session.execute(delete(UserSessionModel).where(
        UserSessionModel.token_hash == token_digest(token)
    ))
    await session.commit()
    clear_session_cookies(response)
    response.status_code = 204
    return response


@router.get("/auth/me", response_model=UserRead)
async def me(user: UserModel = Depends(get_current_user)):
    return user


@router.get("/account/export")
async def export_account(user: UserModel = Depends(get_current_user),
                         session: AsyncSession = Depends(get_session)):
    trips = (await session.scalars(select(TripModel).where(TripModel.owner_id == user.id)
                                   .order_by(TripModel.created_at))).all()
    trip_ids = [trip.id for trip in trips]
    def trip_rows(model):
        return session.scalars(select(model).where(model.trip_id.in_(trip_ids)))

    wishlist = (await trip_rows(WishlistItemModel)).all()
    plans = (await trip_rows(TripPlanModel)).all()
    plan_ids = [item.id for item in plans]
    plan_nodes = (await session.scalars(select(TripPlanNodeModel).where(
        TripPlanNodeModel.plan_id.in_(plan_ids)))).all() if plan_ids else []
    plan_changes = (await session.scalars(select(PlanChangeModel).where(
        PlanChangeModel.plan_id.in_(plan_ids)))).all() if plan_ids else []
    sources = (await trip_rows(ContentSourceModel)).all()
    source_ids = [item.id for item in sources]
    entities = (await session.scalars(select(ExtractedEntityModel).where(
        ExtractedEntityModel.source_id.in_(source_ids)))).all() if source_ids else []
    entity_ids = [item.id for item in entities]
    candidates = (await session.scalars(select(EntityCandidateModel).where(
        EntityCandidateModel.entity_id.in_(entity_ids)))).all() if entity_ids else []
    moments = (await session.scalars(select(TravelMomentModel).where(
        TravelMomentModel.owner_user_id == user.id
    ))).all()
    moment_ids = [moment.id for moment in moments]
    agent_runs = (await trip_rows(AgentRunModel)).all()
    agent_ids = [item.id for item in agent_runs]
    tool_calls = (await session.scalars(select(ToolCallModel).where(
        ToolCallModel.run_id.in_(agent_ids)))).all() if agent_ids else []
    grouped = {
        "wishlist": wishlist, "plans": plans, "plan_nodes": plan_nodes,
        "plan_changes": plan_changes, "content_sources": sources,
        "content_items": (await session.scalars(select(ContentItemModel).where(
            ContentItemModel.source_id.in_(source_ids)))).all() if source_ids else [],
        "extracted_entities": entities, "entity_candidates": candidates,
        "events": (await trip_rows(TripEventModel)).all(),
        "feedback": (await trip_rows(UserFeedbackModel)).all(),
        "memories": (await trip_rows(MemoryModel)).all(),
        "workflow_runs": (await trip_rows(WorkflowRunModel)).all(),
        "agent_runs": agent_runs, "tool_calls": tool_calls,
        "friendships": (await session.scalars(select(FriendshipModel).where(
            or_(FriendshipModel.user_low_id == user.id, FriendshipModel.user_high_id == user.id)
        ))).all(),
        "trip_shares": (await session.scalars(select(TripShareGrantModel).where(
            or_(TripShareGrantModel.owner_user_id == user.id, TripShareGrantModel.grantee_user_id == user.id)
        ))).all(),
        "travel_moments": moments,
        "moment_media": (await session.scalars(select(MomentMediaModel).where(
            MomentMediaModel.owner_user_id == user.id
        ))).all(),
        "moment_comments": (await session.scalars(select(MomentCommentModel).where(
            or_(MomentCommentModel.author_user_id == user.id,
                MomentCommentModel.moment_id.in_(moment_ids) if moment_ids else False)
        ))).all(),
        "moment_reactions": (await session.scalars(select(MomentReactionModel).where(
            MomentReactionModel.user_id == user.id
        ))).all(),
        "memory_suggestions": (await session.scalars(select(MemorySuggestionModel).where(
            MemorySuggestionModel.owner_user_id == user.id
        ))).all(),
    }
    advisor_sessions = (await session.scalars(select(AdvisorSessionModel).where(
        AdvisorSessionModel.owner_id == user.id
    ).order_by(AdvisorSessionModel.created_at))).all()
    advisor_session_ids = [item.id for item in advisor_sessions]
    grouped["advisor_messages"] = (await session.scalars(select(AdvisorMessageModel).where(
        AdvisorMessageModel.owner_id == user.id,
        AdvisorMessageModel.session_id.in_(advisor_session_ids) if advisor_session_ids else False,
    ).order_by(AdvisorMessageModel.created_at))).all()
    poi_ids = {item.poi_id for item in wishlist}
    poi_ids.update(item.poi_id for item in plan_nodes)
    pois = (await session.scalars(select(PoiModel).where(PoiModel.id.in_(poi_ids)))).all() if poi_ids else []
    grouped["pois"] = pois

    def encode(model):
        return jsonable_encoder({column.name: getattr(model, column.name)
                                 for column in model.__table__.columns})

    return {
        "format": "travelpilot-account-export-v1",
        "exported_at": datetime.now(UTC),
        "user": {"email": user.email, "created_at": user.created_at},
        "trips": [encode(trip) for trip in trips],
        "data": {name: [encode(row) for row in rows] for name, rows in grouped.items()},
        "advisor_sessions": [encode(item) for item in advisor_sessions],
    }


@router.delete("/account", status_code=204)
async def delete_account(response: Response, user: UserModel = Depends(get_current_user),
                         session: AsyncSession = Depends(get_session)):
    media_keys = (await session.execute(select(MomentMediaModel.object_key, MomentMediaModel.thumbnail_key)
                                        .where(MomentMediaModel.owner_user_id == user.id))).all()
    await session.execute(delete(UserModel).where(UserModel.id == user.id))
    # User-entered POIs have no provider and are not shared; remove only those
    # that became unreferenced after the account's Trips cascaded away.
    still_used = (
        exists(select(WishlistItemModel.id).where(WishlistItemModel.poi_id == PoiModel.id))
        | exists(select(TripPlanNodeModel.id).where(TripPlanNodeModel.poi_id == PoiModel.id))
    )
    await session.execute(delete(PoiModel).where(PoiModel.provider.is_(None), ~still_used))
    await session.commit()
    from travelpilot_api.media_storage import remove_media_keys
    remove_media_keys([key for pair in media_keys for key in pair])
    clear_session_cookies(response)
    response.status_code = 204
    return response
