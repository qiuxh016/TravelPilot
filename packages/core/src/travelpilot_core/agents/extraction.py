"""Typed text extraction with an optional OpenAI compatible model endpoint."""

import json
import re
from typing import Literal

import httpx
from pydantic import BaseModel, Field, ValidationError


class PlaceMention(BaseModel):
    name: str = Field(min_length=2, max_length=200)
    category: str = Field(default="ATTRACTION", max_length=50)
    suggestion: str | None = None
    confidence: float = Field(ge=0, le=1)
    action: Literal["add", "remove", "mention"] = "mention"
    evidence: str | None = Field(default=None, max_length=500)


class RequestedTask(BaseModel):
    """User-grounded domain request proposed by the intent model."""

    kind: Literal[
        "PLACE_RECOMMENDATION", "ATTRACTION_SEARCH", "FOOD_SEARCH", "LODGING_SEARCH", "WEATHER_CHECK",
        "PREPARATION_ADVICE", "ROUTE_QUERY", "SCHEDULE",
    ]
    evidence: str = Field(min_length=1, max_length=200)


class Extraction(BaseModel):
    places: list[PlaceMention] = Field(max_length=20)
    search_queries: list[str] = Field(default_factory=list, max_length=5)
    requested_tasks: list[RequestedTask] = Field(default_factory=list, max_length=7)
    intent: Literal["schedule", "recommend", "search", "modify", "preference", "general"] = "general"
    pace_adjustment: Literal["slower", "faster", "none"] = "none"
    token_usage: int = Field(default=0, exclude=True, ge=0)
    output_tokens: int = Field(default=0, exclude=True, ge=0)
    finish_reason: str | None = Field(default=None, exclude=True, max_length=40)
    provider_http_status: int | None = Field(default=None, exclude=True, ge=100, le=599)


_PLACE = re.compile(r"[\u4e00-\u9fff]{2,20}(?:巷子|基地|公园|博物馆|寺|祠|山|街|湖)")
_JSON_FENCE = re.compile(r"^```(?:json)?\s*\n(?P<body>.*?)\n```$", re.IGNORECASE | re.DOTALL)


class TextExtractionFailure(RuntimeError):
    """Safe extraction failure with optional response-shape diagnostics."""

    def __init__(
        self,
        response_shape: dict[str, object] | None = None,
        provider_metadata: dict[str, object] | None = None,
    ):
        super().__init__("TEXT_EXTRACTION_FAILED")
        self.response_shape = response_shape
        self.provider_metadata = provider_metadata


def _response_shape(content: object) -> dict[str, object]:
    """Describe response formatting without retaining or exposing its content."""
    if not isinstance(content, str):
        return {"content_type": type(content).__name__}
    stripped = content.strip()
    fenced = _JSON_FENCE.fullmatch(stripped)
    candidate = fenced.group("body").strip() if fenced else stripped
    return {
        "content_type": "str",
        "character_count": len(content),
        "json_fence": bool(fenced),
        "starts_with_object": candidate.startswith("{"),
        "ends_with_object": candidate.endswith("}"),
    }


def _json_content(content: object) -> str:
    if not isinstance(content, str):
        raise TypeError("Model response content must be text")
    stripped = content.strip()
    fenced = _JSON_FENCE.fullmatch(stripped)
    return fenced.group("body").strip() if fenced else stripped


