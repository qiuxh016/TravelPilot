"""Constrained natural language preference extraction for deterministic planning."""

import json
from dataclasses import dataclass
from typing import Literal
from urllib.parse import urlparse

import httpx
from pydantic import BaseModel, ValidationError

from travelpilot_core.agents.loop import AgentAction
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


@dataclass(frozen=True)
class TokenUsage:
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0


class PlannerDecisionFailure(ToolFailure):
    def __init__(self, code: str, usage: TokenUsage, reason: str | None = None):
        super().__init__(code)
        self.usage = usage
        self.reason = reason


def local_decision(text: str) -> PlanningDecision:
    if any(word in text for word in ("轻松", "慢", "疲劳", "少安排")):
        return PlanningDecision(pace="relaxed", explanation="偏好轻松节奏")
    if any(word in text for word in ("紧凑", "多安排", "赶")):
        return PlanningDecision(pace="packed", explanation="偏好紧凑节奏")
    return PlanningDecision(pace="balanced", explanation="使用均衡节奏")


async def decide_preferences(text: str, *, api_key: str = "", model: str = "",
                             base_url: str = "", token_budget: int = 500) -> tuple[PlanningDecision, TokenUsage]:
    if not api_key or not model:
        return local_decision(text), TokenUsage()
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
            raw_usage = body.get("usage", {})
            prompt_tokens = int(raw_usage.get("prompt_tokens", 0))
            completion_tokens = int(raw_usage.get("completion_tokens", 0))
            total_tokens = int(raw_usage.get("total_tokens", prompt_tokens + completion_tokens))
            if prompt_tokens == 0 and total_tokens >= completion_tokens:
                prompt_tokens = total_tokens - completion_tokens
            usage = TokenUsage(prompt_tokens, completion_tokens, total_tokens)
            try:
                decision = PlanningDecision.model_validate_json(body["choices"][0]["message"]["content"])
            except (KeyError, IndexError, TypeError, ValueError, ValidationError) as exc:
                raise PlannerDecisionFailure("PLANNER_DECISION_FAILED", usage) from exc
            return decision, usage
    except httpx.TimeoutException as exc:
        raise ToolFailure("PLANNER_TIMEOUT") from exc
    except httpx.HTTPError as exc:
        raise ToolFailure("PLANNER_PROVIDER_ERROR") from exc
    except (KeyError, IndexError, TypeError, ValueError) as exc:
        raise ToolFailure("PLANNER_DECISION_FAILED") from exc


