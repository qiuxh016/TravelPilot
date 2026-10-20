"""Bounded, user-owned imported-guide evidence for Advisor recommendations."""

import re
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from travelpilot_infra.db.models.workflow import ContentSourceModel, ExtractedEntityModel


def _normalize_place_name(value: str) -> str:
    return re.sub(r"[^\w\u4e00-\u9fff]", "", value.casefold())


def _source_title(text: str) -> str:
    first_line = next((line.strip() for line in text.splitlines() if line.strip()), "旅行攻略")
    return re.sub(r"^[\W_\d]+", "", first_line)[:80] or "旅行攻略"


async def load_trip_guide_references(
    db: AsyncSession, trip_id: UUID | None,
) -> dict[str, list[dict[str, Any]]]:
    """Load only extracted entity evidence from usable guides for the selected trip."""
    if trip_id is None:
        return {}
    rows = (await db.execute(
        select(
            ContentSourceModel.id,
            ContentSourceModel.text,
            ContentSourceModel.status,
            ExtractedEntityModel.name,
            ExtractedEntityModel.category,
            ExtractedEntityModel.suggestion,
            ExtractedEntityModel.evidence,
        )
        .join(ExtractedEntityModel, ExtractedEntityModel.source_id == ContentSourceModel.id)
        .where(
            ContentSourceModel.trip_id == trip_id,
            ContentSourceModel.status.in_(("IMPORTED", "PARTIAL")),
        )
        .order_by(ContentSourceModel.created_at.desc())
        .limit(160)
    )).all()
    result: dict[str, list[dict[str, Any]]] = {}
    for source_id, source_text, status, name, category, suggestion, evidence in rows:
        key = _normalize_place_name(name)
        if not key:
            continue
        bucket = result.setdefault(key, [])
        reference = {
            "source_id": str(source_id),
            "title": _source_title(source_text),
            "place_name": name,
            "category": category,
            "guide_claim": (suggestion or evidence or "攻略中提到此地点")[:180],
            "evidence_status": "PARTIAL_GUIDE" if status == "PARTIAL" else "IMPORTED_GUIDE",
        }
        if not any(item["source_id"] == reference["source_id"] for item in bucket):
            bucket.append(reference)
            del bucket[3:]
    return result


def guide_references_for_place(
    name: str, references_by_name: dict[str, list[dict[str, Any]]],
) -> list[dict[str, Any]]:
    """Match provider names to guide entities conservatively, allowing aliases by containment."""
    normalized = _normalize_place_name(name)
    if not normalized:
        return []
    exact = references_by_name.get(normalized)
    if exact:
        return exact[:3]
    matches: list[dict[str, Any]] = []
    for entity_name, references in references_by_name.items():
        if min(len(entity_name), len(normalized)) >= 4 and (
            entity_name in normalized or normalized in entity_name
        ):
            matches.extend(references[:2])
    unique: dict[str, dict[str, Any]] = {}
    for reference in matches:
        unique.setdefault(reference["source_id"], reference)
    return list(unique.values())[:3]
