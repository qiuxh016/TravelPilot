from uuid import uuid4

import pytest
from travelpilot_api.advisor.candidate_identity import provider_candidate_id


def test_provider_candidate_id_is_stable_across_retry_and_provider_case_changes():
    session_id = uuid4()

    first = provider_candidate_id(
        session_id=session_id, provider="AMap", provider_id="place-123",
    )
    retried = provider_candidate_id(
        session_id=session_id, provider="amap", provider_id="place-123",
    )

    assert first == retried


def test_provider_candidate_id_is_scoped_to_session_and_provider_place():
    session_id, other_session_id = uuid4(), uuid4()
    original = provider_candidate_id(
        session_id=session_id, provider="amap", provider_id="place-123",
    )

    assert original != provider_candidate_id(
        session_id=other_session_id, provider="amap", provider_id="place-123",
    )
    assert original != provider_candidate_id(
        session_id=session_id, provider="amap", provider_id="place-456",
    )
    assert original != provider_candidate_id(
        session_id=session_id, provider="other", provider_id="place-123",
    )


@pytest.mark.parametrize(("provider", "provider_id"), [("", "id"), ("amap", " ")])
def test_provider_candidate_id_rejects_missing_provider_identity(provider, provider_id):
    with pytest.raises(ValueError, match="provider and provider place ID"):
        provider_candidate_id(
            session_id=uuid4(), provider=provider, provider_id=provider_id,
        )
