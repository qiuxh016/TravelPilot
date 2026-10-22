from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi import HTTPException
from travelpilot_api.api.v1 import advisor_drafts
from travelpilot_api.schemas.advisor import AdvisorDraftCreate


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("expected_draft_version", "expected_context_revision", "detail"),
    [
        (2, 5, "草案已被更新"),
        (3, 4, "旅行条件已被更新"),
    ],
)
async def test_create_draft_rejects_stale_client_snapshot(
    monkeypatch, expected_draft_version, expected_context_revision, detail,
):
    session = SimpleNamespace(
        id=uuid4(),
        status="ACTIVE",
        draft_version=3,
        draft={"version": 3, "context_revision": 5},
        context={"revision": 5},
    )

    async def owned_session(*args, **kwargs):
        return session

    monkeypatch.setattr(advisor_drafts, "owned_session", owned_session)

    with pytest.raises(HTTPException, match=detail) as error:
        await advisor_drafts.create_draft(
            session.id,
            AdvisorDraftCreate(
                candidate_ids=[uuid4()],
                expected_draft_version=expected_draft_version,
                expected_context_revision=expected_context_revision,
            ),
            db=object(),
            user=SimpleNamespace(id=uuid4()),
            maps=object(),
            weather=object(),
        )

    assert error.value.status_code == 409
