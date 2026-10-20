import asyncio
import logging
from urllib.parse import urlsplit
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from travelpilot_core.agents.extraction import TextExtractionFailure, extract_places
from travelpilot_core.skills import select_skill_decision
from travelpilot_infra.db.models.poi import PoiModel
from travelpilot_infra.db.models.trip import TripModel
from travelpilot_infra.db.models.wishlist import WishlistItemModel
from travelpilot_infra.db.models.workflow import (
    ContentItemModel,
    ContentSourceModel,
    EntityCandidateModel,
    ExtractedEntityModel,
)
from travelpilot_infra.db.session import get_session
from travelpilot_infra.providers.maps.errors import MapsProviderError

from travelpilot_api.dependencies import get_maps_provider, get_owned_content, get_owned_trip
from travelpilot_api.media_storage import normalize_image
from travelpilot_api.ocr import recognize_text
from travelpilot_api.schemas.content import (
    CandidateRead,
    ConfirmCandidates,
    ContentCreate,
    ContentRead,
    ContentSummaryRead,
    EntityRead,
    GuideFactsUpdate,
    OcrTextRead,
)
from travelpilot_api.settings import settings

router = APIRouter(tags=["content"])
logger = logging.getLogger("travelpilot.content")


def _guide_place_category(raw_category: str, name: str) -> str:
    value = f"{raw_category} {name}".casefold()
    if any(term in value for term in ("food", "restaurant", "餐饮", "餐厅", "餐馆", "饭店", "食府", "酒楼", "火锅", "小吃")):
        return "FOOD"
    if any(term in value for term in ("lodging", "hotel", "hostel", "住宿", "酒店", "宾馆", "民宿", "客栈", "旅舍")):
        return "LODGING"
    if any(term in value for term in ("attraction", "sightseeing", "景点", "景区", "公园", "博物馆", "古城", "古镇", "街区", "寺", "祠")):
        return "ATTRACTION"
    return "OTHER"


@router.post("/trips/{trip_id}/content/ocr", response_model=OcrTextRead)
async def recognize_content_image(trip_id: UUID, file: UploadFile = File(...),
                                  session: AsyncSession = Depends(get_session),
                                  _trip: TripModel = Depends(get_owned_trip)):
    if file.content_type not in {"image/jpeg", "image/png", "image/webp"}:
        raise HTTPException(415, detail="仅支持 JPEG、PNG 或 WebP 图片")
    if file.size is not None and file.size > settings.media_max_upload_bytes:
        raise HTTPException(413, detail="图片超出单张大小限制")
    content = await file.read(settings.media_max_upload_bytes + 1)
    if len(content) > settings.media_max_upload_bytes:
        raise HTTPException(413, detail="图片超出单张大小限制")
    try:
        normalized, _, _, _ = await asyncio.to_thread(normalize_image, content, file.content_type)
        text, confidence = await asyncio.to_thread(recognize_text, normalized)
    except ValueError as exc:
        raise HTTPException(400, detail=str(exc)) from exc
    except Exception as exc:
        logger.warning("content_ocr_failed error_type=%s", type(exc).__name__)
        raise HTTPException(503, detail="图片文字识别暂时不可用") from exc
    return OcrTextRead(text=text, confidence=confidence, stored=False)


async def _read(session: AsyncSession, content_id: UUID) -> ContentRead:
    source = await session.get(ContentSourceModel, content_id)
    if source is None:
        raise HTTPException(404, detail="Content not found")
    entities = (await session.scalars(select(ExtractedEntityModel).where(
        ExtractedEntityModel.source_id == content_id
    ).order_by(ExtractedEntityModel.name))).all()
    result = []
    for entity in entities:
        candidates = (await session.scalars(select(EntityCandidateModel).where(
            EntityCandidateModel.entity_id == entity.id
        ).order_by(EntityCandidateModel.score.desc()))).all()
        result.append(EntityRead(
            id=entity.id, name=entity.name, category=entity.category,
            suggestion=entity.suggestion, evidence=entity.evidence, confidence=entity.confidence,
            candidates=[CandidateRead.model_validate(candidate) for candidate in candidates],
        ))
    return ContentRead(
        id=source.id, trip_id=source.trip_id, content_type=source.content_type,
        text=source.text, status=source.status, error=source.error,
        skill_name=source.skill_name, skill_version=source.skill_version, token_usage=source.token_usage,
        skill_applicability=source.skill_applicability,
        extraction_metadata=source.extraction_metadata,
        guide_facts=source.guide_facts or [],
        created_at=source.created_at, entities=result,
    )


