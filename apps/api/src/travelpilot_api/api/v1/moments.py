import asyncio
import hashlib
import json
import logging
from datetime import UTC, datetime
from uuid import UUID, uuid4

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import Response
from sqlalchemy import and_, func, or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from travelpilot_infra.db.models.social import (
    FriendshipModel,
    MemorySuggestionModel,
    MomentCommentModel,
    MomentMediaModel,
    MomentReactionModel,
    TravelMomentModel,
)
from travelpilot_infra.db.models.trip import TripModel
from travelpilot_infra.db.models.user import UserModel
from travelpilot_infra.db.models.workflow import MemoryModel
from travelpilot_infra.db.session import get_session

from travelpilot_api.auth import get_current_user
from travelpilot_api.media_storage import (
    normalize_image,
    read_media,
    remove_media_keys,
    write_image,
)
from travelpilot_api.schemas.moments import (
    MemorySuggestionRead,
    MemorySuggestionUpdate,
    MomentCommentCreate,
    MomentCommentRead,
    MomentCreate,
    MomentMediaRead,
    MomentRead,
    MomentUpdate,
)
from travelpilot_api.settings import settings

router = APIRouter(tags=["travel moments"])
logger = logging.getLogger("travelpilot.moments")


async def _can_view_moment(session: AsyncSession, user_id: UUID, moment: TravelMomentModel) -> bool:
    if moment.owner_user_id == user_id:
        return True
    low, high = sorted((moment.owner_user_id, user_id), key=lambda value: value.int)
    return bool(await session.scalar(select(FriendshipModel.id).where(
        FriendshipModel.user_low_id == low,
        FriendshipModel.user_high_id == high,
        FriendshipModel.status == "ACCEPTED",
    ))) and moment.audience == "FRIENDS"


async def _load_moment(session: AsyncSession, moment_id: UUID, user: UserModel) -> TravelMomentModel:
    moment = await session.get(TravelMomentModel, moment_id)
    if moment is None or not await _can_view_moment(session, user.id, moment):
        raise HTTPException(404, detail="Resource not found")
    return moment


async def _serialize_moments(session: AsyncSession, moments: list[TravelMomentModel], user: UserModel) -> list[MomentRead]:
    if not moments:
        return []
    ids = [moment.id for moment in moments]
    owners = {row.id: row.email for row in (await session.scalars(
        select(UserModel).where(UserModel.id.in_({moment.owner_user_id for moment in moments}))
    )).all()}
    media_rows = (await session.scalars(select(MomentMediaModel).where(
        MomentMediaModel.moment_id.in_(ids)).order_by(MomentMediaModel.created_at))).all()
    media_by_moment: dict[UUID, list[MomentMediaRead]] = {moment_id: [] for moment_id in ids}
    for asset in media_rows:
        media_by_moment[asset.moment_id].append(MomentMediaRead(
            id=asset.id, mime_type=asset.mime_type, size_bytes=asset.size_bytes, created_at=asset.created_at,
        ))
    reaction_rows = (await session.execute(select(
        MomentReactionModel.moment_id, func.count(MomentReactionModel.id)
    ).where(MomentReactionModel.moment_id.in_(ids)).group_by(MomentReactionModel.moment_id))).all()
    reaction_counts = dict(reaction_rows)
    reacted = set((await session.scalars(select(MomentReactionModel.moment_id).where(
        MomentReactionModel.moment_id.in_(ids), MomentReactionModel.user_id == user.id
    ))).all())
    comment_rows = (await session.execute(select(
        MomentCommentModel.moment_id, func.count(MomentCommentModel.id)
    ).where(MomentCommentModel.moment_id.in_(ids)).group_by(MomentCommentModel.moment_id))).all()
    comment_counts = dict(comment_rows)
    return [MomentRead(
        id=moment.id, owner_user_id=moment.owner_user_id, author_email=owners.get(moment.owner_user_id, ""),
        body=moment.body, audience=moment.audience, memory_extraction_enabled=moment.memory_extraction_enabled,
        day=moment.day, place_name=moment.place_name,
        trip_id=moment.trip_id if moment.owner_user_id == user.id else None,
        created_at=moment.created_at, media=media_by_moment[moment.id],
        reaction_count=reaction_counts.get(moment.id, 0), reacted_by_me=moment.id in reacted,
        comment_count=comment_counts.get(moment.id, 0),
    ) for moment in moments]