def fallback_extract(text: str) -> Extraction:
    """Conservative local fallback: suggestions always require user confirmation."""
    if any(term in text for term in ("时间怎么安排", "时间如何安排", "安排时间", "调整时间", "优化时间", "排一下时间", "路线怎么排", "怎么排比较合适")):
        intent = "schedule"
    elif any(term in text for term in ("推荐", "热门", "人气", "有什么好玩", "有什么值得去")):
        intent = "recommend"
    elif any(term in text for term in ("加入", "想去", "搜索", "找一下", "查一下")):
        intent = "search"
    elif any(term in text for term in ("不要", "移除", "删除", "删掉", "保留", "调整", "轻松一点", "紧凑一点")):
        intent = "modify"
    elif any(term in text for term in ("喜欢", "偏好", "预算", "同行", "不喜欢", "不想")):
        intent = "preference"
    else:
        intent = "general"
    aliases = {"熊猫": "成都大熊猫繁育研究基地", "宽窄巷子": "宽窄巷子", "武侯祠": "武侯祠"}
    names = [name for needle, name in aliases.items() if needle in text]
    for segment in re.split(r"[，。、;；和及与]|还有|以及", text):
        for match in _PLACE.finditer(segment):
            raw_name = match.group()
            generic_categories = ("博物馆", "公园", "咖啡馆", "美食", "火锅", "餐厅")
            interest_language = ("喜欢", "偏好", "想", "不要", "不想", "避开", "逛")
            if any(category in raw_name for category in generic_categories) and any(term in raw_name for term in interest_language):
                continue
            name = re.sub(r"^(我不喜欢|不喜欢|不想去|不要去|不想|不要|我想去|想去|要去|去|我想逛|也想逛|想逛|逛|游览|打卡|喜欢|偏好|想看|看看)", "", raw_name)
            if name in {"博物馆", "公园", "咖啡馆", "美食", "火锅", "餐厅"}:
                continue
            if name not in names and not any(alias in name for alias in aliases):
                names.append(name)
    mentions = []
    for name in names[:20]:
        position = text.find(name)
        if position < 0:
            position = next((text.find(alias) for alias, expanded in aliases.items()
                             if expanded == name and text.find(alias) >= 0), 0)
        context = text[max(0, position - 20):position + len(name) + 20]
        if any(term in context for term in ("不要", "不去", "不想去", "移除", "删掉", "删除", "去掉", "取消", "排除")):
            action = "remove"
        elif any(term in context for term in ("想去", "要去", "安排", "加入", "保留", "打卡", "游览")):
            action = "add"
        else:
            action = "mention"
        evidence = context[:180].strip() or None
        mentions.append(PlaceMention(name=name, confidence=0.55, action=action, evidence=evidence))
    pace_adjustment = (
        "slower" if any(term in text for term in ("轻松一点", "轻松一些", "慢一点", "慢一些", "多休息", "少安排", "别太赶", "不要太赶")) else
        "faster" if any(term in text for term in ("紧凑一点", "快一点", "多安排", "安排紧凑")) else
        "none"
    )
    interest_terms = (
        ("美食", "美食"), ("吃", "美食"), ("川菜", "川菜"), ("火锅", "火锅"),
        ("博物馆", "博物馆"), ("历史", "历史景点"), ("古迹", "历史景点"),
        ("公园", "公园"), ("自然", "自然景点"), ("咖啡", "咖啡馆"),
        ("购物", "购物"), ("夜景", "夜景"), ("亲子", "亲子景点"),
    )
    negative_terms = ("不喜欢", "不想", "不要", "避开", "讨厌", "不去")
    search_queries = list(dict.fromkeys(
        query for term, query in interest_terms
        if term in text and not any(negative in text[max(0, text.find(term) - 8):text.find(term)] for negative in negative_terms)
    ))[:5]
    if any(query in search_queries for query in ("川菜", "火锅")):
        search_queries = [query for query in search_queries if query != "美食"]
    return Extraction(places=mentions, search_queries=search_queries, intent=intent, pace_adjustment=pace_adjustment)


