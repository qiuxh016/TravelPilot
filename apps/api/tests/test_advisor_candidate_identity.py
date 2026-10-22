from uuid import uuid4

import pytest
from travelpilot_api.advisor.candidate_identity import (
    candidate_assignment_identity,
    provider_candidate_id,
)


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


def test_same_provider_restaurant_has_independent_lunch_and_dinner_assignments():
    session_id = uuid4()
    lunch_id, lunch_match = candidate_assignment_identity(
        session_id=session_id,
        provider="amap",
        provider_id="restaurant-123",
        existing_candidates=[],
        meal_type="LUNCH",
    )
    lunch = {
        "id": lunch_id,
        "provider": "amap",
        "provider_id": "restaurant-123",
        "meal_type": "LUNCH",
    }

    dinner_id, dinner_match = candidate_assignment_identity(
        session_id=session_id,
        provider="amap",
        provider_id="restaurant-123",
        existing_candidates=[lunch],
        meal_type="DINNER",
    )
    retried_dinner_id, retried_dinner_match = candidate_assignment_identity(
        session_id=session_id,
        provider="amap",
        provider_id="restaurant-123",
        existing_candidates=[lunch, {
            **lunch,
            "id": dinner_id,
            "meal_type": "DINNER",
        }],
        meal_type="DINNER",
    )

    assert lunch_match is None
    assert dinner_match is None
    assert dinner_id != lunch_id
    assert retried_dinner_id == dinner_id
    assert retried_dinner_match is not None


def test_unassigned_provider_candidate_keeps_base_identity_when_given_a_meal():
    session_id = uuid4()
    candidate_id = provider_candidate_id(
        session_id=session_id,
        provider="amap",
        provider_id="restaurant-123",
    )
    candidate = {
        "id": candidate_id,
        "provider": "amap",
        "provider_id": "restaurant-123",
        "meal_type": None,
    }

    resolved_id, resolved = candidate_assignment_identity(
        session_id=session_id,
        provider="amap",
        provider_id="restaurant-123",
        existing_candidates=[candidate],
        meal_type="LUNCH",
    )

    assert resolved_id == candidate_id
    assert resolved is candidate


@pytest.mark.parametrize(("provider", "provider_id"), [("", "id"), ("amap", " ")])
def test_provider_candidate_id_rejects_missing_provider_identity(provider, provider_id):
    with pytest.raises(ValueError, match="provider and provider place ID"):
        provider_candidate_id(
            session_id=uuid4(), provider=provider, provider_id=provider_id,
        )