@router.get("/moments", response_model=list[MomentRead])
async def list_moments(
    limit: int = Query(default=50, ge=1, le=100),
    session: AsyncSession = Depends(get_session),
    user: UserModel = Depends(get_current_user),
):
    pairs = (await session.execute(select(FriendshipModel.user_low_id, FriendshipModel.user_high_id).where(
        FriendshipModel.status == "ACCEPTED",
        or_(FriendshipModel.user_low_id == user.id, FriendshipModel.user_high_id == user.id),
    ))).all()
    friend_ids = {
        high if low == user.id else low for low, high in pairs
    }
    moments = (await session.scalars(select(TravelMomentModel).where(
        or_(
            TravelMomentModel.owner_user_id == user.id,
            and_(TravelMomentModel.owner_user_id.in_(friend_ids), TravelMomentModel.audience == "FRIENDS"),
        )
    ).order_by(TravelMomentModel.created_at.desc()).limit(limit))).all()
    return await _serialize_moments(session, moments, user)


@router.post("/moments", response_model=MomentRead, status_code=201)
async def create_moment(
    payload: MomentCreate,
    session: AsyncSession = Depends(get_session),
    user: UserModel = Depends(get_current_user),
):
    if payload.trip_id is not None:
        trip = await session.scalar(select(TripModel).where(
            TripModel.id == payload.trip_id, TripModel.owner_id == user.id
        ))
        if trip is None:
            raise HTTPException(404, detail="Resource not found")
        if payload.day and not trip.start_date <= payload.day <= trip.end_date:
            raise HTTPException(422, detail="旅行日期超出 Trip 范围")
    moment = TravelMomentModel(
        owner_user_id=user.id, trip_id=payload.trip_id, day=payload.day,
        place_name=payload.place_name.strip() if payload.place_name else None,
        body=payload.body.strip(), audience=payload.audience,
        memory_extraction_enabled=payload.memory_extraction_enabled,
    )
    session.add(moment)
    await session.commit()
    await session.refresh(moment)
    return (await _serialize_moments(session, [moment], user))[0]


@router.patch("/moments/{moment_id}", response_model=MomentRead)
async def update_moment(
    moment_id: UUID,
    payload: MomentUpdate,
    session: AsyncSession = Depends(get_session),
    user: UserModel = Depends(get_current_user),
):
    moment = await session.scalar(select(TravelMomentModel).where(
        TravelMomentModel.id == moment_id, TravelMomentModel.owner_user_id == user.id
    ))
    if moment is None:
        raise HTTPException(404, detail="Resource not found")
    changes = payload.model_dump(exclude_unset=True)
    if "body" in changes and changes["body"] is not None:
        changes["body"] = changes["body"].strip()
    if "day" in changes and changes["day"] is not None and moment.trip_id is not None:
        trip = await session.scalar(select(TripModel).where(
            TripModel.id == moment.trip_id, TripModel.owner_id == user.id
        ))
        if trip is None or not trip.start_date <= changes["day"] <= trip.end_date:
            raise HTTPException(422, detail="旅行日期超出 Trip 范围")
    if changes.get("memory_extraction_enabled") is False:
        await session.execute(
            update(MemorySuggestionModel).where(
                MemorySuggestionModel.source_moment_id == moment.id,
                MemorySuggestionModel.owner_user_id == user.id,
                MemorySuggestionModel.status == "PENDING",
            ).values(status="REJECTED")
        )
    for name, value in changes.items():
        setattr(moment, name, value)
    moment.updated_at = datetime.now(UTC)
    await session.commit()
    await session.refresh(moment)
    return (await _serialize_moments(session, [moment], user))[0]


