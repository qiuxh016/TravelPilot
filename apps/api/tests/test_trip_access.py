from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from fastapi import HTTPException
from travelpilot_api.api.v1.trip_members import accept_trip_invitation, invite_trip_member
from travelpilot_api.schemas.trip_members import TripMemberInvite, TripMemberRoleUpdate
from travelpilot_api.trip_access import get_trip_access, role_allows


class TripAccessSessionDouble:
    def __init__(self, trip, member=None):
        self.trip = trip
        self.member = member
        self.calls = 0
        self.statements = []

    async def scalar(self, statement):
        self.statements.append(statement)
        if self.calls == 0:
            result = self.trip
        else:
            result = self.member if getattr(self.member, "status", None) == "ACCEPTED" else None
        self.calls += 1
        return result


@pytest.mark.parametrize(
    ("actual", "required", "allowed"),
    [
        ("OWNER", "OWNER", True),
        ("OWNER", "EDITOR", True),
        ("OWNER", "VIEWER", True),
        ("EDITOR", "EDITOR", True),
        ("EDITOR", "VIEWER", True),
        ("EDITOR", "OWNER", False),
        ("VIEWER", "VIEWER", True),
        ("VIEWER", "EDITOR", False),
        ("UNKNOWN", "VIEWER", False),
    ],
)
def test_trip_role_hierarchy(actual, required, allowed):
    assert role_allows(actual, required) is allowed


@pytest.mark.asyncio
async def test_trip_owner_has_implicit_owner_role_without_membership_row():
    owner_id = uuid4()
    trip = SimpleNamespace(id=uuid4(), owner_id=owner_id)
    session = TripAccessSessionDouble(trip)

    access = await get_trip_access(
        session, trip_id=trip.id, user_id=owner_id, required_role="OWNER",
    )

    assert access.trip is trip
    assert access.role == "OWNER"
    assert access.membership is None
    assert session.calls == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("role", ["EDITOR", "VIEWER"])
async def test_accepted_members_get_only_their_current_role(role):
    owner_id, member_id, trip_id = uuid4(), uuid4(), uuid4()
    trip = SimpleNamespace(id=trip_id, owner_id=owner_id)
    member = SimpleNamespace(trip_id=trip_id, user_id=member_id, role=role, status="ACCEPTED")
    session = TripAccessSessionDouble(trip, member)

    access = await get_trip_access(
        session, trip_id=trip_id, user_id=member_id,
        required_role="VIEWER" if role == "VIEWER" else "EDITOR",
    )

    assert access.role == role
    assert access.membership is member
    assert "status = :status_1" in str(session.statements[1])


@pytest.mark.asyncio
async def test_viewer_cannot_get_editor_access():
    owner_id, member_id, trip_id = uuid4(), uuid4(), uuid4()
    trip = SimpleNamespace(id=trip_id, owner_id=owner_id)
    member = SimpleNamespace(trip_id=trip_id, user_id=member_id, role="VIEWER", status="ACCEPTED")

    with pytest.raises(HTTPException) as error:
        await get_trip_access(
            TripAccessSessionDouble(trip, member), trip_id=trip_id,
            user_id=member_id, required_role="EDITOR",
        )

    assert error.value.status_code == 403


@pytest.mark.asyncio
@pytest.mark.parametrize("status", ["PENDING", "DECLINED", "REVOKED"])
async def test_non_accepted_member_has_no_trip_access(status):
    owner_id, member_id, trip_id = uuid4(), uuid4(), uuid4()
    trip = SimpleNamespace(id=trip_id, owner_id=owner_id)
    member = SimpleNamespace(trip_id=trip_id, user_id=member_id, role="EDITOR", status=status)
    session = TripAccessSessionDouble(trip, member)

    with pytest.raises(HTTPException) as error:
        await get_trip_access(session, trip_id=trip_id, user_id=member_id)

    assert error.value.status_code == 404
    assert error.value.detail == "Trip not found"


def test_invite_schema_only_allows_collaborator_roles():
    assert TripMemberInvite(user_id=uuid4()).role == "VIEWER"
    assert TripMemberRoleUpdate(role="EDITOR").role == "EDITOR"
    with pytest.raises(ValueError):
        TripMemberInvite(user_id=uuid4(), role="OWNER")


