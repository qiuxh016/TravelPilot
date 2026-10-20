from types import SimpleNamespace
from uuid import uuid4

import pytest
from sqlalchemy.dialects import postgresql
import httpx

from travelpilot_core.agents.advisor_reply import compose_advisor_reply_with_diagnostics
from travelpilot_api.advisor.guide_evidence import (
    guide_references_for_place,
    load_trip_guide_references,
)


def test_guide_place_matching_is_conservative_and_preserves_source_claim():
    references = {
        "鑫丰特色餐饮": [{
            "source_id": "source-1", "title": "梅州一日游攻略",
            "place_name": "鑫丰特色餐饮", "category": "FOOD",
            "guide_claim": "午餐可考虑鑫丰特色餐饮", "evidence_status": "IMPORTED_GUIDE",
        }],
    }

    assert guide_references_for_place("鑫丰特色餐饮", references)[0]["title"] == "梅州一日游攻略"
    assert guide_references_for_place("鑫丰特色餐饮（东山店）", references)[0]["guide_claim"] == "午餐可考虑鑫丰特色餐饮"
    assert guide_references_for_place("鑫丰", references) == []


@pytest.mark.asyncio
async def test_trip_guide_lookup_is_scoped_and_returns_bounded_imported_claims():
    trip_id = uuid4()
    source_id = uuid4()
    statement = None
    rows = [(
        source_id,
        "梅州一日游攻略\n午餐推荐鑫丰特色餐饮",
        "PARTIAL",
        "鑫丰特色餐饮",
        "FOOD",
        "午餐推荐",
        "午餐推荐鑫丰特色餐饮",
    )]

    class FakeSession:
        async def execute(self, query):
            nonlocal statement
            statement = query
            return SimpleNamespace(all=lambda: rows)

    result = await load_trip_guide_references(FakeSession(), trip_id)

    assert "content_sources.trip_id" in str(statement.compile(dialect=postgresql.dialect()))
    reference = result["鑫丰特色餐饮"][0]
    assert reference == {
        "source_id": str(source_id), "title": "梅州一日游攻略",
        "place_name": "鑫丰特色餐饮", "category": "FOOD",
        "guide_claim": "午餐推荐", "evidence_status": "PARTIAL_GUIDE",
    }


@pytest.mark.asyncio
async def test_advisor_reply_receives_and_labels_imported_guide_claims(monkeypatch):
    requests = []

    class Response:
        def raise_for_status(self):
            return None

        def json(self):
            return {"usage": {"total_tokens": 20, "completion_tokens": 8}, "choices": [{
                "message": {"content": '{"reply":"《梅州攻略》提到了鑫丰特色餐饮；这只是攻略线索，营业信息仍需核实。"}'},
            }]}

    class Client:
        def __init__(self, **_kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def post(self, _url, **kwargs):
            requests.append(kwargs["json"])
            return Response()

    monkeypatch.setattr(httpx, "AsyncClient", Client)
    reference = {
        "source_id": "source-1", "title": "梅州攻略", "place_name": "鑫丰特色餐饮",
        "category": "FOOD", "guide_claim": "推荐午餐", "evidence_status": "IMPORTED_GUIDE",
    }
    result = await compose_advisor_reply_with_diagnostics(
        {
            "current_message": "给我推荐午餐",
            "requested_category": "FOOD", "requested_meal_type": "LUNCH",
            "verified_candidates": [{
                "name": "鑫丰特色餐饮", "provider": "amap", "verification_status": "VERIFIED",
                "planning_category": "FOOD", "guide_references": [reference],
            }],
        },
        api_key="test", model="test", base_url="https://example.invalid/v1", token_budget=128,
    )

    assert result.reply is not None
    serialized = requests[0]["messages"][1]["content"]
    assert "梅州攻略" in serialized and "推荐午餐" in serialized
    assert "label this as a guide claim" in requests[0]["messages"][0]["content"]
