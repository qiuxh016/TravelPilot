from datetime import date
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi import Response
from travelpilot_api.api.v1 import auth as auth_routes
from travelpilot_api.api.v1.auth import export_account
from travelpilot_infra.db.models.advisor import (
    AdvisorMessageModel,
    AdvisorRunEventModel,
    AdvisorSessionModel,
)
from travelpilot_infra.db.models.social import FriendConversationModel, FriendMessageModel
from travelpilot_infra.db.models.trip import TripModel
from travelpilot_infra.db.models.trip_collaboration import (
    TripActivityModel,
    TripDiscussionMessageModel,
)
from travelpilot_infra.db.models.trip_member import TripMemberModel
from travelpilot_infra.db.models.user import UserModel


class _Rows:
    def __init__(self, rows=()):
        self.rows = list(rows)

    def all(self):
        return self.rows


class _ExportSession:
    def __init__(self, advisor_event, advisor_session=None, advisor_message=None, export_rows=None):
        self.statements = []
        self.advisor_event = advisor_event
        self.advisor_session = advisor_session
        self.advisor_message = advisor_message
        self.export_rows = export_rows or {}
        self.trip_queries = iter(self.export_rows.get("trip_queries", []))

    async def scalars(self, statement):
        self.statements.append(statement)
        entity = statement.column_descriptions[0]["entity"]
        if entity is TripModel:
            return _Rows(next(self.trip_queries, []))
        if entity is AdvisorRunEventModel:
            return _Rows([self.advisor_event])
        if entity is AdvisorSessionModel:
            return _Rows([self.advisor_session] if self.advisor_session else [])
        if entity is AdvisorMessageModel:
            return _Rows([self.advisor_message] if self.advisor_message else [])
        return _Rows(self.export_rows.get(entity, []))

    async def get(self, _model, _key):
        return None


class _DeleteResult:
    def all(self):
        return []


class _DeleteSession:
    def __init__(self, conversation_id):
        self.statements = []
        self.committed = False
        self.conversation_id = conversation_id
        self.select_count = 0

    async def execute(self, statement):
        self.statements.append(statement)
        if statement.is_select:
            self.select_count += 1
            keys = (
                ("moment-owner", "moment-thumb-owner")
                if self.select_count == 1
                else ("friend-media", "friend-thumb")
            )
            return _Rows([keys])
        return _DeleteResult()

    async def scalars(self, statement):
        self.statements.append(statement)
        entity = statement.column_descriptions[0]["entity"]
        return _Rows([self.conversation_id] if entity is FriendConversationModel else [])

    async def commit(self):
        self.committed = True


@pytest.mark.asyncio
async def test_account_export_is_owner_scoped_and_includes_advisor_data_once():
    owner_id = uuid4()
    user = SimpleNamespace(
        id=owner_id,
        email="traveler@example.invalid",
        username="traveler",
        created_at=None,
        display_name="Traveler",
        home_city=None,
        bio=None,
        password_hash="must-not-be-exported",
        timezone="Asia/Shanghai",
        trip_reminders_enabled=True,
    )
    event_id = uuid4()
    advisor_event = AdvisorRunEventModel(
        id=event_id,
        owner_id=owner_id,
        session_id=uuid4(),
        phase="search",
        operation="advisor_agent_loop",
        status="SUCCEEDED",
        duration_ms=17,
        error_code=None,
        token_usage=23,
        metadata_json={"tool": "search_places"},
    )
    advisor_session_id = uuid4()
    advisor_session = AdvisorSessionModel(
        id=advisor_session_id, owner_id=owner_id, mode="existing", destination="成都",
        pace_level=3, preference_text="", candidates=[], context={}, draft_version=0,
        status="ACTIVE",
    )
    advisor_message = AdvisorMessageModel(
        id=uuid4(), session_id=advisor_session_id, owner_id=owner_id, role="assistant",
        content="已为你整理建议。", payload={
            "advisor_feedback": {
                "rating": "NOT_HELPFUL", "reason": "NOT_ACTIONABLE",
                "submitted_at": "2026-10-20T08:00:00+00:00",
            },
        },
    )
    owned_trip = TripModel(
        id=uuid4(), owner_id=owner_id, destination="自己的旅行",
        start_date=date(2026, 11, 1), end_date=date(2026, 11, 2), pace_level=3,
    )
    shared_trip_id = uuid4()
    shared_trip = TripModel(
        id=shared_trip_id, owner_id=uuid4(), destination="好友共享旅行",
        start_date=date(2026, 12, 1), end_date=date(2026, 12, 2), pace_level=2,
    )
    accepted_shared_trip_id = uuid4()
    shared_trip.id = accepted_shared_trip_id
    accepted_membership = TripMemberModel(
        id=uuid4(), trip_id=accepted_shared_trip_id, user_id=owner_id,
        invited_by_id=shared_trip.owner_id, role="EDITOR", status="ACCEPTED",
    )
    revoked_membership = TripMemberModel(
        id=uuid4(), trip_id=uuid4(), user_id=owner_id,
        invited_by_id=shared_trip.owner_id, role="VIEWER", status="REVOKED",
    )
    discussion = TripDiscussionMessageModel(
        id=uuid4(), trip_id=owned_trip.id, author_user_id=owner_id,
        author_display_name="Traveler", body="共享行程请确认集合时间",
    )
    activity = TripActivityModel(
        id=uuid4(), trip_id=accepted_shared_trip_id, actor_user_id=owner_id,
        actor_display_name="Traveler", event_type="PLAN_UPDATED", summary="调整了计划",
        payload={"version": 2},
    )
    session = _ExportSession(
        advisor_event, advisor_session, advisor_message,
        export_rows={
            TripModel: [owned_trip],
            "trip_queries": [[owned_trip], [shared_trip]],
            TripMemberModel: [accepted_membership, revoked_membership],
            TripDiscussionMessageModel: [discussion],
            TripActivityModel: [activity],
        },
    )

    payload = await export_account(user=user, session=session)

    assert payload["format"] == "travelpilot-account-export-v1"
    assert payload["user"] == {
        "email": user.email,
        "username": user.username,
        "created_at": None,
        "display_name": "Traveler",
        "home_city": None,
        "bio": None,
        "timezone": "Asia/Shanghai",
        "trip_reminders_enabled": True,
    }
    assert "password_hash" not in payload["user"]
    assert {"advisor_sessions", "advisor_messages", "advisor_run_events"} <= {
        *payload.keys(), *payload["data"].keys()
    }
    assert payload["data"]["advisor_run_events"][0]["id"] == str(event_id)
    assert payload["data"]["advisor_run_events"][0]["metadata_json"] == {"tool": "search_places"}
    assert payload["data"]["advisor_messages"][0]["payload"]["advisor_feedback"] == {
        "rating": "NOT_HELPFUL", "reason": "NOT_ACTIONABLE",
        "submitted_at": "2026-10-20T08:00:00+00:00",
    }
    assert payload["data"]["trip_discussion_messages"][0]["body"] == "共享行程请确认集合时间"
    assert payload["data"]["trip_activities"][0]["payload"] == {"version": 2}
    assert payload["shared_trips"][0]["destination"] == "好友共享旅行"
    assert [row["status"] for row in payload["data"]["trip_memberships"]] == ["ACCEPTED", "REVOKED"]

    advisor_event_queries = [
        statement for statement in session.statements
        if statement.column_descriptions[0]["entity"] is AdvisorRunEventModel
    ]
    assert len(advisor_event_queries) == 1
    assert str(owner_id) in str(advisor_event_queries[0].compile().params.values())

    trip_query = next(statement for statement in session.statements
                      if statement.column_descriptions[0]["entity"] is TripModel)
    assert str(owner_id) in str(trip_query.compile().params.values())
    group_queries = [
        statement for statement in session.statements
        if statement.column_descriptions[0]["entity"] in {
            TripDiscussionMessageModel, TripActivityModel,
        }
    ]
    assert len(group_queries) == 2
    assert all(
        str(owned_trip.id) in str(statement.compile().params.values())
        and str(accepted_shared_trip_id) in str(statement.compile().params.values())
        and str(revoked_membership.trip_id) not in str(statement.compile().params.values())
        for statement in group_queries
    )


