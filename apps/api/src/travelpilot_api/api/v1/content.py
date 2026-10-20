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
    EntityRead,
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
        guide_facts=source.guide_facts or [],
        created_at=source.created_at, entities=result,
    )


@router.post("/trips/{trip_id}/content", response_model=ContentRead, status_code=status.HTTP_201_CREATED)
async def create_content(trip_id: UUID, payload: ContentCreate,
                         session: AsyncSession = Depends(get_session), maps=Depends(get_maps_provider),
                         _trip: TripModel = Depends(get_owned_trip)):
    source = ContentSourceModel(trip_id=trip_id, content_type=payload.content_type,
                                text=payload.text, status="PARSING")
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
        thinking_mode = (
            "disabled"
            if urlsplit(settings.llm_base_url).hostname == "api.deepseek.com"
            else None
        )
        extraction = await extract_places(payload.text, api_key=settings.llm_api_key,
                                          model=settings.llm_model, base_url=settings.llm_base_url,
                                          token_budget=settings.llm_max_tokens_per_turn,
                                          additional_instructions=skill.instructions if skill else "",
                                          thinking_mode=thinking_mode, guide_import=True)
        source.token_usage = extraction.token_usage
        source.guide_facts = [
            {
                "id": str(uuid4()), "kind": fact.kind, "title": fact.title,
                "content": fact.content, "evidence": fact.evidence,
                "related_places": fact.related_places, "confidence": fact.confidence,
            }
            for fact in extraction.guide_facts
        ]
        map_unavailable = False
        for mention in extraction.places:
            evidence = mention.evidence if mention.evidence and mention.evidence in payload.text else None
            entity = ExtractedEntityModel(source_id=source.id, name=mention.name,
                                          category=_guide_place_category(mention.category, mention.name),
                                          suggestion=mention.suggestion,
                                          evidence=evidence, confidence=mention.confidence)
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
        source.status = "NEEDS_CONFIRMATION" if extraction.places or extraction.guide_facts else "FAILED"
        if map_unavailable:
            source.error = "攻略内容已识别，但地图地点匹配暂时不可用；攻略细节已保留，可稍后重新导入匹配地点。"
        elif source.status == "FAILED":
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
        if failure_code == "INPUT_TOO_LONG":
            source.error = "攻略超过 20,000 字符的导入上限；请拆分后分别导入。"
        elif failure_code == "RESULT_LIMIT_EXCEEDED":
            source.error = "攻略信息量超过单次确认上限；请按行程日期或主题拆分后导入。"
        elif (exc.provider_metadata or {}).get("finish_reason") == "length":
            source.error = "模型输出达到长度上限，攻略尚未解析完成。请缩短攻略内容后重试。"
        else:
            source.error = "攻略内容解析服务暂时不可用；请稍后重试。"
    except RuntimeError as exc:
        logger.warning("content_text_extraction_runtime_failed source_id=%s error_type=%s",
                       source.id, type(exc).__name__)
        source.status = "FAILED"
        source.error = "攻略内容解析暂时不可用；识别未完成，请稍后重试。"
    await session.commit()
    return await _read(session, source.id)


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
    saved_fact_ids: set[str] = set()
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
