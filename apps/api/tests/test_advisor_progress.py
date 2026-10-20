import asyncio
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi import HTTPException
from travelpilot_api.api.v1 import advisor_messages


class _Result:
    def __init__(self, rows):
        self.rows = rows

    def all(self):
        return self.rows


class _DB:
    def __init__(self, rows):
        self.rows = rows
        self.statement = None

    async def scalars(self, statement):
        self.statement = statement
        return _Result(self.rows)

    async def scalar(self, _statement):
        return None


class _ScalarSequenceDB(_DB):
    def __init__(self, values):
        super().__init__([])
        self.values = iter(values)

    async def scalar(self, _statement):
        return next(self.values)


@pytest.mark.asyncio
async def test_progress_reads_only_owned_session_and_returns_safe_event(monkeypatch):
    user_id, session_id, event_id = uuid4(), uuid4(), uuid4()
    event = SimpleNamespace(
        id=event_id, phase="route_query", operation="advisor_agent_loop",
        status="VERIFIED", metadata_json={
            "task_ref": "hashed", "state": "VERIFIED", "tool": "get_driving_route",
            "observation_keys": ["get_driving_route"],
        }, created_at=datetime.now(UTC),
    )
    stage_order_event = SimpleNamespace(
        id=uuid4(), phase="turn_orchestration", operation="stage_order_selected",
        status="RECORDED", metadata_json={
            "task_ref": "hashed", "attempt_ref": "opaque-attempt",
            "context_revision": 5, "order": ["ROUTE", "SCHEDULE"],
            "decision_source": "MODEL",
        }, created_at=datetime.now(UTC),
    )
    db = _DB([event, stage_order_event])
    ownership_checks = []

    async def owned(_db, requested_session_id, requested_owner_id):
        ownership_checks.append((requested_session_id, requested_owner_id))

    monkeypatch.setattr(advisor_messages, "owned_session", owned)
    result = await advisor_messages.get_message_progress(
        session_id=session_id, db=db, user=SimpleNamespace(id=user_id),
        idempotency_key="stable-request-key",
    )

    assert ownership_checks == [(session_id, user_id)]
    route_result = next(item for item in result if item["phase"] == "route_query")
    assert route_result["id"] == event_id
    assert route_result["metadata"]["tool"] == "get_driving_route"
    assert "user_goal" not in route_result["metadata"]
    order_result = next(item for item in result if item["phase"] == "turn_orchestration")
    assert order_result["metadata"]["order"] == ["ROUTE", "SCHEDULE"]
    assert "user_goal" not in order_result["metadata"]
    assert db.statement is not None
    assert any(
        "stage_order_selected" in value
        for value in db.statement.compile().params.values()
        if isinstance(value, (list, tuple))
    )


def test_progress_keeps_only_latest_attempt_per_phase():
    rows = [
        SimpleNamespace(phase="search", metadata_json={"attempt_ref": "old", "state": "OBSERVING"}),
        SimpleNamespace(phase="search", metadata_json={"attempt_ref": "old", "state": "FAILED"}),
        SimpleNamespace(phase="schedule", metadata_json={"attempt_ref": "schedule", "state": "VERIFIED"}),
        SimpleNamespace(phase="search", metadata_json={"attempt_ref": "new", "state": "OBSERVING"}),
        SimpleNamespace(phase="search", metadata_json={"attempt_ref": "new", "state": "SUCCEEDED"}),
    ]

    selected = advisor_messages._latest_attempt_events(rows)

    assert [row.metadata_json["state"] for row in selected] == ["VERIFIED", "OBSERVING", "SUCCEEDED"]


def test_stale_turn_heartbeat_is_reported_as_interrupted_with_safe_retry_hint():
    now = datetime.now(UTC)
    events = [{
        "id": uuid4(), "phase": "turn", "operation": "advisor_agent_loop",
        "status": "TURN_HEARTBEAT", "metadata": {"state": "TURN_HEARTBEAT"},
        "created_at": now - timedelta(days=2),
    }]

    updated = advisor_messages._mark_interrupted_turn(events, now=now)

    assert updated[0]["status"] == "TURN_INTERRUPTED"
    assert updated[0]["metadata"]["state"] == "TURN_INTERRUPTED"
    assert updated[0]["metadata"]["recovery"] == "RETRY_SAME_MESSAGE_SAFELY"


