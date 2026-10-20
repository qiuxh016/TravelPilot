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


class GuideFact(BaseModel):
    kind: Literal[
        "TICKET", "OPENING_HOURS", "SUGGESTED_TIME", "FOOD", "ROUTE",
        "TRANSPORT", "ACCOMMODATION", "TIP", "OTHER",
    ]
    title: str = Field(min_length=1, max_length=100)
    content: str = Field(min_length=1, max_length=600)
    evidence: str | None = Field(default=None, max_length=600)
    related_places: list[str] = Field(default_factory=list, max_length=10)
    confidence: float = Field(ge=0, le=1)


class RequestedTask(BaseModel):
    """User-grounded domain request proposed by the intent model."""

    kind: Literal[
        "PLACE_RECOMMENDATION", "ATTRACTION_SEARCH", "FOOD_SEARCH", "LODGING_SEARCH", "WEATHER_CHECK",
        "PREPARATION_ADVICE", "ROUTE_QUERY", "SCHEDULE",
    ]
    evidence: str = Field(min_length=1, max_length=200)


class Extraction(BaseModel):
    places: list[PlaceMention] = Field(max_length=20)
    guide_facts: list[GuideFact] = Field(default_factory=list, max_length=40)
    search_queries: list[str] = Field(default_factory=list, max_length=5)
    requested_tasks: list[RequestedTask] = Field(default_factory=list, max_length=7)
    intent: Literal["schedule", "recommend", "search", "modify", "preference", "general"] = "general"
    pace_adjustment: Literal["slower", "faster", "none"] = "none"
    token_usage: int = Field(default=0, exclude=True, ge=0)
    output_tokens: int = Field(default=0, exclude=True, ge=0)
    chunk_count: int = Field(default=0, exclude=True, ge=0)
    failed_chunks: list[dict[str, object]] = Field(default_factory=list, exclude=True)
    finish_reason: str | None = Field(default=None, exclude=True, max_length=40)
    provider_http_status: int | None = Field(default=None, exclude=True, ge=100, le=599)


