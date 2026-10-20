"""Bounded dialogue context for multi-turn Advisor responses."""

import math
from typing import Any


def recent_conversation_context(messages: list[Any], *, limit: int = 6) -> list[dict[str, str]]:
    """Return recent visible turns only; omit payloads and bound every message."""
    result: list[dict[str, str]] = []
    for message in messages:
        role = getattr(message, "role", None)
        content = getattr(message, "content", None)
        if role not in {"user", "assistant"} or not isinstance(content, str):
            continue
        text = content.strip()
        if not text:
            continue
        result.append({"role": role, "content": text[-400:]})
    return result[-max(0, limit):]


def recent_verified_candidate_context(messages: list[Any], *, limit: int = 8) -> list[dict[str, Any]]:
    """Reuse only the latest assistant message's verified candidates for grounded follow-ups."""
    bounded_limit = max(0, min(limit, 8))
    if bounded_limit == 0:
        return []
    for message in reversed(messages):
        if getattr(message, "role", None) != "assistant":
            continue
        payload = getattr(message, "payload", None)
        if not isinstance(payload, dict) or "verified_candidates" not in payload:
            # The newest assistant turn supersedes older candidate evidence even
            # when it predates the evidence field or did not perform a search.
            return []
        raw_candidates = payload.get("verified_candidates")
        if not isinstance(raw_candidates, list):
            return []
        candidates = []
        for item in raw_candidates:
            if not isinstance(item, dict):
                continue
            name = item.get("name")
            provider = item.get("provider")
            verification_status = item.get("verification_status")
            if (
                not isinstance(name, str) or not name.strip() or len(name) > 160
                or provider not in {"amap", "fake"}
                or verification_status not in {"VERIFIED", "SIMULATED"}
            ):
                continue
            candidate = {
                "name": name.strip(),
                "provider": provider,
                "verification_status": verification_status,
                "evidence_scope": "PRIOR_TURN",
            }
            planning_category = item.get("planning_category")
            if planning_category in {"FOOD", "LODGING", "ATTRACTION"}:
                candidate["planning_category"] = planning_category
            meal_type = item.get("meal_type")
            if meal_type in {"BREAKFAST", "LUNCH", "DINNER", "SNACK"}:
                candidate["meal_type"] = meal_type
            for field, max_length in (("address", 240), ("category", 120)):
                value = item.get(field)
                if isinstance(value, str) and value.strip():
                    candidate[field] = value.strip()[:max_length]
            score = item.get("popularity_score")
            if (
                isinstance(score, (int, float)) and not isinstance(score, bool)
                and math.isfinite(score)
                and 0 <= score <= 10
            ):
                candidate["popularity_score"] = float(score)
            guide_references = item.get("guide_references")
            if isinstance(guide_references, list):
                bounded_references = []
                for reference in guide_references[:3]:
                    if not isinstance(reference, dict):
                        continue
                    source_id = reference.get("source_id")
                    title = reference.get("title")
                    guide_claim = reference.get("guide_claim")
                    if all(isinstance(value, str) and value.strip() for value in (source_id, title, guide_claim)):
                        bounded_references.append({
                            "source_id": source_id[:64], "title": title[:80],
                            "guide_claim": guide_claim[:180],
                            "evidence_status": reference.get("evidence_status")
                            if reference.get("evidence_status") in {"IMPORTED_GUIDE", "PARTIAL_GUIDE"}
                            else "IMPORTED_GUIDE",
                        })
                if bounded_references:
                    candidate["guide_references"] = bounded_references
            candidates.append(candidate)
            if len(candidates) >= bounded_limit:
                break
        # An explicitly empty result from the latest search supersedes older
        # candidates; never resurrect options from an earlier unrelated query.
        return candidates
    return []
