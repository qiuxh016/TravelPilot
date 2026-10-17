import asyncio
import logging
from uuid import UUID

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from travelpilot_core.agents.extraction import extract_places
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
        extraction = await extract_places(payload.text, api_key=settings.llm_api_key,
                                          model=settings.llm_model, base_url=settings.llm_base_url,
                                          additional_instructions=skill.instructions if skill else "")
        source.token_usage = extraction.token_usage
        for mention in extraction.places:
            evidence = mention.evidence if mention.evidence and mention.evidence in payload.text else None
            entity = ExtractedEntityModel(source_id=source.id, name=mention.name,
                                          category=mention.category, suggestion=mention.suggestion,
                                          evidence=evidence, confidence=mention.confidence)
            session.add(entity)
            await session.flush()
            places = await maps.search_place(mention.name)
            for place in places[:5]:
                session.add(EntityCandidateModel(
                    entity_id=entity.id, provider=place.provider, provider_id=place.provider_id,
                    name=place.name, address=place.address, latitude=place.latitude,
                    longitude=place.longitude, category=place.category,
                    duration_min=place.suggested_duration_min,
                    score=1.0 if place.name == mention.name else 0.7, confirmed=False,
                ))
        source.status = "NEEDS_CONFIRMATION"
    except (MapsProviderError, RuntimeError):
        source.status = "FAILED"
        source.error = "内容解析或地点查询暂时不可用"
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
    for candidate, entity in rows:
        if candidate.confirmed:
            continue
        poi = PoiModel(name=candidate.name, category=candidate.category, address=candidate.address,
                       latitude=candidate.latitude, longitude=candidate.longitude,
                       suggested_duration_min=candidate.duration_min,
                       provider=candidate.provider, provider_id=candidate.provider_id)
        session.add(poi)
        await session.flush()
        session.add(WishlistItemModel(trip_id=source.trip_id, poi_id=poi.id, priority=3,
                                      notes=entity.suggestion, source_type="CONTENT"))
        candidate.confirmed = True
    source.status = "IMPORTED"
    await session.commit()
    return await _read(session, content_id)