async def _save_extraction_results(
    session: AsyncSession, source: ContentSourceModel, extraction, maps,
) -> bool:
    """Persist newly extracted facts and unique places; return map failure state."""
    source.token_usage = (source.token_usage or 0) + extraction.token_usage
    existing_facts = list(source.guide_facts or [])
    fact_keys = {
        (item.get("kind"), "".join(str(item.get("content", "")).split()).casefold())
        for item in existing_facts
    }
    for fact in extraction.guide_facts:
        key = (fact.kind, "".join(fact.content.split()).casefold())
        if key in fact_keys:
            continue
        fact_keys.add(key)
        existing_facts.append({
            "id": str(uuid4()), "kind": fact.kind, "title": fact.title,
            "content": fact.content, "evidence": fact.evidence,
            "related_places": fact.related_places, "confidence": fact.confidence,
        })
    source.guide_facts = existing_facts

    saved_entities = list((await session.scalars(
        select(ExtractedEntityModel).where(ExtractedEntityModel.source_id == source.id)
    )).all())
    known_names = {"".join(entity.name.split()).casefold() for entity in saved_entities}
    map_unavailable = False
    for mention in extraction.places:
        key = "".join(mention.name.split()).casefold()
        if key in known_names:
            continue
        known_names.add(key)
        evidence = mention.evidence if mention.evidence and mention.evidence in source.text else None
        entity = ExtractedEntityModel(
            source_id=source.id, name=mention.name,
            category=_guide_place_category(mention.category, mention.name),
            suggestion=mention.suggestion, evidence=evidence, confidence=mention.confidence,
        )
        session.add(entity)
        await session.flush()
        try:
            places = await maps.search_place(mention.name)
        except MapsProviderError:
            logger.info("content_place_lookup_unavailable source_id=%s", source.id)
            map_unavailable = True
            continue
        for place in places[:5]:
            session.add(EntityCandidateModel(
                entity_id=entity.id, provider=place.provider, provider_id=place.provider_id,
                name=place.name, address=place.address, latitude=place.latitude,
                longitude=place.longitude, category=place.category,
                duration_min=place.suggested_duration_min,
                score=1.0 if place.name == mention.name else 0.7, confirmed=False,
            ))
    return map_unavailable


def _deepseek_thinking_mode() -> str | None:
    return "disabled" if urlsplit(settings.llm_base_url).hostname == "api.deepseek.com" else None


