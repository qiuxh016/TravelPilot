"""Persist bounded, privacy-safe Advisor execution checkpoints."""

import hashlib
import logging
from typing import Any
from uuid import UUID, uuid4

from travelpilot_infra.db.models.advisor import AdvisorRunEventModel
from travelpilot_infra.db.session import AsyncSessionFactory

logger = logging.getLogger(__name__)


def checkpoint_writer(
    *, owner_id: UUID, session_id: UUID, task_id: str, phase: str,
    context_revision: int | None = None,
):
    """Write safe Agent loop state in independent transactions."""
    task_ref = hashlib.sha256(task_id.encode("utf-8")).hexdigest()[:24]
    attempt_ref = uuid4().hex[:24]

    async def write(state: dict[str, Any]) -> None:
        metadata: dict[str, Any] = {"task_ref": task_ref, "attempt_ref": attempt_ref}
        if isinstance(context_revision, int) and not isinstance(context_revision, bool):
            metadata["context_revision"] = max(0, context_revision)
        for key in ("state", "transition", "tool"):
            value = state.get(key)
            if isinstance(value, (str, int)) and not isinstance(value, bool):
                metadata[key] = value[:80] if isinstance(value, str) else value
        error_code = state.get("error_code", state.get("error", state.get("reason")))
        if isinstance(error_code, str):
            metadata["error_code"] = error_code[:80]
        keys = state.get("observation_keys")
        if isinstance(keys, list):
            metadata["observation_keys"] = [x[:80] for x in keys[:20] if isinstance(x, str)]
        stages = {"SEARCH_WEATHER", "SCHEDULE", "ROUTE"}
        eligible = state.get("eligible_stages")
        order = state.get("selected_order")
        if (
            isinstance(eligible, list)
            and isinstance(order, list)
            and len(eligible) <= 3
            and len(order) <= 3
            and all(isinstance(item, str) and item in stages for item in eligible)
            and all(isinstance(item, str) and item in stages for item in order)
            and len(set(eligible)) == len(eligible)
            and len(set(order)) == len(order)
            and set(eligible) == set(order)
        ):
            metadata["eligible_stages"] = eligible
            metadata["selected_order"] = order
            source = state.get("decision_source")
            if isinstance(source, str) and source in {
                "MODEL", "RULE", "RUNTIME_GUARD", "RULE_RECOVERY",
            }:
                metadata["decision_source"] = source
        try:
            async with AsyncSessionFactory() as session:
                session.add(AdvisorRunEventModel(
                    owner_id=owner_id, session_id=session_id, phase=phase[:32],
                    operation="advisor_agent_loop",
                    status=str(metadata.get("state", "UNKNOWN"))[:24], metadata_json=metadata,
                ))
                await session.commit()
        except Exception:
            logger.exception("Could not persist Advisor checkpoint")

    return write