@router.delete("/moments/{moment_id}", status_code=204)
async def delete_moment(
    moment_id: UUID,
    session: AsyncSession = Depends(get_session),
    user: UserModel = Depends(get_current_user),
):
    moment = await session.scalar(select(TravelMomentModel).where(
        TravelMomentModel.id == moment_id, TravelMomentModel.owner_user_id == user.id
    ))
    if moment is None:
        raise HTTPException(404, detail="Resource not found")
    keys = (await session.execute(select(MomentMediaModel.object_key, MomentMediaModel.thumbnail_key).where(
        MomentMediaModel.moment_id == moment_id
    ))).all()
    await session.delete(moment)
    await session.commit()
    remove_media_keys([key for pair in keys for key in pair])


@router.post("/moments/{moment_id}/media", response_model=MomentMediaRead, status_code=201)
async def upload_moment_media(
    moment_id: UUID,
    request: Request,
    session: AsyncSession = Depends(get_session),
    user: UserModel = Depends(get_current_user),
):
    moment = await session.scalar(select(TravelMomentModel).where(
        TravelMomentModel.id == moment_id, TravelMomentModel.owner_user_id == user.id
    ))
    if moment is None:
        raise HTTPException(404, detail="Resource not found")
    if request.headers.get("content-type", "").split(";", 1)[0].strip().lower() not in {
        "image/jpeg", "image/png", "image/webp",
    }:
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
    content = b"".join(chunks)
    if not content:
        raise HTTPException(400, detail="图片内容为空")
    try:
        original, thumbnail, extension, mime_type = await asyncio.to_thread(
            normalize_image, content, request.headers.get("content-type", "")
        )
    except ValueError as exc:
        raise HTTPException(400, detail=str(exc)) from exc
    if len(original) > 20 * 1024 * 1024:
        raise HTTPException(413, detail="处理后的图片超出大小限制，请缩小图片后重试")
    if len((await session.scalars(select(MomentMediaModel.id).where(
        MomentMediaModel.moment_id == moment_id
    ))).all()) >= 12:
        raise HTTPException(413, detail="每条旅行动态最多添加 12 张照片")
    stored_bytes = await session.scalar(select(func.coalesce(func.sum(MomentMediaModel.size_bytes), 0)).where(
        MomentMediaModel.owner_user_id == user.id
    ))
    if stored_bytes + len(original) > 200 * 1024 * 1024:
        raise HTTPException(413, detail="账户照片空间已达到 200 MB")
    asset_id = uuid4()
    try:
        object_key, thumbnail_key = await asyncio.to_thread(
            write_image, user.id, asset_id, extension, original, thumbnail
        )
    except OSError as exc:
        logger.exception("media_write_failed owner_id=%s", user.id)
        raise HTTPException(503, detail="图片保存失败，请稍后重试") from exc
    asset = MomentMediaModel(
        id=asset_id, moment_id=moment_id, owner_user_id=user.id,
        object_key=object_key, thumbnail_key=thumbnail_key, mime_type=mime_type,
        size_bytes=len(original), sha256=hashlib.sha256(original).hexdigest(), exif_stripped=True,
    )
    session.add(asset)
    try:
        await session.commit()
    except Exception:
        await session.rollback()
        remove_media_keys([object_key, thumbnail_key])
        raise
    await session.refresh(asset)
    return MomentMediaRead.model_validate(asset)


@router.get("/media/{asset_id}")
async def read_moment_media(
    asset_id: UUID,
    session: AsyncSession = Depends(get_session),
    user: UserModel = Depends(get_current_user),
):
    asset = await session.get(MomentMediaModel, asset_id)
    if asset is None:
        raise HTTPException(404, detail="Resource not found")
    moment = await session.get(TravelMomentModel, asset.moment_id)
    if moment is None or not await _can_view_moment(session, user.id, moment):
        raise HTTPException(404, detail="Resource not found")
    try:
        content = await asyncio.to_thread(read_media, asset.object_key)
    except Exception as exc:
        logger.exception("media_read_failed asset_id=%s", asset.id)
        raise HTTPException(503, detail="图片暂时无法读取，请稍后重试") from exc
    return Response(content, media_type=asset.mime_type, headers={
        "Cache-Control": "private, no-store", "X-Content-Type-Options": "nosniff",
    })