@router.post("/trips/{trip_id}/content", response_model=ContentRead, status_code=status.HTTP_201_CREATED)
async def create_content(trip_id: UUID, payload: ContentCreate,
                         session: AsyncSession = Depends(get_session), maps=Depends(get_maps_provider),
                         _trip: TripModel = Depends(get_owned_trip)):
    duplicate_rows = (await session.scalars(
        select(ContentSourceModel.id).where(
            ContentSourceModel.trip_id == trip_id,
            ContentSourceModel.text == payload.text,
        ).limit(1)
    )).all()
    duplicate_of = duplicate_rows[0] if duplicate_rows else None
    source = ContentSourceModel(trip_id=trip_id, content_type=payload.content_type,
                                text=payload.text, status="PARSING")
    if duplicate_of:
        source.extraction_metadata = {"duplicate_source_id": str(duplicate_of)}
    session.add(source)
    await session.flush()
    session.add(ContentItemModel(source_id=source.id, text=payload.text, kind="TEXT"))
    try:
        selection = select_skill_decision(
            "content_import",
            context={
                "task": "content_import",
                "user_content": bool(payload.text.strip()),
                "provider_unavailable": False,
            },
        )
        skill = selection.skill
        source.skill_applicability = selection.to_record()
        if skill:
            source.skill_name, source.skill_version = skill.name, skill.version
        # DeepSeek enables reasoning by default. With JSON extraction, the
        # reasoning tokens can consume the whole completion budget and leave
        # message.content empty (finish_reason=length), even on HTTP 200.
        thinking_mode = _deepseek_thinking_mode()
        extraction = await extract_places(payload.text, api_key=settings.llm_api_key,
                                          model=settings.llm_model, base_url=settings.llm_base_url,
                                          token_budget=settings.llm_max_tokens_per_turn,
                                          additional_instructions=skill.instructions if skill else "",
                                          thinking_mode=thinking_mode, guide_import=True)
        map_unavailable = await _save_extraction_results(session, source, extraction, maps)
        failed = extraction.failed_chunks
        completed = max(0, extraction.chunk_count - len(failed))
        source.extraction_metadata = {
            "total_chunks": extraction.chunk_count,
            "completed_chunks": completed,
            "failed_chunks": failed,
            "fallback_chunks": extraction.fallback_chunks,
            "retry_attempts": 0,
            "complete": not failed,
            **({"duplicate_source_id": str(duplicate_of)} if duplicate_of else {}),
        }
        has_results = bool(extraction.places or extraction.guide_facts)
        source.status = (
            "PARTIAL" if failed and has_results else
            "FAILED" if failed or not has_results else
            "NEEDS_CONFIRMATION"
        )
        if failed:
            indexes = ", ".join(str(int(item["index"]) + 1) for item in failed)
            fallback_note = (
                f"其中 {len(extraction.fallback_chunks)} 段已由本地规则保留候选结果，请核对后重试。"
                if extraction.fallback_chunks else ""
            )
            source.error = (
                f"模型已识别 {completed}/{extraction.chunk_count} 段；第 {indexes} 段未完成。"
                f"{fallback_note}"
            )
        if map_unavailable:
            source.error = (source.error + " " if source.error else "") + "部分地点的地图匹配暂不可用，已保留攻略信息。"
        elif source.status == "FAILED" and not failed:
            source.error = "没有识别到可用的地点或攻略细节；请检查文本后重试。"
    except TextExtractionFailure as exc:
        cause = exc.__cause__
        logger.warning(
            "content_text_extraction_failed source_id=%s cause_type=%s response_shape=%s provider_metadata=%s",
            source.id,
            type(cause).__name__ if cause else "unknown",
            exc.response_shape,
            exc.provider_metadata,
        )
        source.status = "FAILED"
        failure_code = (exc.provider_metadata or {}).get("failure_code")
        retryable = failure_code not in {"INPUT_TOO_LONG", "RESULT_LIMIT_EXCEEDED", "CHUNK_LIMIT_EXCEEDED"}
        source.extraction_metadata = {
            "total_chunks": 1 if retryable else None,
            "completed_chunks": 0 if retryable else None,
            "failed_chunks": ([{"index": 0, "start": 0, "end": len(payload.text),
                                "failure_code": failure_code or "MODEL_RESPONSE_INVALID",
                                "finish_reason": (exc.provider_metadata or {}).get("finish_reason")}]
                              if retryable else []),
            "retry_attempts": 0,
            "complete": False,
            "failure_code": failure_code,
            **({"duplicate_source_id": str(duplicate_of)} if duplicate_of else {}),
        }
        if failure_code == "INPUT_TOO_LONG":
            source.error = "攻略超过 20,000 字符的导入上限；请拆分后分别导入。"
        elif failure_code == "RESULT_LIMIT_EXCEEDED":
            source.error = "攻略信息量超过单次确认上限；请按行程日期或主题拆分后导入。"
        elif (exc.provider_metadata or {}).get("finish_reason") == "length":
            source.error = "模型输出达到长度上限；本段未完成，可重试失败片段。"
        else:
            source.error = "攻略内容解析服务暂时不可用；请稍后重试。"
    except RuntimeError as exc:
        logger.warning("content_text_extraction_runtime_failed source_id=%s error_type=%s",
                       source.id, type(exc).__name__)
        source.status = "FAILED"
        source.error = "攻略内容解析暂时不可用；识别未完成，请稍后重试。"
    await session.commit()
    return await _read(session, source.id)