def test_recent_turn_heartbeat_remains_running():
    now = datetime.now(UTC)
    events = [{
        "id": uuid4(), "phase": "turn", "operation": "advisor_agent_loop",
        "status": "TURN_HEARTBEAT", "metadata": {"state": "TURN_HEARTBEAT"},
        "created_at": now,
    }]

    updated = advisor_messages._mark_interrupted_turn(events, now=now)

    assert updated[0]["status"] == "TURN_HEARTBEAT"


def test_terminal_turn_event_prevents_older_start_from_being_marked_interrupted():
    old = datetime.now(UTC) - timedelta(days=2)
    events = [
        {"id": uuid4(), "phase": "turn", "status": "TURN_STARTED", "metadata": {}, "created_at": old},
        {"id": uuid4(), "phase": "turn", "status": "TURN_SUCCEEDED", "metadata": {}, "created_at": old},
    ]

    updated = advisor_messages._mark_interrupted_turn(events)

    assert [event["status"] for event in updated] == ["TURN_STARTED", "TURN_SUCCEEDED"]


@pytest.mark.asyncio
async def test_turn_heartbeat_writer_emits_liveness_without_sensitive_payload():
    events = []

    async def write(state):
        events.append(state)

    task = asyncio.create_task(
        advisor_messages._keep_turn_heartbeat(write, interval=0.01)
    )
    await asyncio.sleep(0.05)
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)

    assert events
    assert all(event == {"state": "TURN_HEARTBEAT"} for event in events)


@pytest.mark.asyncio
async def test_idempotent_user_message_is_committed_in_independent_transaction(monkeypatch):
    class Session:
        def __init__(self):
            self.added = []
            self.committed = False

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return False

        def add(self, value):
            self.added.append(value)

        async def commit(self):
            self.committed = True

    session = Session()
    monkeypatch.setattr(advisor_messages, "AsyncSessionFactory", lambda: session)
    session_id, owner_id = uuid4(), uuid4()

    await advisor_messages._persist_idempotent_user_message(
        session_id=session_id, owner_id=owner_id,
        idempotency_key="persist-before-agent-123", content="请帮我调整行程",
    )

    assert session.committed is True
    assert len(session.added) == 1
    assert session.added[0].session_id == session_id
    assert session.added[0].owner_id == owner_id
    assert session.added[0].role == "user"
    assert session.added[0].content == "请帮我调整行程"
    assert session.added[0].idempotency_key == "persist-before-agent-123"


@pytest.mark.asyncio
async def test_idempotent_user_message_persists_selection_snapshot(monkeypatch):
    class Session:
        def __init__(self):
            self.added = []

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return False

        def add(self, value):
            self.added.append(value)

        async def commit(self):
            pass

    session = Session()
    monkeypatch.setattr(advisor_messages, "AsyncSessionFactory", lambda: session)
    selected_ids = [str(uuid4()), str(uuid4())]

    await advisor_messages._persist_idempotent_user_message(
        session_id=uuid4(), owner_id=uuid4(),
        idempotency_key="persist-selection-123", content="帮我安排这些地点",
        selected_candidate_ids=selected_ids,
    )

    assert session.added[0].payload == {"selection_snapshot": selected_ids}


