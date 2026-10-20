import json
from datetime import UTC, datetime
from io import BytesIO
from types import SimpleNamespace
from uuid import uuid4

import httpx
import pytest
from fastapi import HTTPException, UploadFile
from starlette.datastructures import Headers
from travelpilot_api.api.v1 import content as content_api
from travelpilot_api.api.v1.content import (
    _guide_place_category,
    confirm_content,
    recognize_content_image,
)
from travelpilot_api.schemas.content import ConfirmCandidates
from travelpilot_core.agents.extraction import (
    Extraction,
    GuideFact,
    PlaceMention,
    _merge_extractions,
    _split_guide_text,
    extract_places,
    fallback_extract,
)
from travelpilot_infra.db.models.poi import PoiModel
from travelpilot_infra.db.models.wishlist import WishlistItemModel
from travelpilot_infra.db.models.workflow import ContentSourceModel

GUIDE = """梅州一日游攻略
上午9:00-11:00 江北老街（嘉应古城）
打车至八角亭，步行20米即可达。
11:00-11:30 梅州学宫，周一闭馆，其余时间开放。
午餐推荐鑫丰特色餐饮，门票提前预约。
晚上18:00-21:00 嘉应古城夜景，凌风西路骑楼群
推荐入住梅州客家宾馆。
"""


def test_fallback_extracts_places_and_classifies_food_and_lodging():
    result = fallback_extract(GUIDE)
    places = {item.name: item for item in result.places}

    assert "江北老街" in places
    assert "梅州学宫" in places
    assert "凌风西路骑楼群" in places
    assert "八角亭" in places
    assert places["鑫丰特色餐饮"].category == "FOOD"
    assert places["梅州客家宾馆"].category == "LODGING"
    assert "打车至八角亭" not in places
    assert all(item.evidence and item.evidence in GUIDE for item in result.places)


def test_fallback_extracts_distinct_guide_facts_with_verbatim_evidence():
    result = fallback_extract(GUIDE)
    facts = {(item.kind, item.content) for item in result.guide_facts}

    assert ("SUGGESTED_TIME", "上午9:00-11:00 江北老街（嘉应古城）") in facts
    assert ("OPENING_HOURS", "周一闭馆") in facts
    assert ("FOOD", "午餐推荐鑫丰特色餐饮") in facts
    assert ("TICKET", "门票提前预约") in facts
    assert ("TRANSPORT", "打车至八角亭") in facts
    assert ("TRANSPORT", "步行20米即可达") in facts
    assert ("ACCOMMODATION", "推荐入住梅州客家宾馆") in facts
    assert all(evidence in GUIDE for _, evidence in facts)
    hours = next(item for item in result.guide_facts if item.kind == "OPENING_HOURS")
    assert hours.related_places == ["梅州学宫"]


@pytest.mark.parametrize(
    ("raw", "name", "expected"),
    [
        ("FOOD", "鑫丰特色餐饮", "FOOD"),
        ("餐厅", "老字号", "FOOD"),
        ("LODGING", "梅州客家宾馆", "LODGING"),
        ("ATTRACTION", "梅州学宫", "ATTRACTION"),
        ("OTHER", "街角便利店", "OTHER"),
        ("UNRECOGNIZED", "街角便利店", "OTHER"),
    ],
)
def test_entity_category_normalization(raw, name, expected):
    assert _guide_place_category(raw, name) == expected