@router.get("/trips/{trip_id}/content", response_model=list[ContentSummaryRead])
async def list_trip_content(trip_id: UUID, session: AsyncSession = Depends(get_session),
                            _trip: TripModel = Depends(get_owned_trip)):
    sources = (await session.scalars(
        select(ContentSourceModel).where(ContentSourceModel.trip_id == trip_id)
        .order_by(ContentSourceModel.created_at.desc()).limit(30)
    )).all()
    return [ContentSummaryRead(
        id=source.id, content_type=source.content_type, status=source.status,
        error=source.error, extraction_metadata=source.extraction_metadata,
        created_at=source.created_at,
    ) for source in sources]


@router.post("/content/{content_id}/retry", response_model=ContentRead)
async def retry_content_extraction(content_id: UUID, session: AsyncSession = Depends(get_session),
                                   maps=Depends(get_maps_provider),
                                   source: ContentSourceModel = Depends(get_owned_content)):
    """Retry only failed text ranges; imported content is immutable."""
    if source.status not in {"PARTIAL", "FAILED"} or source.status == "IMPORTED":
        raise HTTPException(409, detail="Content has no retryable extraction failures")
    metadata = source.extraction_metadata or {}
    retry_attempts = metadata.get("retry_attempts", 0)
    if type(retry_attempts) is not int or retry_attempts >= 2:
        raise HTTPException(429, detail="This extraction has reached its retry limit")
    failed = metadata.get("failed_chunks")
    # Older versions rejected an otherwise valid guide when the merged result
    # exceeded the former 20-place cap. After increasing that cap, let the
    # user retry the complete source in place instead of deleting and reimporting.
    retry_whole_guide = metadata.get("failure_code") == "RESULT_LIMIT_EXCEEDED" and not failed
    if retry_whole_guide:
        failed = [{"index": 0, "start": 0, "end": len(source.text)}]
    if not isinstance(failed, list) or not failed:
        raise HTTPException(409, detail="Content has no retryable extraction failures")
    valid_ranges = []
    for item in failed:
        if not isinstance(item, dict):
            continue
        start, end, index = item.get("start"), item.get("end"), item.get("index")
        if type(start) is int and type(end) is int and type(index) is int and 0 <= start < end <= len(source.text):
            valid_ranges.append({**item, "start": start, "end": end, "index": index})
    if len(valid_ranges) != len(failed):
        raise HTTPException(409, detail="Stored retry ranges are invalid")

    remaining = []
    failed_usage = 0
    map_unavailable = False
    replaced_chunk_count = 0
    previous_chunk_count = metadata.get("total_chunks")
    previous_chunk_count = previous_chunk_count if type(previous_chunk_count) is int else 0
    next_chunk_index = previous_chunk_count
    fallback_chunks = list(metadata.get("fallback_chunks", []))
    skill = select_skill_decision("content_import", context={"task": "content_import", "user_content": True})
    retry_chunk_target = 300 if retry_attempts == 0 else 180
    for item in valid_ranges:
        segment = source.text[item["start"]:item["end"]]
        try:
            extraction = await extract_places(
                segment, api_key=settings.llm_api_key, model=settings.llm_model,
                base_url=settings.llm_base_url, token_budget=settings.llm_max_tokens_per_turn,
                additional_instructions=skill.skill.instructions if skill.skill else "",
                thinking_mode=_deepseek_thinking_mode(), guide_import=True,
                chunk_target_chars=retry_chunk_target,
            )
        except TextExtractionFailure as exc:
            details = exc.provider_metadata or {}
            usage = details.get("usage")
            if isinstance(usage, dict) and type(usage.get("total_tokens")) is int:
                failed_usage += usage["total_tokens"]
            remaining.append({**item,
                              "failure_code": details.get("failure_code", "MODEL_RESPONSE_INVALID"),
                              "finish_reason": details.get("finish_reason")})
            replaced_chunk_count += 1
            continue
        local_chunk_count = max(1, extraction.chunk_count)
        replaced_chunk_count += local_chunk_count
        map_unavailable = await _save_extraction_results(session, source, extraction, maps) or map_unavailable
        for chunk in extraction.fallback_chunks:
            local_start, local_end = chunk.get("start"), chunk.get("end")
            if type(local_start) is int and type(local_end) is int:
                fallback_chunks.append({
                    "index": next_chunk_index + int(chunk.get("index", 0)),
                    "start": item["start"] + local_start,
                    "end": item["start"] + local_end,
                })
        for chunk in extraction.failed_chunks:
            local_start, local_end = chunk.get("start"), chunk.get("end")
            if type(local_start) is not int or type(local_end) is not int:
                continue
            remaining.append({
                "index": next_chunk_index,
                "start": item["start"] + local_start,
                "end": item["start"] + local_end,
                "failure_code": chunk.get("failure_code", "MODEL_RESPONSE_INVALID"),
                "finish_reason": chunk.get("finish_reason"),
            })
            next_chunk_index += 1

    metadata["failed_chunks"] = remaining
    metadata["fallback_chunks"] = list({
        (chunk.get("start"), chunk.get("end")): chunk for chunk in fallback_chunks
    }.values())
    metadata["retry_attempts"] = retry_attempts + 1
    metadata["total_chunks"] = (
        replaced_chunk_count if retry_whole_guide else
        max(0, previous_chunk_count - len(valid_ranges) + replaced_chunk_count)
    )
    metadata["completed_chunks"] = max(0, metadata["total_chunks"] - len(remaining))
    metadata["complete"] = not remaining
    if remaining:
        metadata["failure_code"] = remaining[0].get("failure_code", "MODEL_RESPONSE_INVALID")
    else:
        metadata.pop("failure_code", None)
    source.extraction_metadata = metadata
    source.token_usage = (source.token_usage or 0) + failed_usage
    has_results = bool(source.guide_facts)
    if not has_results:
        has_results = bool((await session.scalars(
            select(ExtractedEntityModel.id).where(ExtractedEntityModel.source_id == source.id)
        )).first())
    source.status = "PARTIAL" if remaining and has_results else "FAILED" if remaining or not has_results else "NEEDS_CONFIRMATION"
    if remaining:
        indexes = ", ".join(str(int(item["index"]) + 1) for item in remaining)
        next_step = "可以再试一次。" if metadata["retry_attempts"] < 2 else "重试次数已用完，可以删除记录后重新导入。"
        fallback_note = (
            f"已有 {len(metadata['fallback_chunks'])} 段由本地规则保留候选结果，请核对。"
            if metadata["fallback_chunks"] else ""
        )
        source.error = (
            f"已识别 {metadata['completed_chunks']}/{metadata.get('total_chunks', 0)} 段；"
            f"第 {indexes} 段仍未完成。{fallback_note}{next_step}"
        )
    elif not has_results:
        source.error = "失败片段已重试，但没有识别到可用的地点或攻略细节。"
    elif map_unavailable:
        source.error = "攻略信息已识别，但部分地点的地图匹配暂不可用。"
    else:
        source.error = None
    await session.commit()
    return await _read(session, content_id)