_PLACE = re.compile(
    r"[\u4e00-\u9fff]{2,24}(?:大熊猫繁育研究基地|动物园|植物园|博物馆|纪念馆|展览馆|"
    r"特色餐饮|青年旅舍|餐厅|餐馆|饭店|食府|酒楼|火锅|酒店|宾馆|民宿|客栈|"
    r"步行街|骑楼群|主题乐园|游乐园|古城|古镇|老街|巷子|景区|景点|公园|广场|"
    r"学宫|书院|故居|纪念堂|教堂|寺|祠|山|湖|江|河|海|岛|古塔|塔|亭|楼(?!群)|桥|"
    r"机场|火车站|高铁站|汽车站|码头)"
)
_JSON_FENCE = re.compile(r"^```(?:json)?\s*\n(?P<body>.*?)\n```$", re.IGNORECASE | re.DOTALL)
_GUIDE_CHUNK_TARGET_CHARS = 1000
_GUIDE_CHUNK_TOKEN_CAP = 768
_GUIDE_MAX_INPUT_CHARS = 20000


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
            generic_categories = (
                "博物馆", "公园", "咖啡馆", "美食", "火锅", "餐厅", "餐馆",
                "酒店", "宾馆", "民宿", "景区", "景点", "老街", "古城",
            )
            interest_language = ("喜欢", "偏好", "想", "不要", "不想", "避开", "逛")
            if any(category in raw_name for category in generic_categories) and any(term in raw_name for term in interest_language):
                continue
            name = re.sub(
                r"^(我不喜欢|不喜欢|不想去|不要去|不想|不要|我想去|想去|要去|去|"
                r"打车至|乘车至|步行至|导航至|然后前往|接着前往|前往|经过|途经|"
                r"午餐推荐|晚餐推荐|早餐推荐|推荐入住|推荐|我想逛|也想逛|想逛|逛|游览|打卡|喜欢|偏好|想看|看看)",
                "", raw_name,
            )
            if name in generic_categories:
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
        evidence = name if name in text else context[:180].strip() or None
        lowered = name.casefold()
        category = (
            "FOOD" if any(term in lowered for term in (
                "餐饮", "餐厅", "餐馆", "饭店", "食府", "酒楼", "火锅", "小吃", "美食",
            )) else "LODGING" if any(term in lowered for term in (
                "酒店", "宾馆", "民宿", "客栈", "旅舍",
            )) else "OTHER" if any(term in lowered for term in (
                "机场", "火车站", "高铁站", "汽车站", "码头",
            )) else "ATTRACTION"
        )
        mentions.append(PlaceMention(
            name=name, category=category, confidence=0.55, action=action, evidence=evidence,
        ))
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
    fact_patterns = (
        ("TICKET", "购票与预约", re.compile(r"购票|门票|预约|订票|提前购买")),
        ("OPENING_HOURS", "开放时间", re.compile(r"开放时间|营业时间|开馆|闭馆|几点开放|几点关门")),
        ("SUGGESTED_TIME", "建议时段", re.compile(r"上午|下午|早上|中午|傍晚|晚上|\d{1,2}:\d{2}")),
        ("FOOD", "餐饮推荐", re.compile(r"美食|餐饮|餐厅|餐馆|小吃|火锅|吃饭|用餐|早餐|午餐|晚餐")),
        ("ACCOMMODATION", "住宿建议", re.compile(r"住宿|酒店|宾馆|民宿|客栈|旅舍")),
        ("ROUTE", "路线建议", re.compile(r"路线|顺路|然后|接着|前往|途经|经过|到达|游览顺序")),
        ("TRANSPORT", "出行方式", re.compile(r"步行|打车|地铁|公交|乘车|开车|骑行|停车|换乘")),
        ("TIP", "攻略提示", re.compile(r"建议|注意|记得|最好|避开|带伞|带上|携带")),
    )
    guide_facts = []
    seen_facts: set[str] = set()
    # Keep the user's sentence as the evidence unit. The previous broad regex
    # could attach a whole neighboring sentence to a fact (e.g. lunch as a
    # ticket fact) and miss details after Chinese commas.
    segments = [
        item.strip(" \t-•⭐️📍⏰")
        for item in re.split(r"[\n。；;，,]+", text)
        if item.strip(" \t-•⭐️📍⏰")
    ]
    for evidence in segments:
        for kind, title, pattern in fact_patterns:
            if not pattern.search(evidence):
                continue
            normalized = re.sub(r"\s+", "", evidence)
            key = f"{kind}:{normalized}"
            if len(evidence) < 3 or key in seen_facts:
                continue
            seen_facts.add(key)
            related = [mention.name for mention in mentions if mention.name in evidence]
            if not related and kind in {"OPENING_HOURS", "SUGGESTED_TIME"}:
                source_line = next((line for line in text.splitlines() if evidence in line), "")
                related = [mention.name for mention in mentions if mention.name in source_line]
            guide_facts.append(GuideFact(
                kind=kind, title=title, content=evidence[:600], evidence=evidence[:600],
                related_places=related[:10], confidence=0.45,
            ))
            if len(guide_facts) >= 40:
                break
        if len(guide_facts) >= 40:
            break
    return Extraction(
        places=mentions, guide_facts=guide_facts, search_queries=search_queries,
        intent=intent, pace_adjustment=pace_adjustment,
    )


async def extract_places(
    text: str, *, api_key: str = "", model: str = "", base_url: str = "", token_budget: int = 800,
    additional_instructions: str = "", thinking_mode: Literal["enabled", "disabled"] | None = None,
    conversation_context: list[dict[str, str]] | None = None,
    travel_preferences: list[str] | None = None,
    guide_import: bool = False,
) -> Extraction:
    """Extract advisor intent or, in guide mode, all bounded text chunks."""
    if not api_key or not model:
        if guide_import and len(text) > _GUIDE_CHUNK_TARGET_CHARS:
            chunks = _split_guide_text(text)
            merged = _merge_extractions([fallback_extract(chunk) for chunk in chunks])
            return merged.model_copy(update={"chunk_count": len(chunks)})
        return fallback_extract(text)
    if not guide_import or len(text) <= _GUIDE_CHUNK_TARGET_CHARS:
        extraction = await _extract_places_once(
            text, api_key=api_key, model=model, base_url=base_url,
            token_budget=token_budget, additional_instructions=additional_instructions,
            thinking_mode=thinking_mode, conversation_context=conversation_context,
            travel_preferences=travel_preferences, guide_import=guide_import,
        )
        return extraction.model_copy(update={"chunk_count": 1 if guide_import else 0})
    if len(text) > _GUIDE_MAX_INPUT_CHARS:
        raise TextExtractionFailure(
            {"character_count": len(text), "maximum_characters": _GUIDE_MAX_INPUT_CHARS},
            {"failure_code": "INPUT_TOO_LONG"},
        )

    chunks = _split_guide_text(text)
    extractions = []
    failed_chunks = []
    failed_token_usage = 0
    failed_output_tokens = 0
    offset = 0
    for chunk_index, chunk in enumerate(chunks):
        start, end = offset, offset + len(chunk)
        offset = end
        try:
            extractions.append(await _extract_places_once(
                chunk, api_key=api_key, model=model, base_url=base_url,
                token_budget=min(token_budget, _GUIDE_CHUNK_TOKEN_CAP), additional_instructions=additional_instructions,
                thinking_mode=thinking_mode, guide_import=True,
            ))
        except TextExtractionFailure as exc:
            metadata = exc.provider_metadata or {}
            usage = metadata.get("usage")
            if isinstance(usage, dict):
                failed_token_usage += int(usage.get("total_tokens", 0))
                failed_output_tokens += int(usage.get("completion_tokens", 0))
            failed_chunks.append({
                "index": chunk_index,
                "start": start,
                "end": end,
                "failure_code": metadata.get("failure_code", "MODEL_RESPONSE_INVALID"),
                "finish_reason": metadata.get("finish_reason"),
            })
    if not extractions:
        return Extraction(
            places=[], guide_facts=[], token_usage=failed_token_usage,
            output_tokens=failed_output_tokens, chunk_count=len(chunks), failed_chunks=failed_chunks,
        )
    merged = _merge_extractions(extractions)
    return merged.model_copy(update={
        "token_usage": merged.token_usage + failed_token_usage,
        "output_tokens": merged.output_tokens + failed_output_tokens,
        "chunk_count": len(chunks),
        "failed_chunks": failed_chunks,
    })


