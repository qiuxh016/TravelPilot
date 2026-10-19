"""Short-lived, owner-scoped cache for completed Advisor intent extraction."""

import hashlib
import json
import logging
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import urlsplit
from uuid import UUID, uuid4

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession
from travelpilot_core.agents.extraction import Extraction
from travelpilot_infra.db.models.advisor import AdvisorRunEventModel
from travelpilot_infra.db.session import AsyncSessionFactory

logger = logging.getLogger(__name__)
_PHASE = "intent_recovery"
_OPERATION = "advisor_intent_result"
_TTL_SECONDS = 600
_SCHEMA_VERSION = "advisor_intent_checkpoint_v1"


def _digest(value: Any) -> str:
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()[:32]


def _preference_scope(personal_memories: list[dict] | None) -> list[str]:
    return [
        memory["text"][:160]
        for memory in (personal_memories or [])
        if isinstance(memory, dict)
        and memory.get("constraint_type") == "PREFERENCE"
        and isinstance(memory.get("text"), str)
        and memory["text"].strip()
    ][:5]


def intent_checkpoint_scope_ref(
    *,
    goal: str,
    conversation_context: list[dict[str, str]] | None,
    personal_memories: list[dict] | None,
    context_revision: int,
    token_budget: int,
    model: str,
    base_url: str,
) -> str:
    endpoint = urlsplit(base_url)
    material = {
        "schema": _SCHEMA_VERSION,
        "goal": goal[:8000],
        "conversation": [
            {"role": item.get("role"), "content": item.get("content", "")[-400:]}
            for item in (conversation_context or [])[-6:]
            if isinstance(item, dict) and item.get("role") in {"user", "assistant"}
            and isinstance(item.get("content"), str)
        ],
        "preferences": _preference_scope(personal_memories),
        "context_revision": context_revision,
        "token_budget": token_budget,
        "model": model[:120],
        "endpoint": {"scheme": endpoint.scheme, "host": endpoint.hostname, "path": endpoint.path},
    }
    return _digest(material)


def _serialize_extraction(extraction: Extraction, goal: str) -> dict[str, Any] | None:
    places = []
    for item in extraction.places:
        if item.suggestion is not None and len(item.suggestion) > 200:
            return None
        evidence_span = None
        if item.evidence:
            start = goal.find(item.evidence)
            if start < 0 or len(item.evidence) > 500:
                return None
            evidence_span = [start, start + len(item.evidence)]
        places.append({
            "name": item.name,
            "category": item.category,
            "suggestion": item.suggestion,
            "confidence": item.confidence,
            "action": item.action,
            # Store only offsets; the user's verbatim text remains in the message row.
            "evidence_span": evidence_span,
        })
    if any(not isinstance(query, str) or len(query) > 160 for query in extraction.search_queries):
        return None
    return {
        "places": places,
        "search_queries": extraction.search_queries,
        "intent": extraction.intent,
        "pace_adjustment": extraction.pace_adjustment,
        "total_tokens": extraction.token_usage,
        "output_tokens": extraction.output_tokens,
    }


def _deserialize_extraction(payload: Any, goal: str) -> Extraction | None:
    if not isinstance(payload, dict):
        return None
    records = payload.get("places")
    if not isinstance(records, list) or len(records) > 20:
        return None
    places = []
    for record in records:
        if not isinstance(record, dict):
            return None
        span = record.get("evidence_span")
        evidence = None
        if span is not None:
            if (
                not isinstance(span, list) or len(span) != 2
                or any(isinstance(value, bool) or not isinstance(value, int) for value in span)
            ):
                return None
            start, end = span
            if not 0 <= start < end <= len(goal) or end - start > 500:
                return None
            evidence = goal[start:end]
        places.append({
            "name": record.get("name"),
            "category": record.get("category"),
            "suggestion": record.get("suggestion"),
            "confidence": record.get("confidence"),
            "action": record.get("action"),
            "evidence": evidence,
        })
    queries = payload.get("search_queries")
    total_tokens = payload.get("total_tokens")
    output_tokens = payload.get("output_tokens")
    if (
        not isinstance(queries, list) or len(queries) > 5
        or any(not isinstance(item, str) or len(item) > 160 for item in queries)
        or isinstance(total_tokens, bool) or not isinstance(total_tokens, int) or total_tokens < 0
        or isinstance(output_tokens, bool) or not isinstance(output_tokens, int) or output_tokens < 0
    ):
        return None
    try:
        return Extraction.model_validate({
            "places": places,
            "search_queries": queries,
            "intent": payload.get("intent"),
            "pace_adjustment": payload.get("pace_adjustment"),
            "token_usage": total_tokens,
            "output_tokens": output_tokens,
        })
    except (TypeError, ValueError):
        return None


