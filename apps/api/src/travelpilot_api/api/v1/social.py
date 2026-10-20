import asyncio
import base64
import json
import logging
from datetime import UTC, datetime
from uuid import UUID, uuid4

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from sqlalchemy import and_, func, or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from travelpilot_core.application.trip_activity import record_trip_activity
from travelpilot_infra.db.models.plan import TripPlanModel, TripPlanNodeModel
from travelpilot_infra.db.models.poi import PoiModel
from travelpilot_infra.db.models.social import (
    FriendConversationModel,
    FriendMessageModel,
    FriendshipModel,
    TripShareGrantModel,
)
from travelpilot_infra.db.models.trip import TripModel
from travelpilot_infra.db.models.trip_member import TripMemberModel
from travelpilot_infra.db.models.user import UserModel
from travelpilot_infra.db.session import get_session

from travelpilot_api.auth import get_current_user, normalize_email
from travelpilot_api.media_storage import (
    normalize_image,
    read_media,
    remove_media_keys,
    write_image,
)
from travelpilot_api.schemas.social import (
    FriendAnalysisConsentUpdate,
    FriendAnalysisRead,
    FriendConversationRead,
    FriendMessageCreate,
    FriendMessageRead,
    FriendRequestCreate,
    FriendRequestNotice,
    FriendSearchResult,
    FriendshipRead,
    SharedTripDetailRead,
    SharedTripPlanNodeRead,
    SharedTripRead,
    TripShareCreate,
    TripShareRead,
)
from travelpilot_api.settings import settings

router = APIRouter(tags=["friends and trip sharing"])
logger = logging.getLogger(__name__)


def _email_hint(email: str) -> str:
    local, _, domain = email.partition("@")
    return f"{local[:1]}***@{domain}" if domain else ""


def _pair(left: UUID, right: UUID) -> tuple[UUID, UUID]:
    return (left, right) if left.int < right.int else (right, left)


async def _accepted_friendship(session: AsyncSession, user_id: UUID, friend_id: UUID):
    low, high = _pair(user_id, friend_id)
    return await session.scalar(select(FriendshipModel).where(
        FriendshipModel.user_low_id == low,
        FriendshipModel.user_high_id == high,
        FriendshipModel.status == "ACCEPTED",
    ))


def _friend_read(row: FriendshipModel, actor_id: UUID, other: UserModel) -> FriendshipRead:
    direction = "OUTGOING" if row.status == "PENDING" and row.requested_by_id == actor_id else (
        "INCOMING" if row.status == "PENDING" else "NONE"
    )
    return FriendshipRead(
        id=row.id, friend_user_id=other.id, email=other.email, display_name=other.display_name, status=row.status,
        direction=direction, created_at=row.created_at,
    )


@router.post("/friends/requests", response_model=FriendRequestNotice, status_code=202)
async def request_friend(
    payload: FriendRequestCreate,
    session: AsyncSession = Depends(get_session),
    user: UserModel = Depends(get_current_user),
):
    email = normalize_email(payload.email)
    other = await session.scalar(select(UserModel).where(
        UserModel.email == email, UserModel.is_active.is_(True), UserModel.email_verified.is_(True)
    ))
    message = "如果该邮箱对应可添加的账户，我们已处理好友申请。"
    if other is None or other.id == user.id:
        return FriendRequestNotice(message=message)
    low, high = _pair(user.id, other.id)
    friendship = await session.scalar(select(FriendshipModel).where(
        FriendshipModel.user_low_id == low, FriendshipModel.user_high_id == high
    ))
    if friendship is None:
        friendship = FriendshipModel(
            user_low_id=low, user_high_id=high, requested_by_id=user.id, status="PENDING"
        )
        session.add(friendship)
        try:
            await session.commit()
        except IntegrityError:
            await session.rollback()
    elif friendship.status == "REJECTED":
        friendship.status = "PENDING"
        friendship.requested_by_id = user.id
        friendship.blocked_by_id = None
        friendship.updated_at = datetime.now(UTC)
        await session.commit()
    return FriendRequestNotice(message=message)