@pytest.mark.asyncio
async def test_message_endpoint_applies_and_persists_current_selection(monkeypatch):
    from travelpilot_api.advisor import turn_service

    user_id, session_id, candidate_id = uuid4(), uuid4(), uuid4()
    row = SimpleNamespace(
        id=session_id, status="ACTIVE",
        candidates=[{"id": str(candidate_id), "name": "熊猫基地"}],
        context={"revision": 4, "selected_candidate_ids": []},
    )
    persisted = []

    async def owned(*_args, **_kwargs):
        return row

    async def persist_user_message(**values):
        persisted.append(values)

    async def write(_state):
        pass

    async def execute(*_args, **_kwargs):
        assert row.context == {
            "revision": 5, "selected_candidate_ids": [str(candidate_id)],
            "selection_confirmation_candidate_ids": [str(candidate_id)],
        }
        return "completed"

    monkeypatch.setattr(advisor_messages, "owned_session", owned)
    monkeypatch.setattr(advisor_messages, "_persist_idempotent_user_message", persist_user_message)
    monkeypatch.setattr(advisor_messages, "checkpoint_writer", lambda **_kwargs: write)
    monkeypatch.setattr(turn_service, "execute_advisor_turn", execute)

    result = await advisor_messages.send_message(
        session_id=session_id,
        payload=SimpleNamespace(
            content="按轻松节奏安排", selected_candidate_ids=[candidate_id],
        ),
        db=_DB([]), user=SimpleNamespace(id=user_id),
        maps=object(), weather=object(), idempotency_key="selection-write-123",
    )

    assert result == "completed"
    assert persisted == [{
        "session_id": session_id,
        "owner_id": user_id,
        "idempotency_key": "selection-write-123",
        "content": "按轻松节奏安排",
        "selected_candidate_ids": [str(candidate_id)],
    }]


@pytest.mark.asyncio
async def test_retry_restores_persisted_selection_snapshot_after_context_rollback(monkeypatch):
    from travelpilot_api.advisor import turn_service

    user_id, session_id, candidate_id = uuid4(), uuid4(), uuid4()
    row = SimpleNamespace(
        id=session_id, status="ACTIVE",
        candidates=[{"id": str(candidate_id), "name": "熊猫基地"}],
        context={"revision": 4, "selected_candidate_ids": []},
    )
    previous = SimpleNamespace(
        id=uuid4(), created_at=datetime.now(UTC), content="按轻松节奏安排",
        payload={"selection_snapshot": [str(candidate_id)]},
    )
    calls = []

    async def owned(*_args, **_kwargs):
        return row

    async def write(_state):
        pass

    async def execute(*_args, **kwargs):
        calls.append(kwargs)
        assert row.context == {
            "revision": 5, "selected_candidate_ids": [str(candidate_id)],
            "selection_confirmation_candidate_ids": [str(candidate_id)],
        }
        return "resumed"

    monkeypatch.setattr(advisor_messages, "owned_session", owned)
    monkeypatch.setattr(advisor_messages, "checkpoint_writer", lambda **_kwargs: write)
    monkeypatch.setattr(turn_service, "execute_advisor_turn", execute)

    result = await advisor_messages.send_message(
        session_id=session_id, payload=SimpleNamespace(content="按轻松节奏安排"),
        db=_ScalarSequenceDB([previous, None, None]),
        user=SimpleNamespace(id=user_id), maps=object(), weather=object(),
        idempotency_key="selection-retry-123",
    )

    assert result == "resumed"
    assert calls[0]["resume_existing_user_message"] is True


@pytest.mark.asyncio
async def test_idempotent_retry_rejects_changed_selection_snapshot(monkeypatch):
    user_id, session_id, first_id, second_id = uuid4(), uuid4(), uuid4(), uuid4()
    row = SimpleNamespace(
        id=session_id, status="ACTIVE",
        candidates=[{"id": str(first_id)}, {"id": str(second_id)}],
        context={"revision": 2, "selected_candidate_ids": [str(first_id)]},
    )
    previous = SimpleNamespace(
        id=uuid4(), created_at=datetime.now(UTC), content="按轻松节奏安排",
        payload={"selection_snapshot": [str(first_id)]},
    )

    async def owned(*_args, **_kwargs):
        return row

    monkeypatch.setattr(advisor_messages, "owned_session", owned)

    with pytest.raises(HTTPException) as exc_info:
        await advisor_messages.send_message(
            session_id=session_id,
            payload=SimpleNamespace(
                content="按轻松节奏安排", selected_candidate_ids=[second_id],
            ),
            db=_ScalarSequenceDB([previous]),
            user=SimpleNamespace(id=user_id), maps=object(), weather=object(),
            idempotency_key="selection-retry-123",
        )

    assert exc_info.value.status_code == 409