def test_trip_member_routes_are_registered():
    from travelpilot_api.main import app

    paths = set(app.openapi()["paths"])
    assert "/api/v1/trips/{trip_id}/members" in paths
    assert "/api/v1/trips/{trip_id}/members/{member_id}" in paths
    assert "/api/v1/trip-invitations" in paths
    assert "/api/v1/trip-invitations/{member_id}/accept" in paths
    assert "/api/v1/trip-invitations/{member_id}/decline" in paths


@pytest.mark.asyncio
async def test_owner_invite_creates_pending_member_with_selected_role():
    owner_id, friend_id, trip_id = uuid4(), uuid4(), uuid4()
    trip = SimpleNamespace(id=trip_id, owner_id=owner_id)
    friend = SimpleNamespace(
        id=friend_id, is_active=True, email_verified=True, username="friend",
        display_name="旅行好友", avatar_thumbnail_key=None,
    )

    class Session:
        def __init__(self):
            self.scalar_results = iter([trip, uuid4(), None])
            self.added = []
            self.commit = AsyncMock()
            self.refresh = AsyncMock(side_effect=self._refresh)

        async def _refresh(self, row):
            row.id = uuid4()
            row.created_at = datetime.now(UTC)
            row.updated_at = row.created_at

        async def scalar(self, _statement):
            return next(self.scalar_results)

        async def get(self, _model, user_id):
            assert user_id == friend_id
            return friend

        def add(self, row):
            self.added.append(row)

    session = Session()
    result = await invite_trip_member(
        trip_id, TripMemberInvite(user_id=friend_id, role="EDITOR"), session,  # type: ignore[arg-type]
        SimpleNamespace(id=owner_id),  # type: ignore[arg-type]
    )

    assert result.user_id == friend_id
    assert result.role == "EDITOR"
    assert result.status == "PENDING"
    assert session.added[0].invited_by_id == owner_id
    session.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_editor_cannot_invite_another_member():
    owner_id, editor_id, trip_id = uuid4(), uuid4(), uuid4()
    trip = SimpleNamespace(id=trip_id, owner_id=owner_id)
    editor_membership = SimpleNamespace(role="EDITOR", status="ACCEPTED")

    # The explicit stub keeps the query sequence visible: trip lookup, then membership lookup.
    class Session:
        def __init__(self):
            self.results = iter([trip, editor_membership])

        async def scalar(self, _statement):
            return next(self.results)

    with pytest.raises(HTTPException) as error:
        await invite_trip_member(
            trip_id, TripMemberInvite(user_id=uuid4()), Session(),  # type: ignore[arg-type]
            SimpleNamespace(id=editor_id),  # type: ignore[arg-type]
        )

    assert error.value.status_code == 403


@pytest.mark.asyncio
async def test_invitation_acceptance_is_scoped_to_invited_user():
    invitee_id, member_id = uuid4(), uuid4()

    class Session:
        def __init__(self):
            self.commit = AsyncMock()

        async def scalar(self, _statement):
            return None

    session = Session()
    with pytest.raises(HTTPException) as error:
        await accept_trip_invitation(
            member_id, session, SimpleNamespace(id=invitee_id),  # type: ignore[arg-type]
        )

    assert error.value.status_code == 404
    session.commit.assert_not_awaited()


@pytest.mark.asyncio
async def test_acceptance_changes_only_pending_invitation_after_friendship_check():
    invitee_id, inviter_id, member_id = uuid4(), uuid4(), uuid4()
    member = SimpleNamespace(
        id=member_id, invited_by_id=inviter_id, user_id=invitee_id,
        status="PENDING", accepted_at=None,
    )

    class Session:
        def __init__(self):
            self.results = iter([member, uuid4()])
            self.commit = AsyncMock()

        async def scalar(self, _statement):
            return next(self.results)

    session = Session()
    result = await accept_trip_invitation(
        member_id, session, SimpleNamespace(id=invitee_id),  # type: ignore[arg-type]
    )

    assert result.message == "已接受旅行邀请。"
    assert member.status == "ACCEPTED"
    assert member.accepted_at is not None
    session.commit.assert_awaited_once()
