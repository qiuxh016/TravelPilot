"""Typed text extraction with an optional OpenAI compatible model endpoint."""

import re

import httpx
from pydantic import BaseModel, Field, ValidationError


class PlaceMention(BaseModel):
    name: str = Field(min_length=2, max_length=200)
    category: str = Field(default="ATTRACTION", max_length=50)
    suggestion: str | None = None
    confidence: float = Field(ge=0, le=1)


class Extraction(BaseModel):
    places: list[PlaceMention] = Field(max_length=20)


_PLACE = re.compile(r"[\u4e00-\u9fff]{2,20}(?:巷子|基地|公园|博物馆|寺|祠|山|街|湖)")


def fallback_extract(text: str) -> Extraction:
    """Conservative local fallback: suggestions always require user confirmation."""
    aliases = {"熊猫": "成都大熊猫繁育研究基地", "宽窄巷子": "宽窄巷子", "武侯祠": "武侯祠"}
    names = [name for needle, name in aliases.items() if needle in text]
    for segment in re.split(r"[，。、;；和及与]|还有|以及", text):
        for match in _PLACE.finditer(segment):
            name = re.sub(r"^(想去|要去|去|逛|游览|打卡)", "", match.group())
            if name not in names and not any(alias in name for alias in aliases):
                names.append(name)
    return Extraction(places=[PlaceMention(name=name, confidence=0.55) for name in names[:20]])


async def extract_places(text: str, *, api_key: str = "", model: str = "", base_url: str = "") -> Extraction:
    if not api_key or not model:
        return fallback_extract(text)
    url = base_url.rstrip("/") + "/chat/completions"
    try:
        async with httpx.AsyncClient(timeout=12) as client:
            response = await client.post(url, headers={"Authorization": f"Bearer {api_key}"}, json={
                "model": model,
                "response_format": {"type": "json_object"},
                "messages": [
                    {"role": "system", "content": "Extract real place mentions only. Return JSON object with places: [{name,category,suggestion,confidence}]. Never invent places. At most 20."},
                    {"role": "user", "content": text[:8000]},
                ],
                "temperature": 0,
            })
            response.raise_for_status()
            content = response.json()["choices"][0]["message"]["content"]
            return Extraction.model_validate_json(content)
    except (httpx.HTTPError, KeyError, IndexError, TypeError, ValidationError, ValueError) as exc:
        raise RuntimeError("TEXT_EXTRACTION_FAILED") from exc
