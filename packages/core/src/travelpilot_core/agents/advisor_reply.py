"""Grounded, structured response composition for advisor turns."""

import json
import re
from dataclasses import dataclass
from typing import Literal
from urllib.parse import urlsplit

import httpx
from pydantic import BaseModel, Field, ValidationError


class AdvisorMemorySuggestion(BaseModel):
    category: Literal["PACE", "INTEREST", "FOOD", "LODGING", "TRANSPORT", "GENERAL"]
    text: str = Field(min_length=2, max_length=240)
    evidence: str = Field(min_length=2, max_length=180)


class AdvisorReply(BaseModel):
    reply: str = Field(min_length=1, max_length=1200)
    follow_up_question: str | None = Field(default=None, max_length=300)
    memory_suggestion: AdvisorMemorySuggestion | None = None


@dataclass(frozen=True)
class AdvisorReplyResult:
    reply: AdvisorReply | None
    total_tokens: int
    output_tokens: int
    error_code: str | None = None


def _has_unsupported_removal_claim(reply: str, facts: dict) -> bool:
    """Reject claims that a selected place was removed without server evidence."""
    applied = facts.get("adjustments_applied", [])
    applied_text = " ".join(item for item in applied if isinstance(item, str))
    places = [
        *facts.get("selected_places", []),
        *facts.get("known_trip_places", []),
    ]
    current_plan = facts.get("current_plan")
    if isinstance(current_plan, dict):
        places = [*places, *current_plan.get("stops", [])]
    places = [*places, *facts.get("verified_candidates", [])]
    claim = re.compile(r"(?:已|已经).{0,12}(?:移除|删除|删掉|去掉|取消)|(?:移除了|删除了|删掉了|去掉了)")
    for place in places:
        name = place.get("name") if isinstance(place, dict) else None
        if not isinstance(name, str) or len(name) < 2 or name not in reply:
            continue
        start = max(0, reply.find(name) - 24)
        end = min(len(reply), reply.find(name) + len(name) + 24)
        if claim.search(reply[start:end]) and name not in applied_text:
            return True
    return False


async def compose_advisor_reply(
    facts: dict,
    *,
    api_key: str = "",
    model: str = "",
    base_url: str = "",
    token_budget: int = 400,
) -> tuple[AdvisorReply | None, int, int]:
    """Compose a short response from supplied facts; never execute tools or mutate data."""
    result = await compose_advisor_reply_with_diagnostics(
        facts, api_key=api_key, model=model, base_url=base_url, token_budget=token_budget,
    )
    return result.reply, result.total_tokens, result.output_tokens