@pytest.mark.asyncio
@pytest.mark.parametrize("fails", [False, True])
async def test_message_endpoint_records_turn_terminal_state(monkeypatch, fails):
    from travelpilot_api.advisor import turn_service

    user_id, session_id = uuid4(), uuid4()
    row = SimpleNamespace(id=session_id, status="ACTIVE", context={}, candidates=[])
    checkpoints = []
    persisted = []

    async def owned(_db, requested_session_id, requested_owner_id, **_kwargs):
        assert requested_session_id == session_id
        assert requested_owner_id == user_id
        return row

    async def write(state):
        checkpoints.append(state["state"])

    async def persist_user_message(**values):
        persisted.append(values)

    async def execute(*_args, **_kwargs):
        assert persisted == [{
            "session_id": session_id,
            "owner_id": user_id,
            "idempotency_key": "request-key-123",
            "content": "查路线",
        }]
        assert _kwargs["resume_existing_user_message"] is True
        if fails:
            raise RuntimeError("simulated failure")
        return "completed"

    monkeypatch.setattr(advisor_messages, "owned_session", owned)
    monkeypatch.setattr(advisor_messages, "checkpoint_writer", lambda **_kwargs: write)
    monkeypatch.setattr(advisor_messages, "_persist_idempotent_user_message", persist_user_message)
    monkeypatch.setattr(turn_service, "execute_advisor_turn", execute)
    arguments = {
        "session_id": session_id,
        "payload": SimpleNamespace(content="查路线"),
        "db": _DB([]),
        "user": SimpleNamespace(id=user_id),
        "maps": object(),
        "weather": object(),
        "idempotency_key": "request-key-123",
    }

    if fails:
        with pytest.raises(RuntimeError, match="simulated failure"):
            await advisor_messages.send_message(**arguments)
        assert checkpoints == ["TURN_STARTED", "TURN_FAILED"]
    else:
        result = await advisor_messages.send_message(**arguments)
        assert result == "completed"
        assert checkpoints == ["TURN_STARTED", "TURN_SUCCEEDED"]


@pytest.mark.asyncio
async def test_idempotent_retry_resumes_persisted_user_message_without_reply(monkeypatch):
    from travelpilot_api.advisor import turn_service

    user_id, session_id = uuid4(), uuid4()
    row = SimpleNamespace(id=session_id, status="ACTIVE", context={}, candidates=[])
    previous = SimpleNamespace(
        id=uuid4(), created_at=datetime.now(UTC), content="查路线",
    )
    checkpoints = []
    calls = []

    async def owned(*_args, **_kwargs):
        return row

    async def write(state):
        checkpoints.append(state["state"])

    async def execute(*_args, **kwargs):
        calls.append(kwargs)
        return "resumed"

    monkeypatch.setattr(advisor_messages, "owned_session", owned)
    monkeypatch.setattr(advisor_messages, "checkpoint_writer", lambda **_kwargs: write)
    monkeypatch.setattr(turn_service, "execute_advisor_turn", execute)

    result = await advisor_messages.send_message(
        session_id=session_id,
        payload=SimpleNamespace(content="查路线"),
        db=_ScalarSequenceDB([previous, None, None]),
        user=SimpleNamespace(id=user_id), maps=object(), weather=object(),
        idempotency_key="request-key-123",
    )

    assert result == "resumed"
    assert calls[0]["resume_existing_user_message"] is True
    assert checkpoints == ["TURN_STARTED", "TURN_SUCCEEDED"]


@pytest.mark.asyncio
async def test_idempotent_retry_with_reply_returns_without_rerunning(monkeypatch):
    from travelpilot_api.advisor import turn_service

    user_id, session_id = uuid4(), uuid4()
    row = SimpleNamespace(id=session_id, status="ACTIVE", context={}, candidates=[])
    previous = SimpleNamespace(
        id=uuid4(), created_at=datetime.now(UTC), content="查路线",
    )
    checkpoints = []
    sentinel = object()

    async def owned(*_args, **_kwargs):
        return row

    async def write(state):
        checkpoints.append(state["state"])

    async def execute(*_args, **_kwargs):
        raise AssertionError("completed request must not run twice")

    async def read_session(_db, _row):
        return sentinel

    monkeypatch.setattr(advisor_messages, "owned_session", owned)
    monkeypatch.setattr(advisor_messages, "checkpoint_writer", lambda **_kwargs: write)
    monkeypatch.setattr(advisor_messages, "read_session", read_session)
    monkeypatch.setattr(turn_service, "execute_advisor_turn", execute)

    result = await advisor_messages.send_message(
        session_id=session_id,
        payload=SimpleNamespace(content="查路线"),
        db=_ScalarSequenceDB([previous, uuid4()]),
        user=SimpleNamespace(id=user_id), maps=object(), weather=object(),
        idempotency_key="request-key-123",
    )

    assert result is sentinel
    assert checkpoints == ["TURN_SUCCEEDED"]