@router.get("/friends/search", response_model=list[FriendSearchResult])
async def search_friends(
    q: str = Query(min_length=2, max_length=320),
    session: AsyncSession = Depends(get_session),
    user: UserModel = Depends(get_current_user),
):
    query = q.strip()
    if len(query) < 2:
        return []
    statement = select(UserModel).where(
        UserModel.is_active.is_(True),
        UserModel.email_verified.is_(True),
    )
    if "@" in query:
        statement = statement.where(func.lower(UserModel.email) == normalize_email(query))
    else:
        normalized_query = query.lower()
        statement = statement.where(or_(
            func.lower(UserModel.username).startswith(normalized_query, autoescape=True),
            func.lower(UserModel.display_name).startswith(normalized_query, autoescape=True),
        ))
    users = (await session.scalars(statement.order_by(UserModel.username).limit(10))).all()
    return [FriendSearchResult(
        user_id=target.id,
        username=target.username,
        display_name=target.display_name,
        email_hint=_email_hint(target.email),
        has_avatar=bool(target.avatar_thumbnail_key),
        is_self=target.id == user.id,
    ) for target in users]


@router.get("/friends/users/{target_user_id}/avatar")
async def read_friend_avatar(
    target_user_id: UUID,
    session: AsyncSession = Depends(get_session),
    user: UserModel = Depends(get_current_user),
):
    target = await session.scalar(select(UserModel).where(
        UserModel.id == target_user_id,
        UserModel.is_active.is_(True),
        UserModel.email_verified.is_(True),
    ))
    if target is None or not target.avatar_thumbnail_key or not target.avatar_mime_type:
        raise HTTPException(404, detail="用户头像不可用")
    try:
        content = await asyncio.to_thread(read_media, target.avatar_thumbnail_key)
    except Exception as exc:
        raise HTTPException(503, detail="头像暂时无法读取") from exc
    return Response(content, media_type=target.avatar_mime_type, headers={
        "Cache-Control": "private, no-store", "X-Content-Type-Options": "nosniff",
    })


@router.post("/friends/requests/users/{target_user_id}", response_model=FriendRequestNotice, status_code=202)
async def request_friend_by_user(
    target_user_id: UUID,
    session: AsyncSession = Depends(get_session),
    user: UserModel = Depends(get_current_user),
):
    target = await session.scalar(select(UserModel).where(
        UserModel.id == target_user_id,
        UserModel.is_active.is_(True),
        UserModel.email_verified.is_(True),
    ))
    message = "如果该用户可添加，我们已处理好友申请。"
    if target is None or target.id == user.id:
        return FriendRequestNotice(message=message)
    low, high = _pair(user.id, target.id)
    friendship = await session.scalar(select(FriendshipModel).where(
        FriendshipModel.user_low_id == low, FriendshipModel.user_high_id == high
    ))
    if friendship is None:
        session.add(FriendshipModel(
            user_low_id=low, user_high_id=high, requested_by_id=user.id, status="PENDING"
        ))
        try:
            await session.commit()
        except IntegrityError:
            await session.rollback()
    elif friendship.status == "REJECTED":
        friendship.status = "PENDING"
        friendship.requested_by_id = user.id
        friendship.blocked_by_id = None
        friendship.updated_at = datetime.now(UTC)
        await session.commit()
    return FriendRequestNotice(message=message)


async def _owned_conversation(session: AsyncSession, conversation_id: UUID, user_id: UUID) -> FriendConversationModel:
    row = await session.scalar(select(FriendConversationModel).where(
        FriendConversationModel.id == conversation_id,
        or_(FriendConversationModel.user_low_id == user_id, FriendConversationModel.user_high_id == user_id),
    ))
    if row is None or await _accepted_friendship(session, user_id, row.user_high_id if row.user_low_id == user_id else row.user_low_id) is None:
        raise HTTPException(404, detail="Resource not found")
    return row


def _friend_message_read(row: FriendMessageModel) -> FriendMessageRead:
    return FriendMessageRead(
        id=row.id, sender_user_id=row.sender_user_id, body=row.body,
        has_media=bool(row.media_key), mime_type=row.mime_type, created_at=row.created_at,
    )


