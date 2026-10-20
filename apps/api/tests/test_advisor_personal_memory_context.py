from types import SimpleNamespace
from uuid import uuid4

import pytest
from travelpilot_api.advisor.context import sync_personal_memory_snapshot
from travelpilot_api.advisor.interpretation import _active_personal_memories
from travelpilot_api.advisor.turn_service import _reviewable_memory_suggestion
from travelpilot_core.agents.advisor_reply import AdvisorMemorySuggestion, AdvisorReply


def test_personal_memories_refresh_only_when_opted_in_snapshot_changes():
    previous = {"revision": 4, "personal_memories": [{
        "id": "m1", "text": "偏好自然风景", "constraint_type": "PREFERENCE",
    }]}

    unchanged, changed = sync_personal_memory_snapshot(previous, [{
        "id": "m1", "text": "偏好自然风景", "constraint_type": "PREFERENCE",
    }])
    assert changed is False
    assert unchanged["revision"] == 4

    cleared, changed = sync_personal_memory_snapshot(previous, [])
    assert changed is True
    assert cleared["personal_memories"] == []
    assert cleared["revision"] == 5
    assert previous["personal_memories"] == [{
        "id": "m1", "text": "偏好自然风景", "constraint_type": "PREFERENCE",
    }]


def test_personal_memory_snapshot_is_bounded_and_increments_context_revision():
    memories = [
        {"id": f"hard-{index}", "text": "x", "constraint_type": "HARD_LIMIT"}
        for index in range(6)
    ] + [
        {"id": f"pref-{index}", "text": "y", "constraint_type": "PREFERENCE"}
        for index in range(6)
    ]

    snapshot, changed = sync_personal_memory_snapshot({"revision": 9}, memories)

    assert changed is True
    assert len(snapshot["personal_memories"]) == 10
    assert [item["constraint_type"] for item in snapshot["personal_memories"]] == [
        "HARD_LIMIT", "HARD_LIMIT", "HARD_LIMIT", "HARD_LIMIT", "HARD_LIMIT",
        "PREFERENCE", "PREFERENCE", "PREFERENCE", "PREFERENCE", "PREFERENCE",
    ]
    assert snapshot["revision"] == 10


def test_hidden_memory_change_invalidates_revision_even_if_prompt_subset_is_unchanged():
    memories = [
        {"id": f"hard-{index}", "text": f"约束{index}", "constraint_type": "HARD_LIMIT"}
        for index in range(6)
    ]
    initial, changed = sync_personal_memory_snapshot({"revision": 3}, memories)
    assert changed is True
    revised_memories = [{**item} for item in memories]
    revised_memories[-1]["text"] = "更新后的约束"

    refreshed, changed = sync_personal_memory_snapshot(initial, revised_memories)

    assert changed is True
    assert refreshed["revision"] == 5
    assert refreshed["personal_memories"] == initial["personal_memories"]
    assert refreshed["personal_memory_fingerprint"] != initial["personal_memory_fingerprint"]


class _Rows:
    def __init__(self, rows):
        self.rows = rows

    def all(self):
        return self.rows


class _MemoryReadSession:
    def __init__(self, rows):
        self.rows = iter(rows)
        self.queries = []

    async def get(self, _model, _owner_id):
        return SimpleNamespace(enabled=True)

    async def scalars(self, query):
        self.queries.append(query)
        rows = next(self.rows)
        limit = query._limit_clause
        return _Rows(rows[:limit.value] if limit is not None else rows)


@pytest.mark.asyncio
async def test_disabled_memory_setting_prevents_all_memory_reads():
    class DisabledSession:
        def __init__(self):
            self.queries = []

        async def get(self, _model, _owner_id):
            return SimpleNamespace(enabled=False)

        async def scalars(self, query):
            self.queries.append(query)
            raise AssertionError("disabled memory must not query stored preferences")

    session = DisabledSession()
    assert await _active_personal_memories(session, uuid4()) == []
    assert session.queries == []


@pytest.mark.asyncio
async def test_active_memory_read_keeps_all_hard_limits_and_caps_soft_preferences():
    hard = [SimpleNamespace(
        id=uuid4(), category="GENERAL", text=f"硬限制{index}",
        constraint_type="HARD_LIMIT", expires_at=None,
    ) for index in range(12)]
    preferences = [SimpleNamespace(
        id=uuid4(), category="GENERAL", text=f"偏好{index}",
        constraint_type="PREFERENCE", expires_at=None,
    ) for index in range(7)]
    session = _MemoryReadSession([hard, preferences])

    active = await _active_personal_memories(session, uuid4())

    assert len(active) == 17
    assert [item["constraint_type"] for item in active] == [
        *(["HARD_LIMIT"] * 12), *(["PREFERENCE"] * 5),
    ]
    hard_params = set(session.queries[0].compile().params.values())
    preference_params = set(session.queries[1].compile().params.values())
    assert "HARD_LIMIT" in hard_params
    assert "PREFERENCE" in preference_params
    assert session.queries[0]._limit_clause is None
    assert session.queries[1]._limit_clause.value == 5


def test_model_memory_suggestion_requires_user_opt_in_and_verbatim_evidence():
    reply = AdvisorReply(
        reply="我会留意你的偏好。",
        memory_suggestion=AdvisorMemorySuggestion(
            category="PACE", text="偏好轻松节奏", evidence="我喜欢轻松一点",
        ),
    )

    assert _reviewable_memory_suggestion(
        reply, enabled=True, user_message="我喜欢轻松一点，也想逛博物馆",
    ) == {
        "category": "PACE", "text": "偏好轻松节奏", "evidence": "我喜欢轻松一点",
    }
    assert _reviewable_memory_suggestion(
        reply, enabled=False, user_message="我喜欢轻松一点",
    ) is None
    assert _reviewable_memory_suggestion(
        reply, enabled=True, user_message="这次想轻松一点",
    ) is None
