import json

from travelpilot_api.advisor.intent_checkpoint import (
    _deserialize_extraction,
    _serialize_extraction,
    intent_checkpoint_scope_ref,
)
from travelpilot_core.agents.extraction import Extraction, PlaceMention


def test_intent_scope_changes_for_turn_facts_that_can_change_extraction():
    common = {
        "goal": "请安排宽窄巷子",
        "conversation_context": [{"role": "user", "content": "偏好自然风景"}],
        "personal_memories": [{"constraint_type": "PREFERENCE", "text": "喜欢自然"}],
        "context_revision": 3,
        "token_budget": 700,
        "model": "travel-model",
        "base_url": "https://models.example/v1",
    }
    baseline = intent_checkpoint_scope_ref(**common)

    assert intent_checkpoint_scope_ref(**{**common, "goal": "请安排武侯祠"}) != baseline
    assert intent_checkpoint_scope_ref(**{**common, "context_revision": 4}) != baseline
    assert intent_checkpoint_scope_ref(**{**common, "token_budget": 500}) != baseline
    assert intent_checkpoint_scope_ref(**{**common, "model": "another-model"}) != baseline
    assert intent_checkpoint_scope_ref(**{
        **common,
        "personal_memories": [{"constraint_type": "HARD_LIMIT", "text": "不要爬山"}],
    }) == intent_checkpoint_scope_ref(**{
        **common,
        "personal_memories": [],
    })


def test_intent_checkpoint_restores_evidence_from_offsets_without_persisting_user_text():
    goal = "请把宽窄巷子加入第一天，午餐偏好川菜。"
    evidence = "宽窄巷子加入第一天"
    extraction = Extraction(
        places=[PlaceMention(
            name="宽窄巷子", confidence=0.94, action="add", evidence=evidence,
        )],
        search_queries=["川菜午餐"],
        intent="schedule",
        pace_adjustment="none",
        token_usage=242,
        output_tokens=52,
    )

    payload = _serialize_extraction(extraction, goal)

    assert payload is not None
    assert evidence not in json.dumps(payload, ensure_ascii=False)
    restored = _deserialize_extraction(payload, goal)
    assert restored is not None
    assert restored.places[0].evidence == evidence
    assert restored.intent == extraction.intent
    assert restored.search_queries == extraction.search_queries
    assert restored.token_usage == extraction.token_usage
    assert restored.output_tokens == extraction.output_tokens


def test_intent_checkpoint_rejects_invalid_evidence_offsets_and_unbounded_fields():
    payload = {
        "places": [{
            "name": "宽窄巷子", "category": "ATTRACTION", "suggestion": None,
            "confidence": 0.9, "action": "add", "evidence_span": [1, 99],
        }],
        "search_queries": [], "intent": "schedule", "pace_adjustment": "none",
        "total_tokens": 30, "output_tokens": 10,
    }

    assert _deserialize_extraction(payload, "请去宽窄巷子") is None
    assert _serialize_extraction(
        Extraction(places=[], search_queries=["x" * 161]), "搜索地点",
    ) is None