@router.delete("/content/{content_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_content(content_id: UUID, session: AsyncSession = Depends(get_session),
                         source: ContentSourceModel = Depends(get_owned_content)):
    """Delete an imported source and extraction artifacts, preserving confirmed trip items."""
    await session.delete(source)
    await session.commit()


@router.get("/content/{content_id}", response_model=ContentRead)
async def get_content(content_id: UUID, session: AsyncSession = Depends(get_session),
                      _source: ContentSourceModel = Depends(get_owned_content)):
    return await _read(session, content_id)


@router.get("/content/{content_id}/candidates", response_model=list[CandidateRead])
async def list_candidates(content_id: UUID, session: AsyncSession = Depends(get_session),
                          _source: ContentSourceModel = Depends(get_owned_content)):
    content = await _read(session, content_id)
    return [candidate for entity in content.entities for candidate in entity.candidates]


@router.post("/content/{content_id}/confirm", response_model=ContentRead)
async def confirm_content(content_id: UUID, payload: ConfirmCandidates,
                          session: AsyncSession = Depends(get_session),
                          source: ContentSourceModel = Depends(get_owned_content)):
    if source.status not in ("NEEDS_CONFIRMATION", "IMPORTED"):
        raise HTTPException(409, detail="Content is not ready for confirmation")
    rows = (await session.execute(
        select(EntityCandidateModel, ExtractedEntityModel)
        .join(ExtractedEntityModel, EntityCandidateModel.entity_id == ExtractedEntityModel.id)
        .where(EntityCandidateModel.id.in_(payload.candidate_ids), ExtractedEntityModel.source_id == content_id)
    )).all()
    if len(rows) != len(set(payload.candidate_ids)):
        raise HTTPException(400, detail="Unknown candidate")
    stored_facts = source.guide_facts or []
    requested_fact_ids = {str(fact_id) for fact_id in payload.fact_ids}
    known_fact_ids = {str(fact.get("id")) for fact in stored_facts}
    if not requested_fact_ids.issubset(known_fact_ids):
        raise HTTPException(400, detail="Unknown guide fact")
    # Explicitly selected facts remain saved in the trip's guide source even
    # when they are trip-wide or belong to a place the user did not select.
    saved_fact_ids: set[str] = requested_fact_ids
    for candidate, entity in rows:
        if candidate.confirmed:
            continue
        poi = PoiModel(name=candidate.name, category=candidate.category, address=candidate.address,
                       latitude=candidate.latitude, longitude=candidate.longitude,
                       suggested_duration_min=candidate.duration_min,
                       provider=candidate.provider, provider_id=candidate.provider_id)
        session.add(poi)
        await session.flush()
        chosen_facts = [
            fact for fact in (source.guide_facts or [])
            if str(fact.get("id")) in {str(fact_id) for fact_id in payload.fact_ids}
            and (
                not fact.get("related_places")
                or any(str(place).casefold() == entity.name.casefold() for place in fact["related_places"])
            )
        ]
        notes = [entity.suggestion.strip()] if entity.suggestion and entity.suggestion.strip() else []
        notes.extend(
            f"{fact.get('title', '攻略提示')}：{fact.get('content', '')}"
            for fact in chosen_facts if fact.get("content")
        )
        saved_fact_ids.update(str(fact.get("id")) for fact in chosen_facts)
        session.add(WishlistItemModel(
            trip_id=source.trip_id, poi_id=poi.id, priority=3,
            notes="\n".join(dict.fromkeys(notes)) or None, source_type="CONTENT",
        ))
        candidate.confirmed = True
    source.guide_facts = [
        {**fact, "saved": str(fact.get("id")) in saved_fact_ids}
        for fact in stored_facts
    ]
    source.status = "IMPORTED"
    await session.commit()
    return await _read(session, content_id)