def _split_guide_text(text: str) -> list[str]:
    """Split without dropping characters, preferring paragraph and sentence edges."""
    chunks: list[str] = []
    start = 0
    while start < len(text):
        hard_end = min(start + _GUIDE_CHUNK_TARGET_CHARS, len(text))
        if hard_end == len(text):
            chunks.append(text[start:hard_end])
            break
        safe_start = min(start + 1200, hard_end)
        boundary = max(
            text.rfind(mark, safe_start, hard_end) + len(mark)
            for mark in ("\n", "。", "！", "？", "；", "\r")
        )
        if boundary < safe_start:
            boundary = max(
                text.rfind(mark, safe_start, hard_end) + len(mark)
                for mark in ("，", ",", "、", " ", "\t")
            )
        if boundary < safe_start:
            boundary = hard_end
        chunks.append(text[start:boundary])
        start = boundary
    return chunks


def _merge_extractions(extractions: list[Extraction]) -> Extraction:
    places: dict[str, PlaceMention] = {}
    facts: dict[tuple[str, str], GuideFact] = {}
    queries: list[str] = []
    tasks: dict[tuple[str, str], RequestedTask] = {}
    for extraction in extractions:
        for place in extraction.places:
            key = re.sub(r"\s+", "", place.name).casefold()
            previous = places.get(key)
            if previous is None:
                places[key] = place
            else:
                places[key] = previous.model_copy(update={
                    "suggestion": previous.suggestion or place.suggestion,
                    "evidence": previous.evidence or place.evidence,
                    "confidence": max(previous.confidence, place.confidence),
                    "category": place.category if previous.category == "ATTRACTION" and place.category != "ATTRACTION" else previous.category,
                })
        for fact in extraction.guide_facts:
            key = (fact.kind, re.sub(r"\s+", "", fact.content).casefold())
            previous = facts.get(key)
            if previous is None:
                facts[key] = fact
            else:
                facts[key] = previous.model_copy(update={
                    "related_places": list(dict.fromkeys(previous.related_places + fact.related_places)),
                    "evidence": previous.evidence or fact.evidence,
                    "confidence": max(previous.confidence, fact.confidence),
                })
        queries.extend(query for query in extraction.search_queries if query not in queries)
        for task in extraction.requested_tasks:
            tasks.setdefault((task.kind, task.evidence), task)
    merged_places = list(places.values())
    merged_facts = list(facts.values())
    if len(merged_places) > 20 or len(merged_facts) > 40:
        raise TextExtractionFailure(
            {"place_count": len(merged_places), "fact_count": len(merged_facts)},
            {"failure_code": "RESULT_LIMIT_EXCEEDED"},
        )
    return Extraction(
        places=merged_places, guide_facts=merged_facts, search_queries=queries[:5],
        requested_tasks=list(tasks.values())[:7],
        intent=next((item.intent for item in extractions if item.intent != "general"), "general"),
        pace_adjustment=next((item.pace_adjustment for item in extractions if item.pace_adjustment != "none"), "none"),
        token_usage=sum(item.token_usage for item in extractions),
        output_tokens=sum(item.output_tokens for item in extractions),
        chunk_count=sum(item.chunk_count for item in extractions),
        failed_chunks=[failed for item in extractions for failed in item.failed_chunks],
        finish_reason=next((item.finish_reason for item in reversed(extractions) if item.finish_reason), None),
        provider_http_status=next((item.provider_http_status for item in reversed(extractions) if item.provider_http_status), None),
    )


