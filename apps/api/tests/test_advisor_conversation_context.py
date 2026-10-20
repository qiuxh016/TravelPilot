from types import SimpleNamespace

import pytest
from travelpilot_api.advisor.conversation import (
    recent_conversation_context,
    recent_verified_candidate_context,
)


def test_recent_conversation_is_ordered_bounded_and_excludes_non_dialogue_payloads():
    rows = [
        SimpleNamespace(role="user", content="  我喜欢轻松一点  ", payload={"secret": "ignored"}),
        SimpleNamespace(role="assistant", content="可以减少跨城区安排。", payload={"tool_calls": []}),
        SimpleNamespace(role="system", content="internal prompt"),
        SimpleNamespace(role="user", content="那哪个更适合带孩子？"),
    ]

    history = recent_conversation_context(rows)

    assert history == [
        {"role": "user", "content": "我喜欢轻松一点"},
        {"role": "assistant", "content": "可以减少跨城区安排。"},
        {"role": "user", "content": "那哪个更适合带孩子？"},
    ]
    assert all(set(item) == {"role", "content"} for item in history)


def test_recent_conversation_keeps_only_last_bounded_turns_and_characters():
    rows = [SimpleNamespace(role="user", content=f"{index}:" + "x" * 500) for index in range(9)]

    history = recent_conversation_context(rows)

    assert len(history) == 6
    assert all(len(item["content"]) == 400 for item in history)
    assert history[0]["content"].endswith("x" * 398)
    assert history[-1]["content"].startswith("x" * 400)


def test_recent_candidates_reuse_only_latest_verified_result_and_bound_fields():
    rows = [
        SimpleNamespace(
            role="assistant",
            content="之前搜索",
            payload={"verified_candidates": [{
                "name": "旧地点", "provider": "amap", "verification_status": "VERIFIED",
            }]},
        ),
        SimpleNamespace(role="user", content="哪个更适合孩子？", payload={}),
        SimpleNamespace(
            role="assistant",
            content="刚才的搜索",
            payload={"verified_candidates": [
                {
                    "name": " 博物馆 ", "address": "路" * 400, "category": "文化" * 100,
                    "planning_category": "ATTRACTION",
                    "popularity_score": 4.7, "provider": "amap", "verification_status": "VERIFIED",
                },
                {
                    "name": "午餐店", "provider": "fake", "verification_status": "SIMULATED",
                    "planning_category": "FOOD", "meal_type": "LUNCH",
                },
                {"name": "假结果", "provider": "unknown", "verification_status": "VERIFIED"},
                {"name": "坏分数", "provider": "fake", "verification_status": "SIMULATED",
                 "popularity_score": float("nan")},
            ]},
        ),
    ]

    candidates = recent_verified_candidate_context(rows)

    assert candidates == [
        {
            "name": "博物馆", "address": "路" * 240, "category": "文化" * 60,
            "planning_category": "ATTRACTION",
            "popularity_score": 4.7, "provider": "amap", "verification_status": "VERIFIED",
            "evidence_scope": "PRIOR_TURN",
        },
        {
            "name": "午餐店", "provider": "fake", "verification_status": "SIMULATED",
            "planning_category": "FOOD", "meal_type": "LUNCH", "evidence_scope": "PRIOR_TURN",
        },
        {
            "name": "坏分数", "provider": "fake", "verification_status": "SIMULATED",
            "evidence_scope": "PRIOR_TURN",
        },
    ]


def test_empty_latest_candidate_search_does_not_resurrect_older_candidates():
    rows = [
        SimpleNamespace(role="assistant", content="有结果", payload={"verified_candidates": [
            {"name": "旧地点", "provider": "fake", "verification_status": "SIMULATED"},
        ]}),
        SimpleNamespace(role="assistant", content="无结果", payload={"verified_candidates": []}),
    ]

    assert recent_verified_candidate_context(rows) == []
    assert recent_verified_candidate_context(rows, limit=0) == []


def test_latest_assistant_turn_without_candidate_payload_invalidates_old_evidence():
    rows = [
        SimpleNamespace(role="assistant", content="有结果", payload={"verified_candidates": [
            {"name": "旧餐厅", "provider": "fake", "verification_status": "SIMULATED"},
        ]}),
        SimpleNamespace(role="user", content="谢谢", payload={}),
        SimpleNamespace(role="assistant", content="不客气。", payload={"response_source": "TEMPLATE"}),
    ]

    assert recent_verified_candidate_context(rows) == []


def test_advisor_memory_suggestion_schema_only_allows_supported_non_sensitive_categories():
    from pydantic import ValidationError
    from travelpilot_core.agents.advisor_reply import AdvisorReply

    reply = AdvisorReply.model_validate({
        "reply": "我会尽量按轻松节奏推荐。",
        "memory_suggestion": {
            "category": "PACE", "text": "偏好轻松节奏", "evidence": "我喜欢轻松一点",
        },
    })

    assert reply.memory_suggestion is not None
    assert reply.memory_suggestion.category == "PACE"
    with pytest.raises(ValidationError):
        AdvisorReply.model_validate({
            "reply": "好的。",
            "memory_suggestion": {"category": "HEALTH", "text": "有健康状况"},
        })
