from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi import HTTPException
from travelpilot_api.api.v1.memory import confirm_user_memory_suggestion
from travelpilot_api.schemas.memory import UserPreferenceMemorySuggestionConfirm
from travelpilot_infra.db.models.workflow import UserPreferenceMemoryModel


class _Session:
    def __init__(self, *, enabled: bool, existing=None):
        self.settings = SimpleNamespace(enabled=enabled)
        self.existing = existing
        self.added = []
        self.commits = 0

    async def get(self, _model, _owner_id):
        return self.settings

    async def scalar(self, _query):
        return self.existing

    def add(self, row):
        self.added.append(row)

    async def commit(self):
        self.commits += 1

    async def refresh(self, _row):
        return None


@pytest.mark.asyncio
async def test_memory_suggestion_confirmation_requires_opt_in_and_sets_user_chosen_type():
    owner = SimpleNamespace(id=uuid4())
    payload = UserPreferenceMemorySuggestionConfirm(
        category="PACE", text="  偏好 轻松的行程  ", constraint_type="HARD_LIMIT",
    )
    disabled = _Session(enabled=False)

    with pytest.raises(HTTPException) as exc:
        await confirm_user_memory_suggestion(payload, disabled, owner)

    assert exc.value.status_code == 409
    assert disabled.added == []
    assert disabled.commits == 0

    enabled = _Session(enabled=True)
    row = await confirm_user_memory_suggestion(payload, enabled, owner)

    assert isinstance(row, UserPreferenceMemoryModel)
    assert row.owner_id == owner.id
    assert row.category == "PACE"
    assert row.text == "偏好 轻松的行程"
    assert row.constraint_type == "HARD_LIMIT"
    assert row.source == "MODEL_SUGGESTED"
    assert row.confirmed is True
    assert enabled.added == [row]
    assert enabled.commits == 1


def test_memory_suggestion_schema_rejects_unapproved_categories():
    with pytest.raises(ValueError):
        UserPreferenceMemorySuggestionConfirm(
            category="HEALTH", text="敏感内容", constraint_type="PREFERENCE",
        )