async def compose_advisor_reply_with_diagnostics(
    facts: dict,
    *,
    api_key: str = "",
    model: str = "",
    base_url: str = "",
    token_budget: int = 400,
) -> AdvisorReplyResult:
    """Compose a grounded answer and return a safe, non-sensitive failure code."""
    if not api_key or not model or token_budget < 64:
        return AdvisorReplyResult(None, 0, 0, "NOT_CONFIGURED" if not api_key or not model else "TOKEN_BUDGET_TOO_SMALL")
    try:
        async with httpx.AsyncClient(timeout=12) as client:
            request_body = {
                "model": model,
                "response_format": {"type": "json_object"},
                "max_tokens": min(640, token_budget),
                "temperature": 0.2,
                "messages": [
                    {
                        "role": "system",
                        "content": (
                            "You are a helpful Chinese travel advisor. Return JSON with reply, "
                            "optional follow_up_question, and memory_suggestion (an object or null). "
                            "Only suggest a memory when memory_suggestions_enabled is true and the user "
                            "clearly states a stable, non-sensitive travel preference that could help future trips. "
                            "Return category, a short faithful summary and an exact short quote from current_message "
                            "as evidence; the quote must be copied verbatim. Never infer sensitive traits, "
                            "never decide that something is a hard limit, and return null for one-trip requests, "
                            "uncertain preferences, or an explicit refusal to remember. A suggestion is only a "
                            "draft; the user must choose its meaning and confirm it before storage. "
                            "Answer the user's actual question first, "
                            "including short replies that continue a prior question; resolve them using recent_conversation "
                            "and pending task context instead of demanding a fixed answer format. Keep reply concise: "
                            "normally one to three short sentences, with only essential facts or one focused question. "
                            "Prefer selectable options over a long explanation when the next step is a simple choice. "
                            "Use a warm but practical tone. You may "
                            "give clearly general travel advice from common knowledge. Treat supplied "
                            "context as the source of truth for this user's trip. Never invent live "
                            "weather, route times, prices, opening hours, booking availability, or map "
                            "results; when those are not provided, say they need checking. Mention a "
                            "specific place only if it appears in verified_candidates or current_plan. "
                            "Candidates marked CURRENT_TURN are evidence from this turn; candidates marked "
                            "PRIOR_TURN may be used to compare the previously discussed options, but do not "
                            "treat prior-turn evidence as current opening hours, weather, prices, availability, "
                            "or route conditions. If a candidate has guide_references, you may mention that "
                            "the imported guide with the supplied title mentioned it; label this as a guide "
                            "claim, not map verification or a current fact. Do not invent guide details. "
                            "Use only the supplied candidate fields. "
                            "Distinguish general suggestions from verified trip facts. Ask at most one "
                            "focused question, and only when the answer materially changes the next step. "
                            "Do not claim a plan was saved or changed; confirmation is a separate action. "
                            "For selected-place changes, treat selected_places and adjustments_applied as "
                            "the authoritative state. Never say a place was added or removed unless that "
                            "exact change appears in adjustments_applied. known_trip_places is the complete "
                            "bounded list of places in this trip; do not invent another place from memory."
                            " When the user asks how to schedule or optimize time and current_plan is supplied, "
                            "discuss those existing stops directly; do not ask them to name places already in the plan. "
                            "Describe a generated schedule as a proposal that needs confirmation. "
                            "When verified_candidates is supplied, synthesize a useful comparison or "
                            "recommendation for the user's actual question instead of merely repeating names. "
                            "Use only candidate facts present in the input. If requested_category is FOOD or "
                            "LODGING, discuss only candidates with that planning_category and never drift into "
                            "attractions or other categories. If requested_meal_type is present, keep the "
                            "recommendation aligned to that meal. If candidates lack menus, prices, hours, "
                            "route evidence or live popularity evidence, say that those details are unknown; "
                            "do not infer them from a candidate name or category. Clearly identify any general "
                            "travel advice as general advice, separate from verified candidate facts. "
                            "For destination discovery without a selected city, answer broadly first, then ask "
                            "one small preference question instead of requiring a complete travel form."
                            " When recent_conversation is supplied, use it to resolve references in the latest "
                            "message (for example, 'which one is better?'). Prioritize the latest request; older "
                            "messages must not override newer preferences or verified trip facts."
                            " If trip_completeness_audit is present, use it to answer what is missing, "
                            "group related gaps briefly, and offer the user a choice to get a recommendation, "
                            "import their guide, handle it later, or mark it unnecessary. Do not imply a missing "
                            "item is mandatory. Treat confirmed_personal_memories as optional, user-approved "
                            "personalization hints, not facts about the current trip. The user's latest explicit "
                            "request and current trip data take precedence over older preference memories. For a "
                            "clearly conflicting HARD_LIMIT memory, do not silently recommend the conflict; "
                            "surface it and ask the user when intent is ambiguous. The server-side draft verifier "
                            "remains authoritative for hard-limit conflicts."
                        ),
                    },
                    {"role": "user", "content": json.dumps(facts, ensure_ascii=False)},
                ],
            }
            if urlsplit(base_url).hostname == "api.deepseek.com":
                request_body["thinking"] = {"type": "disabled"}
            response = await client.post(
                base_url.rstrip("/") + "/chat/completions",
                headers={"Authorization": f"Bearer {api_key}"},
                json=request_body,
            )
            response.raise_for_status()
            body = response.json()
            usage_details = body.get("usage", {})
            usage = int(usage_details.get("total_tokens", 0))
            output_tokens = int(usage_details.get("completion_tokens", usage))
            if output_tokens > token_budget:
                return AdvisorReplyResult(None, usage, output_tokens, "OUTPUT_TOKEN_LIMIT")
            content = body["choices"][0]["message"].get("content")
            if not isinstance(content, str) or not content.strip():
                return AdvisorReplyResult(None, usage, output_tokens, "EMPTY_RESPONSE")
            reply = AdvisorReply.model_validate_json(content)
            if _has_unsupported_removal_claim(reply.reply, facts):
                return AdvisorReplyResult(None, usage, output_tokens, "UNSUPPORTED_STATE_CLAIM")
            return AdvisorReplyResult(reply, usage, output_tokens)
    except httpx.TimeoutException:
        return AdvisorReplyResult(None, 0, 0, "TIMEOUT")
    except httpx.HTTPStatusError as exc:
        return AdvisorReplyResult(None, 0, 0, f"HTTP_{exc.response.status_code}")
    except httpx.HTTPError:
        return AdvisorReplyResult(None, 0, 0, "UPSTREAM_ERROR")
    except (KeyError, IndexError, TypeError):
        return AdvisorReplyResult(None, 0, 0, "INVALID_RESPONSE")
    except (ValueError, ValidationError):
        return AdvisorReplyResult(None, 0, 0, "INVALID_JSON_OR_SCHEMA")