@pytest.mark.asyncio
async def test_account_deletion_scopes_reads_cleans_owned_media_and_revokes_cookies(monkeypatch):
    owner_id = uuid4()
    user = SimpleNamespace(
        id=owner_id,
        avatar_key="avatar-owner",
        avatar_thumbnail_key="avatar-thumb-owner",
    )
    conversation_id = uuid4()
    session = _DeleteSession(conversation_id)
    removed_keys = []
    monkeypatch.setattr(auth_routes, "remove_media_keys", removed_keys.extend)
    response = Response()

    result = await auth_routes.delete_account(response=response, user=user, session=session)

    assert result.status_code == 204
    assert session.committed
    assert removed_keys == [
        "avatar-owner", "avatar-thumb-owner", "moment-owner", "moment-thumb-owner",
        "friend-media", "friend-thumb",
    ]
    assert any(
        statement.column_descriptions[0]["entity"] is FriendConversationModel
        and str(owner_id) in str(statement.compile().params.values())
        for statement in session.statements
    )
    user_deletes = [
        statement for statement in session.statements
        if getattr(statement, "table", None) == UserModel.__table__
    ]
    assert len(user_deletes) == 1
    assert str(owner_id) in str(user_deletes[0].compile().params.values())
    friend_message_queries = [
        statement for statement in session.statements
        if statement.is_select and statement.column_descriptions[0]["entity"] is FriendMessageModel
    ]
    assert len(friend_message_queries) == 1
    assert str(conversation_id) in str(friend_message_queries[0].compile().params.values())
    set_cookie_headers = [value.decode() for key, value in response.raw_headers if key.lower() == b"set-cookie"]
    assert len(set_cookie_headers) == 2
    assert all("Max-Age=0" in value for value in set_cookie_headers)


def test_advisor_feedback_is_removed_by_existing_account_cascade():
    owner_foreign_keys = [
        foreign_key for foreign_key in AdvisorMessageModel.__table__.foreign_keys
        if foreign_key.column.table is UserModel.__table__
    ]

    assert len(owner_foreign_keys) == 1
    assert owner_foreign_keys[0].ondelete == "CASCADE"


def test_group_trip_history_survives_member_account_deletion_but_owned_trip_cascades():
    for model in (TripDiscussionMessageModel, TripActivityModel):
        foreign_keys = {key.column.table.name: key.ondelete for key in model.__table__.foreign_keys}
        assert foreign_keys["trips"] == "CASCADE"
        assert foreign_keys["users"] == "SET NULL"

    membership_foreign_keys = list(TripMemberModel.__table__.foreign_keys)
    assert any(key.column.table.name == "trips" and key.ondelete == "CASCADE"
               for key in membership_foreign_keys)
    assert any(key.column.table.name == "users" and key.ondelete == "CASCADE"
               for key in membership_foreign_keys)
    assert any(key.column.table.name == "users" and key.ondelete == "SET NULL"
               for key in membership_foreign_keys)