@pytest.mark.asyncio
async def test_idempotent_retry_does_not_overtake_a_later_user_message(monkeypatch):
    user_id, session_id = uuid4(), uuid4()
    row = SimpleNamespace(id=session_id, status="ACTIVE", context={}, candidates=[])
    previous = SimpleNamespace(
        id=uuid4(), created_at=datetime.now(UTC), content="查路线",
    )

    async def owned(*_args, **_kwargs):
        return row

    monkeypatch.setattr(advisor_messages, "owned_session", owned)

    with pytest.raises(HTTPException) as exc_info:
        await advisor_messages.send_message(
            session_id=session_id,
            payload=SimpleNamespace(content="查路线"),
            db=_ScalarSequenceDB([previous, None, SimpleNamespace(role="user")]),
            user=SimpleNamespace(id=user_id), maps=object(), weather=object(),
            idempotency_key="request-key-123",
        )

    assert exc_info.value.status_code == 409


@pytest.mark.asyncio
async def test_idempotent_retry_rejects_a_reply_linked_to_another_turn(monkeypatch):
    user_id, session_id = uuid4(), uuid4()
    row = SimpleNamespace(id=session_id, status="ACTIVE", context={}, candidates=[])
    previous = SimpleNamespace(id=uuid4(), created_at=datetime.now(UTC), content="查路线")

    async def owned(*_args, **_kwargs):
        return row

    monkeypatch.setattr(advisor_messages, "owned_session", owned)
    with pytest.raises(HTTPException) as exc_info:
        await advisor_messages.send_message(
            session_id=session_id,
            payload=SimpleNamespace(content="查路线"),
            db=_ScalarSequenceDB([
                previous, None,
                SimpleNamespace(role="assistant", payload={
                    "response_to_idempotency_key": "different-key-123",
                }),
            ]),
            user=SimpleNamespace(id=user_id), maps=object(), weather=object(),
            idempotency_key="request-key-123",
        )

    assert exc_info.value.status_code == 409


@pytest.mark.asyncio
async def test_budget_resume_reuses_user_message_and_links_reply_key(monkeypatch):
    from travelpilot_api.advisor import turn_service

    added = []

    class DB:
        def add(self, value):
            added.append(value)

        async def commit(self):
            pass

        async def refresh(self, _row):
            pass

    row = SimpleNamespace(id=uuid4(), context={}, updated_at=None)
    user = SimpleNamespace(id=uuid4())
    sentinel = object()

    async def read_session(_db, _row):
        return sentinel

    monkeypatch.setattr(turn_service, "read_session", read_session)
    result = await turn_service._save_budget_control_message(
        DB(), row, user, {}, "继续", "额度已用完", {"response_source": "TEMPLATE"},
        idempotency_key="request-key-123", resume_existing_user_message=True,
    )

    assert result is sentinel
    assert len(added) == 1
    assert added[0].role == "assistant"
    assert added[0].payload["response_to_idempotency_key"] == "request-key-123"


@pytest.mark.asyncio
async def test_progress_requires_a_valid_task_key(monkeypatch):
    async def owned(*_args):
        return None

    monkeypatch.setattr(advisor_messages, "owned_session", owned)
    with pytest.raises(HTTPException) as exc_info:
        await advisor_messages.get_message_progress(
            session_id=uuid4(), db=object(), user=SimpleNamespace(id=uuid4()),
            idempotency_key=None,
        )

    assert exc_info.value.status_code == 422