async def extract_places(
    text: str, *, api_key: str = "", model: str = "", base_url: str = "", token_budget: int = 800,
    additional_instructions: str = "", thinking_mode: Literal["enabled", "disabled"] | None = None,
    conversation_context: list[dict[str, str]] | None = None,
    travel_preferences: list[str] | None = None,
) -> Extraction:
    if not api_key or not model:
        return fallback_extract(text)
    url = base_url.rstrip("/") + "/chat/completions"
    content: object = None
    response = None
    provider_metadata: dict[str, object] = {}
    bounded_history = []
    for item in (conversation_context or [])[-6:]:
        if not isinstance(item, dict) or item.get("role") not in {"user", "assistant"}:
            continue
        message = item.get("content")
        if isinstance(message, str) and message.strip():
            bounded_history.append({"role": item["role"], "content": message.strip()[-400:]})
    extraction_input = json.dumps({
        "current_message": text[:8000],
        "recent_conversation": bounded_history,
        "confirmed_travel_preferences": [
            item.strip()[:160] for item in (travel_preferences or [])[:5]
            if isinstance(item, str) and item.strip()
        ],
    }, ensure_ascii=False)
    try:
        async with httpx.AsyncClient(timeout=12) as client:
            request_body = {
                "model": model,
                "response_format": {"type": "json_object"},
                "max_tokens": min(800, token_budget),
                "messages": [
                    {"role": "system", "content": "Interpret the user's latest travel-planning message. The input is JSON with current_message and recent_conversation. Use recent_conversation only to resolve explicit references in current_message; never treat older messages or assistant suggestions as new approval, preference changes, or user actions. Return JSON only, for example {\"intent\":\"schedule\",\"places\":[],\"search_queries\":[],\"requested_tasks\":[],\"pace_adjustment\":\"none\"}. intent must be one of schedule (ask to arrange/review time or route for known trip places, including an explicit request to adjust the remaining itinerary because of current fatigue), recommend (ask for suggestions/popular places), search (name/category to search), modify (add/remove/keep/change existing choices or pace), preference (state preferences/constraints), general (other). Also return requested_tasks, a list of explicitly requested task objects with kind from PLACE_RECOMMENDATION, ATTRACTION_SEARCH, FOOD_SEARCH, LODGING_SEARCH, WEATHER_CHECK, PREPARATION_ADVICE, ROUTE_QUERY, SCHEDULE and evidence copied verbatim from current_message. Include only tasks the user is asking TravelPilot to perform now; do not infer tasks from trip facts, older turns, assistant suggestions, examples, or negative statements. Every evidence string must be a substring of current_message. Do not include a task when the user says they do not want or need it. A statement that the user is tired without a request to change this trip is not schedule intent and must not change pace. Return places: [{name,category,suggestion,confidence,action,evidence}], search_queries (up to 5 short map keywords for positively stated interest categories, such as food or museums), and pace_adjustment (slower, faster, or none). Each confidence must be a JSON number from 0 to 1, never a descriptive label; place evidence must be a short verbatim substring of current_message or null. action must be add, remove, or mention. Use remove only when the latest message clearly asks to remove/avoid that place; use add only when the latest message clearly asks to add or visit it; otherwise mention. When resolving 'the first one' or similar, identify a real place only if it appears in recent_conversation. Never infer the action from assistant text. Change pace and search categories only when explicitly requested in current_message; never search for a category the user rejects. Extract real place names only, never invent places or interests, and return at most 20 places.\n" + additional_instructions[:4000]},
                    {"role": "user", "content": extraction_input},
                ],
                "temperature": 0,
            }
            if thinking_mode is not None:
                request_body["thinking"] = {"type": thinking_mode}
            response = await client.post(
                url, headers={"Authorization": f"Bearer {api_key}"}, json=request_body,
            )
            response.raise_for_status()
            body = response.json()
            usage_details = body.get("usage", {})
            usage = int(usage_details.get("total_tokens", 0))
            output_tokens = int(usage_details.get("completion_tokens", usage))
            status_code = getattr(response, "status_code", None)
            if type(status_code) is int:
                provider_metadata["http_status"] = status_code
            if isinstance(usage_details, dict):
                provider_metadata["usage"] = {
                    key: usage_details[key]
                    for key in ("prompt_tokens", "completion_tokens", "total_tokens")
                    if type(usage_details.get(key)) is int and usage_details[key] >= 0
                }
            choices = body.get("choices")
            if isinstance(choices, list) and choices and isinstance(choices[0], dict):
                finish_reason = choices[0].get("finish_reason")
                if isinstance(finish_reason, str):
                    provider_metadata["finish_reason"] = finish_reason[:40]
            if output_tokens > token_budget:
                raise RuntimeError("TOKEN_BUDGET_EXCEEDED")
            content = body["choices"][0]["message"]["content"]
            extracted = Extraction.model_validate_json(_json_content(content))
            # Evidence is used to authorize a referential add/remove only when
            # the model points to text actually present in the latest message.
            extracted = extracted.model_copy(update={
                "places": [
                    item.model_copy(update={
                        "evidence": item.evidence if item.evidence and item.evidence in text else None,
                    })
                    for item in extracted.places
                ],
                "requested_tasks": [
                    item for item in extracted.requested_tasks
                    if item.evidence in text
                ],
            })
            return extracted.model_copy(
                update={
                    "token_usage": usage,
                    "output_tokens": output_tokens,
                    "finish_reason": provider_metadata.get("finish_reason"),
                    "provider_http_status": provider_metadata.get("http_status"),
                }
            )
    except (httpx.HTTPError, KeyError, IndexError, TypeError, ValidationError, ValueError) as exc:
        if response is not None:
            status_code = getattr(response, "status_code", None)
            if type(status_code) is int:
                provider_metadata.setdefault("http_status", status_code)
        raise TextExtractionFailure(_response_shape(content), provider_metadata or None) from exc
