"""Typed text extraction with an optional OpenAI compatible model endpoint."""

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


class Extraction(BaseModel):
    places: list[PlaceMention] = Field(max_length=20)
    search_queries: list[str] = Field(default_factory=list, max_length=5)
    pace_adjustment: Literal["slower", "faster", "none"] = "none"
    token_usage: int = Field(default=0, exclude=True, ge=0)
    output_tokens: int = Field(default=0, exclude=True, ge=0)


_PLACE = re.compile(r"[\u4e00-\u9fff]{2,20}(?:巷子|基地|公园|博物馆|寺|祠|山|街|湖)")


def fallback_extract(text: str) -> Extraction:
    """Conservative local fallback: suggestions always require user confirmation."""
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
    return Extraction(places=mentions, search_queries=search_queries, pace_adjustment=pace_adjustment)


async def extract_places(
    text: str, *, api_key: str = "", model: str = "", base_url: str = "", token_budget: int = 800,
    additional_instructions: str = "",
) -> Extraction:
    if not api_key or not model:
        return fallback_extract(text)
    url = base_url.rstrip("/") + "/chat/completions"
    try:
        async with httpx.AsyncClient(timeout=12) as client:
            response = await client.post(url, headers={"Authorization": f"Bearer {api_key}"}, json={
                "model": model,
                "response_format": {"type": "json_object"},
                "max_tokens": min(800, token_budget),
                "messages": [
                    {"role": "system", "content": "Interpret explicit travel-planning intent. Return JSON object with places: [{name,category,suggestion,confidence,action,evidence}], search_queries (up to 5 short map keywords for positively stated interest categories, such as food or museums), and pace_adjustment (slower, faster, or none). action must be add, remove, or mention. Use remove only when the user clearly asks to remove/avoid that place; use add when they clearly want to visit it; otherwise mention. Change pace and search categories only when explicitly requested; never search for a category the user rejects. Extract real place names only, never invent places or interests, and return at most 20 places. Evidence must be a short verbatim substring of the input, or null.\n" + additional_instructions[:4000]},
                    {"role": "user", "content": text[:8000]},
                ],
                "temperature": 0,
            })
            response.raise_for_status()
            body = response.json()
            usage_details = body.get("usage", {})
            usage = int(usage_details.get("total_tokens", 0))
            output_tokens = int(usage_details.get("completion_tokens", usage))
            if output_tokens > token_budget:
                raise RuntimeError("TOKEN_BUDGET_EXCEEDED")
            content = body["choices"][0]["message"]["content"]
            return Extraction.model_validate_json(content).model_copy(
                update={"token_usage": usage, "output_tokens": output_tokens}
            )
    except (httpx.HTTPError, KeyError, IndexError, TypeError, ValidationError, ValueError) as exc:
        raise RuntimeError("TEXT_EXTRACTION_FAILED") from exc