async def decide_next_action(
    observation: dict, tool_schemas: dict[str, dict], *, api_key: str = "", model: str = "",
    base_url: str = "", token_budget: int = 128,
) -> tuple[AgentAction, TokenUsage]:
    """Choose one action from caller-provided read-only tools and finish."""
    if not api_key or not model:
        tool_name = "get_trip_context" if "get_trip_context" in tool_schemas and "get_trip_context" not in observation else None
        if tool_name:
            return AgentAction(kind="CALL_TOOL", tool=tool_name,
                               arguments={"trip_id": observation.get("trip_id")}), TokenUsage()
        matrix = observation.get("get_trip_context", {}).get("points", [])
        if len(matrix) > 1 and "get_distance_matrix" in tool_schemas and "get_distance_matrix" not in observation:
            return AgentAction(kind="CALL_TOOL", tool="get_distance_matrix", arguments={"points": matrix}), TokenUsage()
        return AgentAction(kind="FINISH", summary="deterministic fallback"), TokenUsage()
    tool_contracts = {
        name: schema.get("input_schema", {}) for name, schema in tool_schemas.items()
    }
    prompt = {
        "observation": observation,
        "allowed_tools": tool_contracts,
        "allowed_actions": ["CALL_TOOL", "ASK_USER", "FINISH"],
    }
    try:
        body = {
                    "model": model, "response_format": {"type": "json_object"},
                    # Callers reserve against the turn budget before invoking the
                    # model. Honor that reservation (up to a small absolute cap)
                    # so structured actions are not silently truncated at 128.
                    "max_tokens": min(512, token_budget), "temperature": 0,
                    "messages": [
                        {"role": "system", "content": (
                            "Select exactly one next action using only the supplied observations and tools. "
                            "Output one minimal JSON object and no prose. For CALL_TOOL include only kind, tool, and arguments; "
                            "omit summary. For ASK_USER include only kind and one concise question. For FINISH include kind "
                            "and a summary of at most 80 characters. Do not put analysis or full travel advice in action fields; "
                            "the user-facing answer is composed separately. Examples: "
                            '{"kind":"CALL_TOOL","tool":"get_distance_matrix","arguments":{}}; '
                            '{"kind":"ASK_USER","question":"Which date?"}; '
                            '{"kind":"FINISH","summary":"Ready for review"}. '
                            "Use ASK_USER when a required user choice or feasibility-critical fact is missing; "
                            "do not use FINISH to replace a needed question. Use FINISH only when the requested "
                            "read-only work is complete, or when you must safely explain a blocker and no useful "
                            "user decision can unblock it. Never invent tools or commit, delete, share, or publish data."
                        )},
                        {"role": "user", "content": json.dumps(prompt, ensure_ascii=False, separators=(",", ":"))[:4000]},
                    ],
                }
        if urlparse(base_url).hostname == "api.deepseek.com":
            body["thinking"] = {"type": "disabled"}
        async with httpx.AsyncClient(timeout=12) as client:
            response = await client.post(
                base_url.rstrip("/") + "/chat/completions",
                headers={"Authorization": f"Bearer {api_key}"}, json=body,
            )
            response.raise_for_status()
            body = response.json()
            raw_usage = body.get("usage", {})
            prompt_tokens = int(raw_usage.get("prompt_tokens", 0))
            completion_tokens = int(raw_usage.get("completion_tokens", 0))
            total_tokens = int(raw_usage.get("total_tokens", prompt_tokens + completion_tokens))
            if prompt_tokens == 0 and total_tokens >= completion_tokens:
                prompt_tokens = total_tokens - completion_tokens
            usage = TokenUsage(prompt_tokens, completion_tokens, total_tokens)
            try:
                choice = body["choices"][0]
                content = choice["message"]["content"]
            except (KeyError, IndexError, TypeError) as exc:
                raise PlannerDecisionFailure("AGENT_ACTION_INVALID", usage, "MESSAGE_CONTENT_MISSING") from exc
            if not isinstance(content, str):
                raise PlannerDecisionFailure("AGENT_ACTION_INVALID", usage, "MESSAGE_CONTENT_NOT_TEXT")
            try:
                payload = json.loads(content)
            except (json.JSONDecodeError, TypeError) as exc:
                reason = (
                    "MESSAGE_CONTENT_TRUNCATED"
                    if choice.get("finish_reason") == "length"
                    else "MESSAGE_CONTENT_NOT_JSON"
                )
                raise PlannerDecisionFailure("AGENT_ACTION_INVALID", usage, reason) from exc
            try:
                action = AgentAction.model_validate(payload)
            except (ValidationError, TypeError, ValueError) as exc:
                raise PlannerDecisionFailure("AGENT_ACTION_INVALID", usage, "ACTION_SCHEMA_INVALID") from exc
            if action.kind == "CALL_TOOL" and action.tool not in tool_schemas:
                raise PlannerDecisionFailure("AGENT_ACTION_INVALID", usage, "TOOL_NOT_ALLOWLISTED")
            return action, usage
    except httpx.TimeoutException as exc:
        raise ToolFailure("PLANNER_TIMEOUT") from exc
    except httpx.HTTPError as exc:
        raise ToolFailure("PLANNER_PROVIDER_ERROR") from exc
    except (KeyError, IndexError, TypeError, ValueError) as exc:
        raise ToolFailure("AGENT_ACTION_INVALID") from exc