@router.put("/content/{content_id}/facts", response_model=ContentRead)
async def update_content_facts(content_id: UUID, payload: GuideFactsUpdate,
                               session: AsyncSession = Depends(get_session),
                               source: ContentSourceModel = Depends(get_owned_content)):
    if source.status not in {"PARTIAL", "FAILED", "NEEDS_CONFIRMATION"}:
        raise HTTPException(409, detail="Guide facts can only be edited before import confirmation")
    current = {str(item.get("id")): item for item in (source.guide_facts or [])}
    requested = [str(item.id) for item in payload.facts]
    if len(requested) != len(set(requested)) or not set(requested).issubset(current):
        raise HTTPException(400, detail="Unknown or duplicate guide fact")
    entities = (await session.scalars(select(ExtractedEntityModel).where(
        ExtractedEntityModel.source_id == content_id
    ))).all()
    place_names = {entity.name.casefold(): entity.name for entity in entities}
    updated = []
    for fact in payload.facts:
        if fact.evidence and fact.evidence not in source.text:
            raise HTTPException(400, detail="Evidence must quote the imported source")
        if any(name.casefold() not in place_names for name in fact.related_places):
            raise HTTPException(400, detail="Related place must match an extracted place")
        old = current[str(fact.id)]
        updated.append({
            "id": str(fact.id), "kind": fact.kind, "title": fact.title,
            "content": fact.content, "evidence": fact.evidence,
            "related_places": [place_names[name.casefold()] for name in fact.related_places],
            "confidence": fact.confidence, "saved": bool(old.get("saved", False)),
            "edited": True,
        })
    source.guide_facts = updated
    await session.commit()
    return await _read(session, content_id)