async def _extract_places_once(
    text: str, *, api_key: str = "", model: str = "", base_url: str = "", token_budget: int = 800,
    additional_instructions: str = "", thinking_mode: Literal["enabled", "disabled"] | None = None,
    conversation_context: list[dict[str, str]] | None = None,
    travel_preferences: list[str] | None = None,
    guide_import: bool = False,
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
    if guide_import:
        extraction_input = json.dumps({"current_message": text}, ensure_ascii=False)
        system_instructions = (
            "Extract explicitly stated travel-guide information from current_message only. Return compact JSON with "
            "places, guide_facts, search_queries, intent=general, pace_adjustment=none. Place fields: name, category, "
            "suggestion, confidence, action, evidence. Categories: ATTRACTION, FOOD, LODGING, OTHER. Do not invent "
            "places. Use action=mention unless the text explicitly asks to add or remove. Fact fields: kind, title, "
            "content, evidence, related_places, confidence. Preserve distinct ticket/booking, opening-hours, visit-time, "
            "duration, food, route/order, transport, lodging and practical-tip details. Keep content concise but retain "
            "exact times, amounts, order and conditions. Evidence is the shortest exact supporting quote from the input; "
            "do not repeat whole paragraphs. Never present guide claims as verified facts. JSON only; confidence is a "
            "number from 0 to 1; maximum 20 places and 40 facts.\n"
        ) + additional_instructions[:4000]
    else:
        extraction_input = json.dumps({
            "current_message": text[:8000],
            "recent_conversation": bounded_history,
            "confirmed_travel_preferences": [
                item.strip()[:160] for item in (travel_preferences or [])[:5]
                if isinstance(item, str) and item.strip()
            ],
        }, ensure_ascii=False)
        system_instructions = (
            "Interpret the user's latest travel-planning message. The input is JSON with current_message and recent_conversation. Use recent_conversation only to resolve explicit references in current_message; never treat older messages or assistant suggestions as new approval, preference changes, or user actions. Return JSON only, for example {\"intent\":\"schedule\",\"places\":[],\"search_queries\":[],\"guide_facts\":[],\"requested_tasks\":[],\"pace_adjustment\":\"none\"}. intent must be one of schedule, recommend, search, modify, preference, general. Also return requested_tasks, a list of explicitly requested task objects with kind from PLACE_RECOMMENDATION, ATTRACTION_SEARCH, FOOD_SEARCH, LODGING_SEARCH, WEATHER_CHECK, PREPARATION_ADVICE, ROUTE_QUERY, SCHEDULE and evidence copied verbatim from current_message. Include only tasks the user is asking TravelPilot to perform now. Return places: [{name,category,suggestion,confidence,action,evidence}] and guide_facts: [{kind,title,content,evidence,related_places,confidence}]. Extract named attractions, restaurants, hotels, and other real places in places. Normalize each place category to ATTRACTION, FOOD, LODGING, or OTHER. Separately preserve concrete guide details as guide_facts: kind must be TICKET, OPENING_HOURS, SUGGESTED_TIME, FOOD, ROUTE, TRANSPORT, ACCOMMODATION, TIP, or OTHER; content should retain the useful detail such as a ticket/booking time, recommended restaurant or dish, visit order, route, transit mode, parking, or warning. Keep one fact per actionable detail, include exact time/duration when stated, and relate it to named places when possible. Evidence must be a verbatim substring of current_message (or null); never invent facts. confidence must be a JSON number from 0 to 1. Do not treat assistant text as user evidence. Return at most 20 places and 40 facts.\n"
        ) + additional_instructions[:4000]
    try:
        async with httpx.AsyncClient(timeout=12) as client:
            request_body = {
                "model": model,
                "response_format": {"type": "json_object"},
                "max_tokens": token_budget,
                "messages": [
                    {"role": "system", "content": system_instructions},
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
                "guide_facts": [
                    item.model_copy(update={
                        "evidence": item.evidence if item.evidence and item.evidence in text else None,
                        "related_places": [
                            place_name for place_name in item.related_places
                            if any(place_name.casefold() == place.name.casefold() for place in extracted.places)
                        ],
                    })
                    for item in extracted.guide_facts
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
