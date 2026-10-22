from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi import HTTPException
from travelpilot_api.api.v1 import advisor
from travelpilot_api.schemas.advisor import AdvisorSelectionUpdate


@pytest.mark.asyncio
async def test_update_selection_rejects_stale_context_revision(monkeypatch):
    session = SimpleNamespace(
        id=uuid4(),
        status="ACTIVE",
        context={"revision": 4},
    )

    async def owned_session(*args, **kwargs):
        return session

    monkeypatch.setattr(advisor, "owned_session", owned_session)

    with pytest.raises(HTTPException, match="旅行选择已在其他窗口更新") as error:
        await advisor.update_session_selection(
            session.id,
            AdvisorSelectionUpdate(
                selected_candidate_ids=[],
                expected_context_revision=3,
            ),
            db=object(),
            user=SimpleNamespace(id=uuid4()),
        )

    assert error.value.status_code == 409