@router.post("/friends/conversations/{friend_user_id}", response_model=FriendConversationRead)
async def get_or_create_friend_conversation(
    friend_user_id: UUID,
    session: AsyncSession = Depends(get_session),
    user: UserModel = Depends(get_current_user),
):
    if friend_user_id == user.id or await _accepted_friendship(session, user.id, friend_user_id) is None:
        raise HTTPException(404, detail="Resource not found")
    low, high = _pair(user.id, friend_user_id)
    row = await session.scalar(select(FriendConversationModel).where(
        FriendConversationModel.user_low_id == low, FriendConversationModel.user_high_id == high
    ))
    if row is None:
        row = FriendConversationModel(user_low_id=low, user_high_id=high)
        session.add(row)
        try:
            await session.commit()
        except IntegrityError:
            await session.rollback()
            row = await session.scalar(select(FriendConversationModel).where(
                FriendConversationModel.user_low_id == low, FriendConversationModel.user_high_id == high
            ))
    friend = await session.get(UserModel, friend_user_id)
    if row is None or friend is None:
        raise HTTPException(404, detail="Resource not found")
    return FriendConversationRead(
        id=row.id, friend_user_id=friend.id, username=friend.username, display_name=friend.display_name,
        self_analysis_consent=(row.user_low_analysis_consent if user.id == low else row.user_high_analysis_consent),
        friend_analysis_consent=(row.user_high_analysis_consent if user.id == low else row.user_low_analysis_consent),
    )


@router.put("/friends/conversations/{conversation_id}/analysis-consent", response_model=FriendConversationRead)
async def update_friend_analysis_consent(
    conversation_id: UUID,
    payload: FriendAnalysisConsentUpdate,
    session: AsyncSession = Depends(get_session),
    user: UserModel = Depends(get_current_user),
):
    conversation = await _owned_conversation(session, conversation_id, user.id)
    if user.id == conversation.user_low_id:
        conversation.user_low_analysis_consent = payload.enabled
        friend_id = conversation.user_high_id
    else:
        conversation.user_high_analysis_consent = payload.enabled
        friend_id = conversation.user_low_id
    await session.commit()
    friend = await session.get(UserModel, friend_id)
    return FriendConversationRead(
        id=conversation.id, friend_user_id=friend_id, username=friend.username, display_name=friend.display_name,
        self_analysis_consent=payload.enabled,
        friend_analysis_consent=(conversation.user_high_analysis_consent if user.id == conversation.user_low_id else conversation.user_low_analysis_consent),
    )