@router.get("/media/{asset_id}/thumbnail")
async def read_moment_thumbnail(
    asset_id: UUID,
    session: AsyncSession = Depends(get_session),
    user: UserModel = Depends(get_current_user),
):
    asset = await session.get(MomentMediaModel, asset_id)
    if asset is None:
        raise HTTPException(404, detail="Resource not found")
    moment = await session.get(TravelMomentModel, asset.moment_id)
    if moment is None or not await _can_view_moment(session, user.id, moment):
        raise HTTPException(404, detail="Resource not found")
    try:
        content = await asyncio.to_thread(read_media, asset.thumbnail_key)
    except Exception as exc:
        logger.exception("media_read_failed asset_id=%s", asset.id)
        raise HTTPException(503, detail="图片暂时无法读取，请稍后重试") from exc
    return Response(content, media_type=asset.mime_type, headers={
        "Cache-Control": "private, no-store", "X-Content-Type-Options": "nosniff",
    })


@router.get("/moments/{moment_id}/comments", response_model=list[MomentCommentRead])
async def list_moment_comments(
    moment_id: UUID,
    session: AsyncSession = Depends(get_session),
    user: UserModel = Depends(get_current_user),
):
    await _load_moment(session, moment_id, user)
    rows = (await session.execute(select(MomentCommentModel, UserModel.email)
        .join(UserModel, UserModel.id == MomentCommentModel.author_user_id)
        .where(MomentCommentModel.moment_id == moment_id)
        .order_by(MomentCommentModel.created_at))).all()
    return [MomentCommentRead(
        id=comment.id, author_user_id=comment.author_user_id, author_email=email,
        body=comment.body, created_at=comment.created_at,
    ) for comment, email in rows]


@router.post("/moments/{moment_id}/comments", response_model=MomentCommentRead, status_code=201)
async def create_moment_comment(
    moment_id: UUID,
    payload: MomentCommentCreate,
    session: AsyncSession = Depends(get_session),
    user: UserModel = Depends(get_current_user),
):
    await _load_moment(session, moment_id, user)
    comment = MomentCommentModel(moment_id=moment_id, author_user_id=user.id, body=payload.body.strip())
    session.add(comment)
    await session.commit()
    await session.refresh(comment)
    return MomentCommentRead(
        id=comment.id, author_user_id=user.id, author_email=user.email,
        body=comment.body, created_at=comment.created_at,
    )


@router.delete("/moments/{moment_id}/comments/{comment_id}", status_code=204)
async def delete_moment_comment(
    moment_id: UUID,
    comment_id: UUID,
    session: AsyncSession = Depends(get_session),
    user: UserModel = Depends(get_current_user),
):
    moment = await _load_moment(session, moment_id, user)
    comment = await session.scalar(select(MomentCommentModel).where(
        MomentCommentModel.id == comment_id, MomentCommentModel.moment_id == moment_id,
        or_(MomentCommentModel.author_user_id == user.id, TravelMomentModel.owner_user_id == user.id),
    ).join(TravelMomentModel, TravelMomentModel.id == MomentCommentModel.moment_id))
    if comment is None or (comment.author_user_id != user.id and moment.owner_user_id != user.id):
        raise HTTPException(404, detail="Resource not found")
    await session.delete(comment)
    await session.commit()


@router.put("/moments/{moment_id}/reaction", status_code=204)
async def set_moment_reaction(
    moment_id: UUID,
    session: AsyncSession = Depends(get_session),
    user: UserModel = Depends(get_current_user),
):
    await _load_moment(session, moment_id, user)
    reaction = await session.scalar(select(MomentReactionModel).where(
        MomentReactionModel.moment_id == moment_id, MomentReactionModel.user_id == user.id
    ))
    if reaction is None:
        session.add(MomentReactionModel(moment_id=moment_id, user_id=user.id, kind="LIKE"))
    else:
        reaction.kind = "LIKE"
    try:
        await session.commit()
    except IntegrityError:
        await session.rollback()