@pytest.mark.asyncio
async def test_model_facts_are_typed_and_unverifiable_evidence_is_removed(monkeypatch):
    response_body = {
        "usage": {"total_tokens": 30, "completion_tokens": 20},
        "choices": [{"message": {"content": json.dumps({
            "places": [{
                "name": "鑫丰特色餐饮", "category": "FOOD", "confidence": 0.9,
                "action": "mention", "evidence": "午餐推荐鑫丰特色餐饮",
            }],
            "guide_facts": [
                {"kind": "FOOD", "title": "午餐", "content": "午餐推荐鑫丰特色餐饮",
                 "evidence": "午餐推荐鑫丰特色餐饮", "related_places": ["鑫丰特色餐饮"], "confidence": 0.9},
                {"kind": "TICKET", "title": "门票", "content": "需要提前预约",
                 "evidence": "攻略作者建议提前预约", "related_places": [], "confidence": 0.7},
            ],
            "search_queries": [], "pace_adjustment": "none",
        }, ensure_ascii=False)}}],
    }

    class Response:
        status_code = 200

        def raise_for_status(self):
            return None

        def json(self):
            return response_body

    class Client:
        def __init__(self, **_kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def post(self, *_args, **_kwargs):
            return Response()

    monkeypatch.setattr(httpx, "AsyncClient", Client)
    result = await extract_places(
        "午餐推荐鑫丰特色餐饮", api_key="local-test", model="local-test",
        base_url="https://example.invalid/v1",
    )

    assert result.places[0].category == "FOOD"
    assert result.guide_facts[0].kind == "FOOD"
    assert result.guide_facts[0].evidence == "午餐推荐鑫丰特色餐饮"
    assert result.guide_facts[0].related_places == ["鑫丰特色餐饮"]
    assert result.guide_facts[1].evidence is None


def test_guide_chunker_preserves_all_input_characters_and_respects_chunk_limit():
    text = ("A" * 1300) + "\n" + ("B" * 1000) + "\nTAIL"

    chunks = _split_guide_text(text)

    assert len(chunks) > 1
    assert "".join(chunks) == text
    assert all(0 < len(chunk) <= 500 for chunk in chunks)

    natural_text = "A" * 400 + "。\n" + "B" * 1000
    natural_chunks = _split_guide_text(natural_text)
    assert natural_chunks[0].endswith("\n")
    assert "".join(natural_chunks) == natural_text


def test_guide_merge_keeps_more_than_twenty_places_from_multiple_chunks():
    chunks = [
        Extraction(places=[
            PlaceMention(name=f"旅行地点{i:02}", confidence=0.8)
            for i in range(start, start + 12)
        ])
        for start in (0, 12)
    ]

    merged = _merge_extractions(chunks)

    assert len(merged.places) == 24
    assert merged.places[-1].name == "旅行地点23"
    assert len(ConfirmCandidates(candidate_ids=[uuid4() for _ in range(24)]).candidate_ids) == 24


@pytest.mark.asyncio
async def test_local_fallback_also_processes_late_guide_segments():
    text = ("A" * 1400) + "\n推荐入住梅州客家宾馆"

    result = await extract_places(text, guide_import=True)

    assert any(place.name == "梅州客家宾馆" for place in result.places)
    assert any(fact.kind == "ACCOMMODATION" for fact in result.guide_facts)


@pytest.mark.asyncio
async def test_long_guide_is_chunked_compactly_and_results_are_merged(monkeypatch):
    import httpx

    text = "上午游览宽窄巷子。" * 45 + "\n" + "午餐推荐蜀香餐厅，下午参观武侯祠。" * 15
    chunks = _split_guide_text(text)
    assert len(chunks) == 2
    responses = [
        {
            "usage": {"prompt_tokens": 100, "completion_tokens": 30, "total_tokens": 130},
            "choices": [{"message": {"content": json.dumps({
                "places": [{"name": "宽窄巷子", "category": "ATTRACTION", "confidence": 0.9,
                            "action": "mention", "evidence": "宽窄巷子"}],
                "guide_facts": [{"kind": "SUGGESTED_TIME", "title": "游览时间",
                                 "content": "上午游览宽窄巷子", "evidence": "上午游览宽窄巷子",
                                 "related_places": ["宽窄巷子"], "confidence": 0.9}],
                "search_queries": [], "intent": "general", "pace_adjustment": "none",
            }, ensure_ascii=False)}}],
        },
        {
            "usage": {"prompt_tokens": 110, "completion_tokens": 40, "total_tokens": 150},
            "choices": [{"message": {"content": json.dumps({
                "places": [{"name": "蜀香餐厅", "category": "FOOD", "confidence": 0.8,
                            "action": "mention", "evidence": "蜀香餐厅"},
                           {"name": "武侯祠", "category": "ATTRACTION", "confidence": 0.9,
                            "action": "mention", "evidence": "武侯祠"}],
                "guide_facts": [{"kind": "FOOD", "title": "午餐推荐",
                                 "content": "午餐推荐蜀香餐厅", "evidence": "午餐推荐蜀香餐厅",
                                 "related_places": ["蜀香餐厅"], "confidence": 0.8}],
                "search_queries": [], "intent": "general", "pace_adjustment": "none",
            }, ensure_ascii=False)}}],
        },
    ]
    requests = []

    class Client:
        def __init__(self, **_kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def post(self, *_args, **kwargs):
            requests.append(kwargs["json"])
            return _Response(responses.pop(0))

    class _Response:
        status_code = 200

        def __init__(self, body):
            self.body = body

        def raise_for_status(self):
            return None

        def json(self):
            return self.body

    monkeypatch.setattr(httpx, "AsyncClient", Client)
    result = await extract_places(
        text, api_key="test", model="test", base_url="https://example.invalid/v1",
        token_budget=1200, guide_import=True,
    )

    assert len(requests) == 2
    assert all(item["max_tokens"] == 1200 for item in requests)
    assert [json.loads(item["messages"][1]["content"])["current_message"] for item in requests] == chunks
    assert all("recent_conversation" not in item["messages"][0]["content"] for item in requests)
    assert [place.name for place in result.places] == ["宽窄巷子", "蜀香餐厅", "武侯祠"]
    assert len(result.guide_facts) == 2
    assert all(fact.evidence in text for fact in result.guide_facts)
    assert result.token_usage == 280
    assert result.output_tokens == 70


@pytest.mark.asyncio
async def test_guide_import_processes_text_after_the_old_eight_thousand_character_cutoff(monkeypatch):
    text = "\n".join(f"段落 {index}: " + ("攻略信息" * 100) for index in range(24))
    requests = []

    class Client:
        def __init__(self, **_kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def post(self, *_args, **kwargs):
            requests.append(kwargs["json"])
            return _Response({
                "usage": {"prompt_tokens": 20, "completion_tokens": 2, "total_tokens": 22},
                "choices": [{"message": {"content": json.dumps({
                    "places": [], "guide_facts": [], "search_queries": [],
                    "intent": "general", "pace_adjustment": "none",
                })}}],
            })

    class _Response:
        status_code = 200

        def __init__(self, body):
            self.body = body

        def raise_for_status(self):
            return None

        def json(self):
            return self.body

    monkeypatch.setattr(httpx, "AsyncClient", Client)
    result = await extract_places(
        text, api_key="test", model="test", base_url="https://example.invalid/v1",
        token_budget=1200, guide_import=True,
    )

    sent_chunks = [json.loads(item["messages"][1]["content"])["current_message"] for item in requests]
    assert len(text) > 8000
    assert len(requests) > 8
    assert "".join(sent_chunks) == text
    assert result.token_usage == 22 * len(requests)
    assert result.output_tokens == 2 * len(requests)


@pytest.mark.asyncio
async def test_guide_import_keeps_successful_chunks_and_exact_failed_ranges(monkeypatch):
    from travelpilot_core.agents.extraction import TextExtractionFailure

    text = "梅州学宫。" * 100 + "中国客家博物馆。" * 100
    chunks = _split_guide_text(text)
    calls = []

    async def extract_once(chunk, **_kwargs):
        calls.append(chunk)
        if len(calls) == 2:
            raise TextExtractionFailure(
                provider_metadata={"finish_reason": "length", "usage": {"total_tokens": 9}},
            )
        return Extraction(
            places=[], guide_facts=[GuideFact(kind="TIP", title="提示", content="第一段保留", confidence=0.8)],
            token_usage=11,
        )

    monkeypatch.setattr("travelpilot_core.agents.extraction._extract_places_once", extract_once)
    result = await extract_places(text, api_key="test", model="test", guide_import=True)

    assert calls == chunks
    assert len(chunks) == 3
    assert result.chunk_count == 3
    assert result.token_usage == 31
    assert [fact.content for fact in result.guide_facts] == ["第一段保留"]
    assert any("客家博物馆" in place.name for place in result.places)
    assert result.fallback_chunks == [{
        "index": 1,
        "start": len(chunks[0]),
        "end": len(chunks[0]) + len(chunks[1]),
    }]
    assert result.failed_chunks == [{
        "index": 1, "start": len(chunks[0]), "end": len(chunks[0]) + len(chunks[1]),
        "failure_code": "MODEL_RESPONSE_INVALID", "finish_reason": "length",
    }]


@pytest.mark.asyncio
async def test_short_guide_import_also_obeys_per_chunk_output_budget(monkeypatch):
    captured = {}

    async def extract_once(_text, **kwargs):
        captured.update(kwargs)
        return Extraction(places=[], guide_facts=[])

    monkeypatch.setattr("travelpilot_core.agents.extraction._extract_places_once", extract_once)
    await extract_places("短攻略内容", api_key="test", model="test", token_budget=1200, guide_import=True)
    assert captured["token_budget"] == 1200


@pytest.mark.asyncio
async def test_image_import_ocr_returns_text_without_persisting_or_sending_image_to_model(monkeypatch):
    calls = []

    def normalize(data, content_type):
        calls.append(("normalize", data, content_type))
        return b"normalized-image", b"thumbnail", "png", "image/png"

    def recognize(data):
        calls.append(("ocr", data))
        return "午餐推荐鑫丰特色餐饮", 0.97

    monkeypatch.setattr(content_api, "normalize_image", normalize)
    monkeypatch.setattr(content_api, "recognize_text", recognize)
    upload = UploadFile(
        file=BytesIO(b"image-bytes"), filename="guide.png",
        headers=Headers({"content-type": "image/png"}),
    )

    result = await recognize_content_image(
        uuid4(), upload, session=object(), _trip=SimpleNamespace(),
    )

    assert result.text == "午餐推荐鑫丰特色餐饮"
    assert result.confidence == 0.97
    assert result.stored is False
    assert calls == [
        ("normalize", b"image-bytes", "image/png"),
        ("ocr", b"normalized-image"),
    ]


@pytest.mark.asyncio
async def test_image_import_rejects_unsupported_mime_type():
    upload = UploadFile(
        file=BytesIO(b"not-an-image"), filename="guide.gif",
        headers=Headers({"content-type": "image/gif"}),
    )
    with pytest.raises(HTTPException) as error:
        await recognize_content_image(uuid4(), upload, session=object(), _trip=SimpleNamespace())
    assert error.value.status_code == 415


@pytest.mark.asyncio
async def test_confirmation_saves_selected_facts_only_to_their_related_place():
    fact_id = uuid4()
    source = ContentSourceModel(
        id=uuid4(), trip_id=uuid4(), content_type="TEXT", text=GUIDE,
        status="NEEDS_CONFIRMATION", created_at=datetime.now(UTC), token_usage=0, guide_facts=[
            {"id": str(fact_id), "kind": "FOOD", "title": "午餐", "content": "午餐推荐鑫丰特色餐饮",
             "evidence": "午餐推荐鑫丰特色餐饮", "related_places": ["鑫丰特色餐饮"], "confidence": 0.9},
        ],
    )
    food = SimpleNamespace(
        id=uuid4(), entity_id=uuid4(), confirmed=False, name="鑫丰特色餐饮", address="测试路 1 号",
        latitude=24.29, longitude=116.12, duration_min=60, provider="TEST", provider_id="food-1",
        category="FOOD",
    )
    hotel = SimpleNamespace(
        id=uuid4(), entity_id=uuid4(), confirmed=False, name="梅州客家宾馆", address="测试路 2 号",
        latitude=24.3, longitude=116.13, duration_min=60, provider="TEST", provider_id="hotel-1",
        category="LODGING",
    )
    food_entity = SimpleNamespace(id=food.entity_id, name="鑫丰特色餐饮", suggestion=None)
    hotel_entity = SimpleNamespace(id=hotel.entity_id, name="梅州客家宾馆", suggestion=None)

    class Rows:
        def all(self):
            return []

    class Result:
        def all(self):
            return [(food, food_entity), (hotel, hotel_entity)]

    class Session:
        def __init__(self):
            self.added = []

        async def execute(self, _statement):
            return Result()

        async def scalars(self, _statement):
            return Rows()

        async def get(self, model, _identity):
            return source if model is ContentSourceModel else None

        def add(self, item):
            self.added.append(item)

        async def flush(self):
            for item in self.added:
                if isinstance(item, PoiModel) and item.id is None:
                    item.id = uuid4()

        async def commit(self):
            return None

    session = Session()
    await confirm_content(
        source.id,
        ConfirmCandidates(candidate_ids=[food.id, hotel.id], fact_ids=[fact_id]),
        session=session, source=source,
    )

    wishlist_rows = [item for item in session.added if isinstance(item, WishlistItemModel)]
    notes_by_poi = {item.poi_id: item.notes for item in wishlist_rows}
    food_poi = next(item for item in session.added if isinstance(item, PoiModel) and item.name == "鑫丰特色餐饮")
    hotel_poi = next(item for item in session.added if isinstance(item, PoiModel) and item.name == "梅州客家宾馆")
    assert "午餐推荐鑫丰特色餐饮" in notes_by_poi[food_poi.id]
    assert notes_by_poi[hotel_poi.id] is None
    assert source.guide_facts[0]["saved"] is True