@router.post("/friends/conversations/{conversation_id}/analyze", response_model=FriendAnalysisRead)
async def analyze_friend_conversation(
    conversation_id: UUID,
    include_images: bool = Query(default=False),
    session: AsyncSession = Depends(get_session),
    user: UserModel = Depends(get_current_user),
):
    conversation = await _owned_conversation(session, conversation_id, user.id)
    if not (conversation.user_low_analysis_consent and conversation.user_high_analysis_consent):
        raise HTTPException(403, detail="双方都同意后才能分析私聊内容")
    if not settings.llm_api_key or not settings.llm_model:
        raise HTTPException(503, detail="尚未配置可用的模型")
    rows = list(reversed((await session.scalars(select(FriendMessageModel).where(
        FriendMessageModel.conversation_id == conversation_id
    ).order_by(FriendMessageModel.created_at.desc()).limit(10))).all()))
    if not rows:
        raise HTTPException(422, detail="当前没有可分析的聊天内容")
    prompt = (
        "根据以下好友私聊内容，给当前用户一份克制、非诊断性的交流摘要。只基于明确证据，不推断人格或心理健康，"
        "不虚构旅行偏好。若信息不足请如实说明。只返回 JSON："
        '{"summary":"...","observed_tone":"...","travel_preferences":["..."],"suggested_reply":"...","evidence":["原文短句"]}。\n'
        "聊天记录（含表情符号）：\n" + "\n".join(f"{('我' if row.sender_user_id == user.id else '好友')}: {row.body}" for row in rows)
    )
    content: list[dict] = [{"type": "text", "text": prompt}]
    if include_images:
        images = [row for row in rows if row.media_key and row.mime_type]
        if len(images) > 4:
            images = images[-4:]
        total = 0
        for row in images:
            try:
                image_bytes = await asyncio.to_thread(read_media, row.thumbnail_key or row.media_key)
            except Exception as exc:
                raise HTTPException(503, detail="聊天图片暂时无法读取") from exc
            total += len(image_bytes)
            if total > 4 * 1024 * 1024:
                raise HTTPException(413, detail="本次分析图片总量超出限制")
            encoded = base64.b64encode(image_bytes).decode("ascii")
            content.append({"type": "image_url", "image_url": {"url": f"data:{row.mime_type};base64,{encoded}"}})
    try:
        async with httpx.AsyncClient(timeout=25) as client:
            response = await client.post(
                f"{settings.llm_base_url.rstrip('/')}/chat/completions",
                headers={"Authorization": f"Bearer {settings.llm_api_key}"},
                json={"model": settings.llm_model, "messages": [{"role": "user", "content": content}],
                      "temperature": 0.2, "max_tokens": 500, "response_format": {"type": "json_object"}},
            )
        response.raise_for_status()
        result = json.loads(response.json()["choices"][0]["message"]["content"])
        return FriendAnalysisRead(
            summary=str(result.get("summary", "信息不足"))[:1200],
            observed_tone=str(result.get("observed_tone", "未明确"))[:400],
            travel_preferences=[str(item)[:200] for item in result.get("travel_preferences", [])[:8] if isinstance(item, str)],
            suggested_reply=str(result.get("suggested_reply", ""))[:1000],
            evidence=[str(item)[:300] for item in result.get("evidence", [])[:8] if isinstance(item, str)],
            includes_images=include_images,
        )
    except (httpx.HTTPError, KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        logger.warning("friend_chat_analysis_failed user_id=%s error_type=%s", user.id, type(exc).__name__)
        raise HTTPException(503, detail="私聊分析暂时不可用；聊天内容仍保留在本地服务中") from exc


@router.get("/friends/conversations/{conversation_id}/messages", response_model=list[FriendMessageRead])
async def list_friend_messages(
    conversation_id: UUID,
    session: AsyncSession = Depends(get_session),
    user: UserModel = Depends(get_current_user),
):
    await _owned_conversation(session, conversation_id, user.id)
    rows = (await session.scalars(select(FriendMessageModel).where(
        FriendMessageModel.conversation_id == conversation_id
    ).order_by(FriendMessageModel.created_at.desc()).limit(100))).all()
    return [_friend_message_read(row) for row in reversed(rows)]


@router.post("/friends/conversations/{conversation_id}/messages", response_model=FriendMessageRead, status_code=201)
async def send_friend_message(
    conversation_id: UUID,
    payload: FriendMessageCreate,
    session: AsyncSession = Depends(get_session),
    user: UserModel = Depends(get_current_user),
):
    conversation = await _owned_conversation(session, conversation_id, user.id)
    row = FriendMessageModel(conversation_id=conversation.id, sender_user_id=user.id, body=payload.body.strip())
    if not row.body:
        raise HTTPException(422, detail="消息不能为空")
    session.add(row)
    conversation.updated_at = datetime.now(UTC)
    await session.commit()
    await session.refresh(row)
    return _friend_message_read(row)


@router.post("/friends/conversations/{conversation_id}/messages/image", response_model=FriendMessageRead, status_code=201)
async def send_friend_image(
    conversation_id: UUID,
    request: Request,
    text: str = Query(default="", max_length=4000),
    session: AsyncSession = Depends(get_session),
    user: UserModel = Depends(get_current_user),
):
    conversation = await _owned_conversation(session, conversation_id, user.id)
    content_type = request.headers.get("content-type", "").split(";", 1)[0].strip().lower()
    if content_type not in {"image/jpeg", "image/png", "image/webp"}:
        raise HTTPException(415, detail="仅支持 JPEG、PNG 或 WebP 图片")
    declared_length = request.headers.get("content-length")
    if declared_length and declared_length.isdigit() and int(declared_length) > settings.media_max_upload_bytes:
        raise HTTPException(413, detail="图片超出单张大小限制")
    chunks: list[bytes] = []
    total = 0
    async for chunk in request.stream():
        total += len(chunk)
        if total > settings.media_max_upload_bytes:
            raise HTTPException(413, detail="图片超出单张大小限制")
        chunks.append(chunk)
    if not chunks:
        raise HTTPException(400, detail="图片内容为空")
    try:
        original, thumbnail, extension, mime_type = await asyncio.to_thread(normalize_image, b"".join(chunks), content_type)
    except ValueError as exc:
        raise HTTPException(400, detail=str(exc)) from exc
    message_id = uuid4()
    try:
        media_key, thumbnail_key = await asyncio.to_thread(write_image, user.id, message_id, extension, original, thumbnail)
    except OSError as exc:
        raise HTTPException(503, detail="图片保存失败，请稍后重试") from exc
    row = FriendMessageModel(
        id=message_id, conversation_id=conversation.id, sender_user_id=user.id, body=text.strip(),
        media_key=media_key, thumbnail_key=thumbnail_key, mime_type=mime_type,
        size_bytes=len(original),
    )
    session.add(row)
    conversation.updated_at = datetime.now(UTC)
    try:
        await session.commit()
    except Exception:
        await session.rollback()
        remove_media_keys([media_key, thumbnail_key])
        raise
    await session.refresh(row)
    return _friend_message_read(row)


@router.get("/friends/conversations/{conversation_id}/messages/{message_id}/image")
async def read_friend_message_image(
    conversation_id: UUID,
    message_id: UUID,
    session: AsyncSession = Depends(get_session),
    user: UserModel = Depends(get_current_user),
):
    await _owned_conversation(session, conversation_id, user.id)
    row = await session.scalar(select(FriendMessageModel).where(
        FriendMessageModel.id == message_id, FriendMessageModel.conversation_id == conversation_id
    ))
    if row is None or not row.media_key or not row.mime_type:
        raise HTTPException(404, detail="Resource not found")
    try:
        content = await asyncio.to_thread(read_media, row.thumbnail_key or row.media_key)
    except Exception as exc:
        raise HTTPException(503, detail="图片暂时无法读取") from exc
    return Response(content, media_type=row.mime_type, headers={
        "Cache-Control": "private, no-store", "X-Content-Type-Options": "nosniff",
    })


@router.get("/friends", response_model=list[FriendshipRead])
async def list_friends(
    session: AsyncSession = Depends(get_session),
    user: UserModel = Depends(get_current_user),
):
    rows = (await session.execute(
        select(FriendshipModel, UserModel)
        .join(UserModel, or_(
            and_(FriendshipModel.user_low_id == user.id, UserModel.id == FriendshipModel.user_high_id),
            and_(FriendshipModel.user_high_id == user.id, UserModel.id == FriendshipModel.user_low_id),
        ))
        .where(or_(FriendshipModel.user_low_id == user.id, FriendshipModel.user_high_id == user.id),
               FriendshipModel.status == "ACCEPTED")
        .order_by(UserModel.email)
    )).all()
    return [_friend_read(row, user.id, other) for row, other in rows]


@router.get("/friends/requests", response_model=list[FriendshipRead])
async def list_friend_requests(
    session: AsyncSession = Depends(get_session),
    user: UserModel = Depends(get_current_user),
):
    rows = (await session.execute(
        select(FriendshipModel, UserModel)
        .join(UserModel, or_(
            and_(FriendshipModel.user_low_id == user.id, UserModel.id == FriendshipModel.user_high_id),
            and_(FriendshipModel.user_high_id == user.id, UserModel.id == FriendshipModel.user_low_id),
        ))
        .where(or_(FriendshipModel.user_low_id == user.id, FriendshipModel.user_high_id == user.id),
               FriendshipModel.status == "PENDING")
        .order_by(FriendshipModel.created_at.desc())
    )).all()
    return [_friend_read(row, user.id, other) for row, other in rows]


@router.get("/friends/blocked", response_model=list[FriendshipRead])
async def list_blocked_friends(
    session: AsyncSession = Depends(get_session),
    user: UserModel = Depends(get_current_user),
):
    rows = (await session.execute(
        select(FriendshipModel, UserModel)
        .join(UserModel, or_(
            and_(FriendshipModel.user_low_id == user.id, UserModel.id == FriendshipModel.user_high_id),
            and_(FriendshipModel.user_high_id == user.id, UserModel.id == FriendshipModel.user_low_id),
        ))
        .where(or_(FriendshipModel.user_low_id == user.id, FriendshipModel.user_high_id == user.id),
               FriendshipModel.status == "BLOCKED", FriendshipModel.blocked_by_id == user.id)
        .order_by(UserModel.email)
    )).all()
    return [_friend_read(row, user.id, other) for row, other in rows]


@router.post("/friends/requests/{friendship_id}/accept", response_model=FriendshipRead)
async def accept_friend_request(
    friendship_id: UUID,
    session: AsyncSession = Depends(get_session),
    user: UserModel = Depends(get_current_user),
):
    row = await session.scalar(select(FriendshipModel).where(
        FriendshipModel.id == friendship_id, FriendshipModel.status == "PENDING",
        FriendshipModel.requested_by_id != user.id,
        or_(FriendshipModel.user_low_id == user.id, FriendshipModel.user_high_id == user.id),
    ))
    if row is None:
        raise HTTPException(404, detail="Resource not found")
    row.status = "ACCEPTED"
    row.updated_at = datetime.now(UTC)
    other_id = row.user_high_id if row.user_low_id == user.id else row.user_low_id
    other = await session.get(UserModel, other_id)
    await session.commit()
    return _friend_read(row, user.id, other)


@router.delete("/friends/requests/{friendship_id}", status_code=204)
async def decline_friend_request(
    friendship_id: UUID,
    session: AsyncSession = Depends(get_session),
    user: UserModel = Depends(get_current_user),
):
    row = await session.scalar(select(FriendshipModel).where(
        FriendshipModel.id == friendship_id, FriendshipModel.status == "PENDING",
        FriendshipModel.requested_by_id != user.id,
        or_(FriendshipModel.user_low_id == user.id, FriendshipModel.user_high_id == user.id),
    ))
    if row is None:
        raise HTTPException(404, detail="Resource not found")
    await session.delete(row)
    await session.commit()


async def _end_friendship(friendship_id: UUID, user: UserModel, session: AsyncSession, block: bool):
    conditions = [
        FriendshipModel.id == friendship_id,
        or_(FriendshipModel.user_low_id == user.id, FriendshipModel.user_high_id == user.id),
        FriendshipModel.status.in_(["PENDING", "ACCEPTED"] if block else ["ACCEPTED"]),
    ]
    row = await session.scalar(select(FriendshipModel).where(*conditions))
    if row is None:
        raise HTTPException(404, detail="Resource not found")
    other_id = row.user_high_id if row.user_low_id == user.id else row.user_low_id
    if block:
        row.status = "BLOCKED"
        row.blocked_by_id = user.id
        row.updated_at = datetime.now(UTC)
    else:
        await session.delete(row)
    await session.execute(update(TripShareGrantModel).where(
        or_(
            and_(TripShareGrantModel.owner_user_id == user.id, TripShareGrantModel.grantee_user_id == other_id),
            and_(TripShareGrantModel.owner_user_id == other_id, TripShareGrantModel.grantee_user_id == user.id),
        ),
        TripShareGrantModel.revoked_at.is_(None),
    ).values(revoked_at=datetime.now(UTC)))
    changed_at = datetime.now(UTC)
    active_memberships = (await session.execute(
        select(TripMemberModel, TripModel.id)
        .join(TripModel, TripModel.id == TripMemberModel.trip_id)
        .where(
            TripMemberModel.status.in_(("PENDING", "ACCEPTED")),
            or_(
                and_(TripModel.owner_id == user.id, TripMemberModel.user_id == other_id),
                and_(TripModel.owner_id == other_id, TripMemberModel.user_id == user.id),
            ),
        )
    )).all()
    for member, trip_id in active_memberships:
        record_trip_activity(
            session, trip_id=trip_id, actor_user_id=user.id,
            actor_display_name=user.display_name or user.username,
            event_type="MEMBERSHIP_REVOKED_FRIENDSHIP",
            summary="好友关系结束，相关旅行访问权限已撤销",
            payload={"membership_id": str(member.id)},
        )
    await session.execute(update(TripMemberModel).where(
        TripMemberModel.status.in_(("PENDING", "ACCEPTED")),
        or_(
            and_(
                TripMemberModel.trip_id.in_(select(TripModel.id).where(TripModel.owner_id == user.id)),
                TripMemberModel.user_id == other_id,
            ),
            and_(
                TripMemberModel.trip_id.in_(select(TripModel.id).where(TripModel.owner_id == other_id)),
                TripMemberModel.user_id == user.id,
            ),
        ),
    ).values(status="REVOKED", updated_at=changed_at))
    await session.commit()


@router.delete("/friends/{friendship_id}", status_code=204)
async def remove_friend(friendship_id: UUID, session: AsyncSession = Depends(get_session), user: UserModel = Depends(get_current_user)):
    await _end_friendship(friendship_id, user, session, block=False)


@router.post("/friends/{friendship_id}/block", status_code=204)
async def block_friend(friendship_id: UUID, session: AsyncSession = Depends(get_session), user: UserModel = Depends(get_current_user)):
    await _end_friendship(friendship_id, user, session, block=True)


@router.delete("/friends/{friendship_id}/block", status_code=204)
async def unblock_friend(friendship_id: UUID, session: AsyncSession = Depends(get_session), user: UserModel = Depends(get_current_user)):
    row = await session.scalar(select(FriendshipModel).where(
        FriendshipModel.id == friendship_id, FriendshipModel.status == "BLOCKED",
        FriendshipModel.blocked_by_id == user.id,
    ))
    if row is None:
        raise HTTPException(404, detail="Resource not found")
    await session.delete(row)
    await session.commit()


@router.get("/trips/{trip_id}/shares", response_model=list[TripShareRead])
async def list_trip_shares(
    trip_id: UUID,
    session: AsyncSession = Depends(get_session),
    user: UserModel = Depends(get_current_user),
):
    trip = await session.scalar(select(TripModel).where(TripModel.id == trip_id, TripModel.owner_id == user.id))
    if trip is None:
        raise HTTPException(404, detail="Resource not found")
    rows = (await session.execute(
        select(TripShareGrantModel, UserModel).join(UserModel, UserModel.id == TripShareGrantModel.grantee_user_id)
        .where(TripShareGrantModel.trip_id == trip_id, TripShareGrantModel.revoked_at.is_(None))
        .order_by(TripShareGrantModel.created_at.desc())
    )).all()
    return [TripShareRead(
        id=grant.id, trip_id=grant.trip_id, grantee_user_id=grant.grantee_user_id,
        email=friend.email, scope=grant.scope, created_at=grant.created_at,
    ) for grant, friend in rows]


@router.post("/trips/{trip_id}/shares", response_model=TripShareRead, status_code=201)
async def create_trip_share(
    trip_id: UUID,
    payload: TripShareCreate,
    session: AsyncSession = Depends(get_session),
    user: UserModel = Depends(get_current_user),
):
    if payload.scope not in {"TRIP_SUMMARY", "TRIP_AND_PLAN"} or payload.friend_user_id == user.id:
        raise HTTPException(422, detail="Invalid share settings")
    trip = await session.scalar(select(TripModel).where(TripModel.id == trip_id, TripModel.owner_id == user.id))
    if trip is None or await _accepted_friendship(session, user.id, payload.friend_user_id) is None:
        raise HTTPException(404, detail="Resource not found")
    friend = await session.get(UserModel, payload.friend_user_id)
    if friend is None or not friend.is_active:
        raise HTTPException(404, detail="Resource not found")
    member = await session.scalar(select(TripMemberModel.id).where(
        TripMemberModel.trip_id == trip_id,
        TripMemberModel.user_id == friend.id,
        TripMemberModel.status.in_(("PENDING", "ACCEPTED")),
    ))
    if member is not None:
        raise HTTPException(status_code=409, detail="Trip members use collaborative access, not read-only sharing")
    grant = await session.scalar(select(TripShareGrantModel).where(
        TripShareGrantModel.trip_id == trip_id,
        TripShareGrantModel.grantee_user_id == friend.id,
    ))
    if grant is None:
        grant = TripShareGrantModel(
            trip_id=trip_id, owner_user_id=user.id, grantee_user_id=friend.id, scope=payload.scope
        )
        session.add(grant)
    else:
        grant.owner_user_id = user.id
        grant.scope = payload.scope
        grant.revoked_at = None
    await session.commit()
    await session.refresh(grant)
    return TripShareRead(
        id=grant.id, trip_id=trip_id, grantee_user_id=friend.id, email=friend.email,
        scope=grant.scope, created_at=grant.created_at,
    )


@router.delete("/trips/{trip_id}/shares/{grant_id}", status_code=204)
async def revoke_trip_share(
    trip_id: UUID,
    grant_id: UUID,
    session: AsyncSession = Depends(get_session),
    user: UserModel = Depends(get_current_user),
):
    grant = await session.scalar(select(TripShareGrantModel).join(
        TripModel, TripModel.id == TripShareGrantModel.trip_id
    ).where(
        TripShareGrantModel.id == grant_id,
        TripShareGrantModel.trip_id == trip_id,
        TripShareGrantModel.revoked_at.is_(None),
        TripModel.owner_id == user.id,
    ))
    if grant is None:
        raise HTTPException(404, detail="Resource not found")
    grant.revoked_at = datetime.now(UTC)
    await session.commit()


@router.get("/shared/trips", response_model=list[SharedTripRead])
async def list_shared_trips(
    session: AsyncSession = Depends(get_session),
    user: UserModel = Depends(get_current_user),
):
    rows = (await session.execute(
        select(TripModel, TripShareGrantModel, UserModel)
        .join(TripShareGrantModel, TripShareGrantModel.trip_id == TripModel.id)
        .join(UserModel, UserModel.id == TripModel.owner_id)
        .join(FriendshipModel, and_(
            FriendshipModel.user_low_id == func.least(user.id, TripModel.owner_id),
            FriendshipModel.user_high_id == func.greatest(user.id, TripModel.owner_id),
            FriendshipModel.status == "ACCEPTED",
        ))
        .where(TripShareGrantModel.grantee_user_id == user.id,
               TripShareGrantModel.owner_user_id == TripModel.owner_id,
               TripShareGrantModel.revoked_at.is_(None))
        .order_by(TripModel.start_date)
    )).all()
    return [SharedTripRead(
        id=trip.id, destination=trip.destination, start_date=trip.start_date,
        end_date=trip.end_date, scope=grant.scope, owner_email=owner.email,
    ) for trip, grant, owner in rows]


@router.get("/shared/trips/{trip_id}", response_model=SharedTripDetailRead)
async def get_shared_trip(
    trip_id: UUID,
    session: AsyncSession = Depends(get_session),
    user: UserModel = Depends(get_current_user),
):
    row = (await session.execute(
        select(TripModel, TripShareGrantModel, UserModel)
        .join(TripShareGrantModel, TripShareGrantModel.trip_id == TripModel.id)
        .join(UserModel, UserModel.id == TripModel.owner_id)
        .join(FriendshipModel, and_(
            FriendshipModel.user_low_id == func.least(user.id, TripModel.owner_id),
            FriendshipModel.user_high_id == func.greatest(user.id, TripModel.owner_id),
            FriendshipModel.status == "ACCEPTED",
        ))
        .where(TripModel.id == trip_id, TripShareGrantModel.grantee_user_id == user.id,
               TripShareGrantModel.owner_user_id == TripModel.owner_id,
               TripShareGrantModel.revoked_at.is_(None))
    )).first()
    if row is None:
        raise HTTPException(404, detail="Resource not found")
    trip, grant, owner = row
    latest_version = None
    nodes: list[SharedTripPlanNodeRead] = []
    if grant.scope == "TRIP_AND_PLAN":
        latest_version = await session.scalar(select(func.max(TripPlanModel.version)).where(TripPlanModel.trip_id == trip_id))
        if latest_version is not None:
            plan_id = await session.scalar(select(TripPlanModel.id).where(
                TripPlanModel.trip_id == trip_id, TripPlanModel.version == latest_version
            ))
            plan_nodes = (await session.execute(
                select(TripPlanNodeModel, PoiModel.name)
                .join(PoiModel, PoiModel.id == TripPlanNodeModel.poi_id)
                .where(TripPlanNodeModel.plan_id == plan_id)
                .order_by(TripPlanNodeModel.day, TripPlanNodeModel.start_time)
            )).all()
            nodes = [SharedTripPlanNodeRead(
                name=name, day=node.day, start_time=node.start_time.isoformat(),
                end_time=node.end_time.isoformat(), status=node.status,
            ) for node, name in plan_nodes]
    return SharedTripDetailRead(
        id=trip.id, destination=trip.destination, start_date=trip.start_date,
        end_date=trip.end_date, scope=grant.scope, owner_email=owner.email,
        plan_version=latest_version, plan_nodes=nodes,
    )
