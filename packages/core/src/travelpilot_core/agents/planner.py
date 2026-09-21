"""Constrained natural language preference extraction for deterministic planning."""

from typing import Literal

import httpx
from pydantic import BaseModel, ValidationError

from travelpilot_core.ports.tools import ToolFailure


class PlanningDecision(BaseModel):
    pace: Literal["relaxed", "balanced", "packed"] = "balanced"
    explanation: str = ""

    def constraints(self) -> dict[str, int]:
        return {
            "relaxed": {"max_pois_per_day": 3, "buffer_min": 120},
            "balanced": {"max_pois_per_day": 5, "buffer_min": 60},
            "packed": {"max_pois_per_day": 6, "buffer_min": 60},
        }[self.pace]


def local_decision(text: str) -> PlanningDecision:
    if any(word in text for word in ("轻松", "慢", "疲劳", "少安排")):
        return PlanningDecision(pace="relaxed", explanation="偏好轻松节奏")
    if any(word in text for word in ("紧凑", "多安排", "赶")):
        return PlanningDecision(pace="packed", explanation="偏好紧凑节奏")
    return PlanningDecision(pace="balanced", explanation="使用均衡节奏")


async def decide_preferences(text: str, *, api_key: str = "", model: str = "",
                             base_url: str = "", token_budget: int = 500) -> tuple[PlanningDecision, int]:
    if not api_key or not model:
        return local_decision(text), 0
    try:
        async with httpx.AsyncClient(timeout=12) as client:
            response = await client.post(base_url.rstrip("/") + "/chat/completions",
                headers={"Authorization": f"Bearer {api_key}"}, json={
                    "model": model, "response_format": {"type": "json_object"},
                    "max_tokens": min(256, token_budget), "temperature": 0,
                    "messages": [
                        {"role": "system", "content": "Return JSON with pace: relaxed, balanced, or packed; and short explanation. Do not choose places, routes, or override constraints."},
                        {"role": "user", "content": text[:2000]},
                    ],
                })
            response.raise_for_status()
            body = response.json()
            usage = int(body.get("usage", {}).get("total_tokens", 0))
            if usage > token_budget:
                raise ToolFailure("TOKEN_BUDGET_EXCEEDED")
            return PlanningDecision.model_validate_json(body["choices"][0]["message"]["content"]), usage
    except (httpx.HTTPError, KeyError, IndexError, TypeError, ValueError, ValidationError) as exc:
        raise ToolFailure("PLANNER_DECISION_FAILED") from exc