@router.delete("/moments/{moment_id}/reaction", status_code=204)
async def delete_moment_reaction(
    moment_id: UUID,
    session: AsyncSession = Depends(get_session),
    user: UserModel = Depends(get_current_user),
):
    await _load_moment(session, moment_id, user)
    reaction = await session.scalar(select(MomentReactionModel).where(
        MomentReactionModel.moment_id == moment_id, MomentReactionModel.user_id == user.id
    ))
    if reaction is not None:
        await session.delete(reaction)
        await session.commit()


@router.post("/moments/{moment_id}/caption")
async def draft_moment_caption(
    moment_id: UUID,
    session: AsyncSession = Depends(get_session),
    user: UserModel = Depends(get_current_user),
):
    moment = await session.scalar(select(TravelMomentModel).where(
        TravelMomentModel.id == moment_id, TravelMomentModel.owner_user_id == user.id
    ))
    if moment is None:
        raise HTTPException(404, detail="Resource not found")
    if not settings.llm_api_key or not settings.llm_model:
        raise HTTPException(503, detail="尚未配置文案生成模型")
    prompt = (
        "请基于用户自己的旅行文字，写 2 条简体中文社交媒体文案草稿。保留用户原意，不补充未提供的事实，"
        "不做心理诊断，不添加标签之外的假地点。只返回 JSON：{\"captions\":[\"...\",\"...\"]}。\n"
        f"地点：{moment.place_name or '未指定'}\n旅行感受：{moment.body}"
    )
    try:
        async with httpx.AsyncClient(timeout=20) as client:
            response = await client.post(
                f"{settings.llm_base_url.rstrip('/')}/chat/completions",
                headers={"Authorization": f"Bearer {settings.llm_api_key}"},
                json={"model": settings.llm_model, "messages": [{"role": "user", "content": prompt}],
                      "temperature": 0.7, "response_format": {"type": "json_object"}},
            )
        response.raise_for_status()
        content = response.json()["choices"][0]["message"]["content"]
        captions = json.loads(content).get("captions", [])
        captions = [text.strip()[:1000] for text in captions if isinstance(text, str) and text.strip()][:2]
        if not captions:
            raise ValueError("empty captions")
        return {"captions": captions, "status": "DRAFT", "requires_review": True}
    except (httpx.HTTPError, KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        logger.warning("moment_caption_failed owner_id=%s error_type=%s", user.id, type(exc).__name__)
        raise HTTPException(503, detail="文案生成暂时不可用；你的原文仍已保存") from exc


@router.post("/moments/{moment_id}/memory-suggestions", response_model=MemorySuggestionRead, status_code=201)
async def create_memory_suggestion(
    moment_id: UUID,
    session: AsyncSession = Depends(get_session),
    user: UserModel = Depends(get_current_user),
):
    moment = await session.scalar(select(TravelMomentModel).where(
        TravelMomentModel.id == moment_id, TravelMomentModel.owner_user_id == user.id
    ))
    if moment is None:
        raise HTTPException(404, detail="Resource not found")
    if not moment.memory_extraction_enabled:
        raise HTTPException(403, detail="请先明确启用此条内容的记忆提取")
    if moment.trip_id is None:
        raise HTTPException(422, detail="请先将旅行片段关联到自己的 Trip，再创建可用于规划的记忆")
    pending = await session.scalar(select(MemorySuggestionModel).where(
        MemorySuggestionModel.owner_user_id == user.id,
        MemorySuggestionModel.source_moment_id == moment.id,
        MemorySuggestionModel.status == "PENDING",
    ).order_by(MemorySuggestionModel.created_at.desc()))
    if pending is not None:
        return pending
    if not settings.llm_api_key or not settings.llm_model:
        raise HTTPException(503, detail="尚未配置记忆提取模型")
    prompt = (
        "你只从用户提供的旅行文字中提取一条可帮助旅行规划的非临床偏好或旅行体验。"
        "不诊断情绪或心理健康，不猜测文本之外的信息。若无明确偏好返回空 candidate。"
        "evidence 必须逐字摘自原文。只返回 JSON：{\"candidate\":\"...\",\"evidence\":\"...\"}。\n"
        f"旅行文字：{moment.body}"
    )
    try:
        async with httpx.AsyncClient(timeout=20) as client:
            response = await client.post(
                f"{settings.llm_base_url.rstrip('/')}/chat/completions",
                headers={"Authorization": f"Bearer {settings.llm_api_key}"},
                json={"model": settings.llm_model, "messages": [{"role": "user", "content": prompt}],
                      "temperature": 0, "response_format": {"type": "json_object"}},
            )
        response.raise_for_status()
        result = json.loads(response.json()["choices"][0]["message"]["content"])
        candidate = str(result.get("candidate", "")).strip()
        evidence = str(result.get("evidence", "")).strip()
        if not candidate or not evidence or evidence not in moment.body:
            raise ValueError("model returned no verifiable memory evidence")
    except (httpx.HTTPError, KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        logger.warning("memory_suggestion_failed owner_id=%s error_type=%s", user.id, type(exc).__name__)
        raise HTTPException(503, detail="记忆建议暂时不可用；你的原文仍已保存") from exc
    suggestion = MemorySuggestionModel(
        owner_user_id=user.id, source_moment_id=moment.id,
        candidate=candidate[:2000], evidence=evidence[:2000], status="PENDING",
    )
    session.add(suggestion)
    await session.commit()
    await session.refresh(suggestion)
    return MemorySuggestionRead.model_validate(suggestion)


@router.get("/moments/{moment_id}/memory-suggestions", response_model=list[MemorySuggestionRead])
async def list_memory_suggestions(
    moment_id: UUID,
    session: AsyncSession = Depends(get_session),
    user: UserModel = Depends(get_current_user),
):
    moment = await session.scalar(select(TravelMomentModel).where(
        TravelMomentModel.id == moment_id, TravelMomentModel.owner_user_id == user.id
    ))
    if moment is None:
        raise HTTPException(404, detail="Resource not found")
    return (await session.scalars(select(MemorySuggestionModel).where(
        MemorySuggestionModel.owner_user_id == user.id,
        MemorySuggestionModel.source_moment_id == moment_id,
    ).order_by(MemorySuggestionModel.created_at.desc()))).all()


@router.patch("/moments/{moment_id}/memory-suggestions/{suggestion_id}", response_model=MemorySuggestionRead)
async def review_memory_suggestion(
    moment_id: UUID,
    suggestion_id: UUID,
    payload: MemorySuggestionUpdate,
    session: AsyncSession = Depends(get_session),
    user: UserModel = Depends(get_current_user),
):
    suggestion = await session.scalar(select(MemorySuggestionModel).join(
        TravelMomentModel, TravelMomentModel.id == MemorySuggestionModel.source_moment_id
    ).where(
        MemorySuggestionModel.id == suggestion_id,
        MemorySuggestionModel.source_moment_id == moment_id,
        MemorySuggestionModel.owner_user_id == user.id,
        MemorySuggestionModel.status == "PENDING",
        TravelMomentModel.owner_user_id == user.id,
    ))
    if suggestion is None:
        raise HTTPException(404, detail="Resource not found")
    suggestion.candidate = payload.candidate.strip()
    if payload.accepted:
        moment = await session.get(TravelMomentModel, moment_id)
        if moment is None or moment.trip_id is None:
            raise HTTPException(409, detail="关联旅行已不可用，无法将记忆用于规划")
        previous = await session.scalar(select(MemoryModel.id).where(
            MemoryModel.trip_id == moment.trip_id,
            MemoryModel.text == suggestion.candidate,
        ))
        if previous is None:
            session.add(MemoryModel(
                trip_id=moment.trip_id, feedback_id=None, memory_type="SEMANTIC_PREFERENCE",
                text=suggestion.candidate, importance=0.5, confidence=0.6, confirmed=True,
            ))
        suggestion.status = "CONFIRMED"
    else:
        suggestion.status = "REJECTED"
    await session.commit()
    await session.refresh(suggestion)
    return MemorySuggestionRead.model_validate(suggestion)