async def load_intent_checkpoint(
    db: AsyncSession,
    *,
    owner_id: UUID,
    session_id: UUID,
    task_id: str,
    scope_ref: str,
    goal: str,
) -> Extraction | None:
    task_ref = _digest(task_id)
    rows = await db.scalars(
        select(AdvisorRunEventModel)
        .where(
            AdvisorRunEventModel.owner_id == owner_id,
            AdvisorRunEventModel.session_id == session_id,
            AdvisorRunEventModel.phase == _PHASE,
            AdvisorRunEventModel.operation == _OPERATION,
            AdvisorRunEventModel.metadata_json["task_ref"].as_string() == task_ref,
            AdvisorRunEventModel.metadata_json["scope_ref"].as_string() == scope_ref,
        )
        .order_by(AdvisorRunEventModel.created_at.desc())
        .limit(5)
    )
    now = datetime.now(UTC)
    for row in rows.all():
        metadata = row.metadata_json if isinstance(row.metadata_json, dict) else {}
        created_at = row.created_at
        if not isinstance(created_at, datetime) or created_at.tzinfo is None:
            continue
        age = (now - created_at.astimezone(UTC)).total_seconds()
        if (
            not 0 <= age <= _TTL_SECONDS
            or metadata.get("task_ref") != task_ref
            or metadata.get("scope_ref") != scope_ref
        ):
            continue
        restored = _deserialize_extraction(metadata.get("extraction"), goal)
        if restored is not None:
            return restored
    return None


async def save_intent_checkpoint(
    *,
    owner_id: UUID,
    session_id: UUID,
    task_id: str,
    scope_ref: str,
    extraction: Extraction,
    goal: str,
    context_revision: int,
) -> bool:
    payload = _serialize_extraction(extraction, goal)
    if payload is None:
        return False
    task_ref = _digest(task_id)
    metadata = {
        "task_ref": task_ref,
        "scope_ref": scope_ref,
        "attempt_ref": uuid4().hex[:24],
        "context_revision": max(0, context_revision),
        "extraction": payload,
    }
    try:
        async with AsyncSessionFactory() as session:
            await session.execute(delete(AdvisorRunEventModel).where(
                AdvisorRunEventModel.phase == _PHASE,
                AdvisorRunEventModel.operation == _OPERATION,
                AdvisorRunEventModel.created_at < datetime.now(UTC) - timedelta(seconds=_TTL_SECONDS),
            ))
            await session.execute(delete(AdvisorRunEventModel).where(
                AdvisorRunEventModel.owner_id == owner_id,
                AdvisorRunEventModel.session_id == session_id,
                AdvisorRunEventModel.phase == _PHASE,
                AdvisorRunEventModel.operation == _OPERATION,
                AdvisorRunEventModel.metadata_json["task_ref"].as_string() == task_ref,
            ))
            session.add(AdvisorRunEventModel(
                owner_id=owner_id,
                session_id=session_id,
                phase=_PHASE,
                operation=_OPERATION,
                status="COMPLETED",
                token_usage=extraction.token_usage,
                metadata_json=metadata,
            ))
            await session.commit()
        return True
    except Exception:
        logger.exception("Could not persist Advisor intent checkpoint")
        return False


async def delete_intent_checkpoint(
    db: AsyncSession,
    *,
    owner_id: UUID,
    session_id: UUID,
    task_id: str,
) -> None:
    """Consume a successful turn's extraction checkpoint in its commit transaction."""
    await db.execute(delete(AdvisorRunEventModel).where(
        AdvisorRunEventModel.owner_id == owner_id,
        AdvisorRunEventModel.session_id == session_id,
        AdvisorRunEventModel.phase == _PHASE,
        AdvisorRunEventModel.operation == _OPERATION,
        AdvisorRunEventModel.metadata_json["task_ref"].as_string() == _digest(task_id),
    ))
