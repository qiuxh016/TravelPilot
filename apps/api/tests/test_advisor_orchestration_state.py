from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import uuid4

import pytest
from travelpilot_api.advisor import orchestration_state


class _Rows:
    def __init__(self, rows):
        self._rows = rows

    def all(self):
        return self._rows


class _ReadSession:
    def __init__(self, rows):
        self._rows = rows
        self.statement = None

    async def scalars(self, statement):
        self.statement = statement
        return _Rows(self._rows)


class _WriteSession:
    def __init__(self):
        self.rows = []
        self.committed = False

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        return None

    def add(self, row):
        if getattr(row, "created_at", None) is None:
            row.created_at = datetime.now(UTC)
        self.rows.append(row)

    async def execute(self, statement):
        self.delete_statement = statement

    async def commit(self):
        self.committed = True


@pytest.mark.asyncio
async def test_stage_order_checkpoint_persists_only_bounded_safe_decision(monkeypatch):
    session = _WriteSession()
    monkeypatch.setattr(orchestration_state, "AsyncSessionFactory", lambda: session)
    owner_id, session_id = uuid4(), uuid4()
    observation_ref = orchestration_state.stage_observation_ref({"search_failures": 0})
    write = orchestration_state.stage_order_checkpoint_writer(
        owner_id=owner_id, session_id=session_id,
        task_id="idempotency-secret-text", context_revision=5,
        allowed_stages=["SCHEDULE", "ROUTE"],
        observation_ref=observation_ref,
    )

    await write({
        "order": ["ROUTE", "SCHEDULE"],
        "decision_source": "MODEL",
        "decision_summary": "private user goal must not be stored",
        "user_goal": "private user goal must not be stored",
        "place_names": ["private place"],
    })

    assert session.committed is True
    row = session.rows[0]
    assert row.owner_id == owner_id and row.session_id == session_id
    assert row.phase == "turn_orchestration"
    assert row.operation == "stage_order_selected"
    assert row.metadata_json["order"] == ["ROUTE", "SCHEDULE"]
    assert row.metadata_json["context_revision"] == 5
    assert row.metadata_json["observation_ref"] == observation_ref
    assert row.metadata_json["task_ref"] != "idempotency-secret-text"
    assert "user_goal" not in row.metadata_json
    assert "place_names" not in row.metadata_json
    recovered = await orchestration_state.load_stage_order_checkpoint(
        _ReadSession(session.rows), owner_id=owner_id, session_id=session_id,
        task_id="idempotency-secret-text", context_revision=5,
        allowed_stages=["SCHEDULE", "ROUTE"],
        observation_ref=observation_ref,
    )
    assert recovered is not None
    assert recovered["order"] == ["ROUTE", "SCHEDULE"]
    assert recovered["decision_source"] == "RECOVERED"


@pytest.mark.asyncio
async def test_readonly_action_checkpoint_stores_only_hashes_and_recovers_in_matching_scope(monkeypatch):
    session = _WriteSession()
    monkeypatch.setattr(orchestration_state, "AsyncSessionFactory", lambda: session)
    owner_id, session_id = uuid4(), uuid4()
    task_id = "private-idempotency-key"
    args = {"keyword": "梅州博物馆", "city": "梅州"}
    action_ref = orchestration_state.readonly_action_ref("search_places", args)
    observation_ref = orchestration_state.stage_observation_ref({"evidence": []})
    approved = {action_ref: "search_places"}
    writer = orchestration_state.readonly_tool_decision_writer(
        owner_id=owner_id, session_id=session_id, task_id=task_id,
        context_revision=9, observation_ref=observation_ref,
        decision_number=1, approved_actions=approved,
    )
    await writer({"tool": "search_places", "action_ref": action_ref,
                  "arguments": args, "user_goal": "private request"})

    row = session.rows[0]
    assert row.operation == "readonly_tool_decision"
    assert row.owner_id == owner_id and row.session_id == session_id
    assert row.metadata_json["task_ref"] != task_id
    assert row.metadata_json["action_ref"] == action_ref
    assert "梅州" not in str(row.metadata_json)
    assert "private request" not in str(row.metadata_json)
    assert "arguments" not in row.metadata_json

    recovered = await orchestration_state.load_readonly_tool_decision(
        _ReadSession([row]), owner_id=owner_id, session_id=session_id,
        task_id=task_id, context_revision=9,
        observation_ref=observation_ref, decision_number=1,
        approved_actions=approved,
    )
    assert recovered == {"tool": "search_places", "action_ref": action_ref}
    assert await orchestration_state.load_readonly_tool_decision(
        _ReadSession([row]), owner_id=owner_id, session_id=session_id,
        task_id=task_id, context_revision=9,
        observation_ref="0" * 64, decision_number=1,
        approved_actions=approved,
    ) is None
    assert await orchestration_state.load_readonly_tool_decision(
        _ReadSession([row]), owner_id=owner_id, session_id=session_id,
        task_id=task_id, context_revision=9,
        observation_ref=observation_ref, decision_number=1,
        approved_actions={action_ref: "get_trip_weather"},
    ) is None
    assert await orchestration_state.load_readonly_tool_decision(
        _ReadSession([row]), owner_id=uuid4(), session_id=session_id,
        task_id=task_id, context_revision=9,
        observation_ref=observation_ref, decision_number=1,
        approved_actions=approved,
    ) is None


@pytest.mark.asyncio
async def test_checkpoint_accepts_only_approved_non_persistent_schedule_draft(monkeypatch):
    session = _WriteSession()
    monkeypatch.setattr(orchestration_state, "AsyncSessionFactory", lambda: session)
    owner_id, session_id = uuid4(), uuid4()
    task_id = "schedule-idempotency-key"
    action_ref = orchestration_state.readonly_action_ref("build_schedule_draft", {})
    observation_ref = orchestration_state.stage_observation_ref({"selected_count": 2})
    approved = {action_ref: "build_schedule_draft"}
    writer = orchestration_state.readonly_tool_decision_writer(
        owner_id=owner_id, session_id=session_id, task_id=task_id,
        context_revision=3, observation_ref=observation_ref,
        decision_number=1, approved_actions=approved,
    )
    await writer({"tool": "build_schedule_draft", "action_ref": action_ref})

    assert len(session.rows) == 1
    assert session.rows[0].metadata_json["action_ref"] == action_ref
    recovered = await orchestration_state.load_readonly_tool_decision(
        _ReadSession(session.rows), owner_id=owner_id, session_id=session_id,
        task_id=task_id, context_revision=3, observation_ref=observation_ref,
        decision_number=1, approved_actions=approved,
    )
    assert recovered == {"tool": "build_schedule_draft", "action_ref": action_ref}
    assert await orchestration_state.load_readonly_tool_decision(
        _ReadSession(session.rows), owner_id=owner_id, session_id=session_id,
        task_id=task_id, context_revision=3, observation_ref=observation_ref,
        decision_number=1, approved_actions={action_ref: "search_places"},
    ) is None


@pytest.mark.asyncio
async def test_writer_rejects_decision_that_omits_an_authorized_stage(monkeypatch):
    session = _WriteSession()
    monkeypatch.setattr(orchestration_state, "AsyncSessionFactory", lambda: session)
    write = orchestration_state.stage_order_checkpoint_writer(
        owner_id=uuid4(), session_id=uuid4(), task_id="task", context_revision=1,
        allowed_stages=["SCHEDULE", "ROUTE"],
    )

    await write({"order": ["ROUTE"], "decision_source": "MODEL"})

    assert session.rows == []
    assert session.committed is False


@pytest.mark.asyncio
async def test_load_stage_order_requires_same_hashed_task_and_context_revision():
    task_id = "retryable-task-id"
    task_ref = orchestration_state._task_ref(task_id)
    rows = [
        SimpleNamespace(created_at=datetime.now(UTC), metadata_json={
            "task_ref": task_ref, "context_revision": 8,
            "observation_ref": "",
            "order": ["ROUTE", "SCHEDULE"], "decision_source": "MODEL",
        }),
        SimpleNamespace(created_at=datetime.now(UTC), metadata_json={
            "task_ref": task_ref, "context_revision": 7,
            "observation_ref": "",
            "order": ["SCHEDULE", "ROUTE"], "decision_source": "MODEL",
        }),
    ]
    db = _ReadSession(rows)

    result = await orchestration_state.load_stage_order_checkpoint(
        db, owner_id=uuid4(), session_id=uuid4(), task_id=task_id,
        context_revision=8, allowed_stages=["SCHEDULE", "ROUTE"],
    )

    assert result == {
        "order": ["ROUTE", "SCHEDULE"],
        "decision_source": "RECOVERED",
        "original_decision_source": "MODEL",
        "decision_summary": "恢复本任务已记录的执行顺序，继续安全重试",
        "fallback_reason": None,
    }
    assert "owner_id" in str(db.statement)
    assert "session_id" in str(db.statement)


@pytest.mark.asyncio
async def test_load_ignores_invalid_or_incomplete_stage_order():
    task_id = "retryable-task-id"
    rows = [
        SimpleNamespace(metadata_json={
            "task_ref": orchestration_state._task_ref(task_id),
            "context_revision": 3,
            "order": ["ROUTE"],
        }),
    ]

    result = await orchestration_state.load_stage_order_checkpoint(
        _ReadSession(rows), owner_id=uuid4(), session_id=uuid4(), task_id=task_id,
        context_revision=3, allowed_stages=["SCHEDULE", "ROUTE"],
    )

    assert result is None


@pytest.mark.parametrize(
    "created_at",
    [
        datetime.now(UTC) - timedelta(seconds=orchestration_state._STAGE_ORDER_TTL_SECONDS + 1),
        datetime.now(UTC).replace(tzinfo=None),
        datetime.now(UTC) + timedelta(seconds=30),
    ],
    ids=["expired", "naive", "future"],
)
@pytest.mark.asyncio
async def test_load_ignores_invalid_timestamp_stage_order_checkpoint(created_at):
    task_id = "retryable-task-id"
    stale = SimpleNamespace(
        created_at=created_at,
        metadata_json={
            "task_ref": orchestration_state._task_ref(task_id),
            "context_revision": 3,
            "observation_ref": "",
            "order": ["ROUTE", "SCHEDULE"],
            "decision_source": "MODEL",
        },
    )

    result = await orchestration_state.load_stage_order_checkpoint(
        _ReadSession([stale]), owner_id=uuid4(), session_id=uuid4(), task_id=task_id,
        context_revision=3, allowed_stages=["SCHEDULE", "ROUTE"],
    )

    assert result is None


@pytest.mark.asyncio
async def test_retry_after_interruption_reuses_persisted_stage_order(monkeypatch):
    owner_id, session_id = uuid4(), uuid4()
    task_id = "same-client-idempotency-key"
    evidence_ref = orchestration_state.stage_observation_ref({"search_failures": 0})
    durable = _WriteSession()
    monkeypatch.setattr(orchestration_state, "AsyncSessionFactory", lambda: durable)
    persist = orchestration_state.stage_order_checkpoint_writer(
        owner_id=owner_id,
        session_id=session_id,
        task_id=task_id,
        context_revision=12,
        allowed_stages=["SCHEDULE", "ROUTE"],
        observation_ref=evidence_ref,
    )
    decisions = 0

    async def choose_first_attempt():
        nonlocal decisions
        decisions += 1
        return {
            "order": ["ROUTE", "SCHEDULE"],
            "decision_source": "MODEL",
            "decision_summary": "not persisted",
        }

    first_decision = await orchestration_state.resolve_stage_order(
        _ReadSession([]),
        owner_id=owner_id,
        session_id=session_id,
        task_id=task_id,
        context_revision=12,
        allowed_stages=["SCHEDULE", "ROUTE"],
        observation_ref=evidence_ref,
        retryable=True,
        choose=choose_first_attempt,
        persist=persist,
    )
    assert first_decision["order"] == ["ROUTE", "SCHEDULE"]
    assert durable.committed

    # Simulate the parent request/process failing after the independent commit.
    async def should_not_choose_again():
        nonlocal decisions
        decisions += 1
        raise AssertionError("retry should reuse the persisted decision")

    recovered = await orchestration_state.resolve_stage_order(
        _ReadSession(durable.rows),
        owner_id=owner_id,
        session_id=session_id,
        task_id=task_id,
        context_revision=12,
        allowed_stages=["SCHEDULE", "ROUTE"],
        observation_ref=evidence_ref,
        retryable=True,
        choose=should_not_choose_again,
        persist=persist,
    )

    assert recovered["order"] == ["ROUTE", "SCHEDULE"]
    assert recovered["decision_source"] == "RECOVERED"
    assert decisions == 1


@pytest.mark.asyncio
async def test_changed_stage_observation_invalidates_checkpoint_and_reselects(monkeypatch):
    owner_id, session_id = uuid4(), uuid4()
    task_id = "same-task-new-provider-result"
    previous_ref = orchestration_state.stage_observation_ref({"weather_available": False})
    updated_ref = orchestration_state.stage_observation_ref({"weather_available": True})
    durable = _WriteSession()
    monkeypatch.setattr(orchestration_state, "AsyncSessionFactory", lambda: durable)
    old_persist = orchestration_state.stage_order_checkpoint_writer(
        owner_id=owner_id, session_id=session_id, task_id=task_id,
        context_revision=2, allowed_stages=["SCHEDULE", "ROUTE"],
        observation_ref=previous_ref,
    )
    await old_persist({"order": ["ROUTE", "SCHEDULE"], "decision_source": "MODEL"})

    async def choose_from_updated_evidence():
        return {"order": ["SCHEDULE", "ROUTE"], "decision_source": "MODEL"}

    updated_persist = orchestration_state.stage_order_checkpoint_writer(
        owner_id=owner_id, session_id=session_id, task_id=task_id,
        context_revision=2, allowed_stages=["SCHEDULE", "ROUTE"],
        observation_ref=updated_ref,
    )
    decision = await orchestration_state.resolve_stage_order(
        _ReadSession(durable.rows),
        owner_id=owner_id,
        session_id=session_id,
        task_id=task_id,
        context_revision=2,
        allowed_stages=["SCHEDULE", "ROUTE"],
        observation_ref=updated_ref,
        retryable=True,
        choose=choose_from_updated_evidence,
        persist=updated_persist,
    )

    assert decision["order"] == ["SCHEDULE", "ROUTE"]
    assert decision["decision_source"] == "MODEL"
    assert len(durable.rows) == 2


@pytest.mark.asyncio
async def test_non_idempotent_turn_does_not_read_or_write_recovery_state():
    async def choose():
        return {"order": ["SCHEDULE", "ROUTE"], "decision_source": "FALLBACK"}

    async def forbidden_persist(_decision):
        raise AssertionError("non-idempotent turns must not persist checkpoints")

    result = await orchestration_state.resolve_stage_order(
        _ReadSession([]),
        owner_id=uuid4(),
        session_id=uuid4(),
        task_id="non-idempotent-task",
        context_revision=1,
        allowed_stages=["SCHEDULE", "ROUTE"],
        retryable=False,
        choose=choose,
        persist=forbidden_persist,
    )

    assert result["order"] == ["SCHEDULE", "ROUTE"]


@pytest.mark.asyncio
async def test_route_matrix_checkpoint_is_owner_scoped_and_recovered_only_for_same_context(monkeypatch):
    owner_id, session_id = uuid4(), uuid4()
    task_id = "route-retry-task-key"
    durable = _WriteSession()
    monkeypatch.setattr(orchestration_state, "AsyncSessionFactory", lambda: durable)
    write = orchestration_state.route_evidence_checkpoint_writer(
        owner_id=owner_id, session_id=session_id, task_id=task_id,
    )
    snapshot = {
        "candidate_ids": ["candidate-a", "candidate-b"],
        "context_revision": 6,
        "durations_min": [[0, 18], [21, 0]],
        "status": "VERIFIED",
        "source": "AMap Web Service",
        "retrieved_at": datetime.now(UTC).isoformat(),
        "place_names": ["must never be stored"],
        "coordinates": [[30.1, 104.1], [30.2, 104.2]],
    }
    await write(snapshot)

    assert durable.committed is True
    row = durable.rows[0]
    assert row.owner_id == owner_id and row.session_id == session_id
    assert row.operation == "route_evidence_snapshot"
    assert row.status == "RECOVERABLE"
    assert row.metadata_json["task_ref"] == orchestration_state._task_ref(task_id)
    assert row.metadata_json["candidate_ids"] == snapshot["candidate_ids"]
    assert row.metadata_json["durations_min"] == snapshot["durations_min"]
    assert "place_names" not in row.metadata_json
    assert "coordinates" not in row.metadata_json

    recovered = await orchestration_state.load_route_evidence_checkpoint(
        _ReadSession([row]),
        owner_id=owner_id, session_id=session_id, task_id=task_id,
        candidate_ids=snapshot["candidate_ids"], context_revision=6,
        expected_source="AMap Web Service",
    )
    assert recovered is not None
    assert recovered["durations_min"] == snapshot["durations_min"]
    assert await orchestration_state.load_route_evidence_checkpoint(
        _ReadSession([row]),
        owner_id=owner_id, session_id=session_id, task_id=task_id,
        candidate_ids=snapshot["candidate_ids"], context_revision=7,
        expected_source="AMap Web Service",
    ) is None
    wrong_owner_db = _ReadSession([])
    assert await orchestration_state.load_route_evidence_checkpoint(
        wrong_owner_db,
        owner_id=uuid4(), session_id=session_id, task_id=task_id,
        candidate_ids=snapshot["candidate_ids"], context_revision=6,
        expected_source="AMap Web Service",
    ) is None
    assert "owner_id" in str(wrong_owner_db.statement)


@pytest.mark.asyncio
async def test_route_matrix_checkpoint_writer_rejects_invalid_matrix_and_status(monkeypatch):
    durable = _WriteSession()
    monkeypatch.setattr(orchestration_state, "AsyncSessionFactory", lambda: durable)
    write = orchestration_state.route_evidence_checkpoint_writer(
        owner_id=uuid4(), session_id=uuid4(), task_id="route-task",
    )
    base = {
        "candidate_ids": ["a", "b"], "context_revision": 1,
        "durations_min": [[0, 10], [12, 0]], "status": "VERIFIED",
        "source": "AMap Web Service", "retrieved_at": datetime.now(UTC).isoformat(),
    }
    await write({**base, "durations_min": [[5, 10], [12, 0]]})
    await write({**base, "status": "UNAVAILABLE"})

    assert durable.rows == []
    assert durable.committed is False


@pytest.mark.asyncio
async def test_weather_checkpoint_is_compact_scoped_and_recovered_only_when_fresh(monkeypatch):
    owner_id, session_id = uuid4(), uuid4()
    durable = _WriteSession()
    monkeypatch.setattr(orchestration_state, "AsyncSessionFactory", lambda: durable)
    city = "成都私密目的地"
    scope = orchestration_state.weather_scope_ref(
        city, "2026-10-20", "2026-10-21", 4, "Open-Meteo",
    )
    writer = orchestration_state.weather_evidence_checkpoint_writer(
        owner_id=owner_id, session_id=session_id, task_id="weather-retry",
        scope_ref=scope, expected_source="Open-Meteo",
    )
    weather = {
        "status": "AVAILABLE", "source": "Open-Meteo",
        "fetched_at": datetime.now(UTC).isoformat(), "city": city,
        "timezone": "Asia/Shanghai", "attribution": "Weather data by Open-Meteo",
        "message": None,
        "days": [{"date": "2026-10-20", "description": "小雨", "weather_code": 61,
                  "temperature_max_c": 20.5, "temperature_min_c": 13,
                  "precipitation_probability_max": 75}],
        "coordinates": [30.6, 104.0], "user_prompt": "secret prompt",
    }
    await writer(weather)
    row = durable.rows[0]
    assert row.status == "RECOVERABLE"
    assert "city" not in row.metadata_json["weather"]
    assert "coordinates" not in str(row.metadata_json)
    assert "user_prompt" not in str(row.metadata_json)
    assert row.metadata_json["scope_ref"] == scope
    loaded = await orchestration_state.load_weather_evidence_checkpoint(
        _ReadSession([row]), owner_id=owner_id, session_id=session_id,
        task_id="weather-retry", scope_ref=scope, city=city,
        expected_source="Open-Meteo",
    )
    assert loaded["city"] == city
    assert loaded["days"] == weather["days"]
    changed_scope = orchestration_state.weather_scope_ref(
        city, "2026-10-21", "2026-10-22", 4, "Open-Meteo",
    )
    assert changed_scope != scope
    assert await orchestration_state.load_weather_evidence_checkpoint(
        _ReadSession([row]), owner_id=owner_id, session_id=session_id,
        task_id="weather-retry", scope_ref=changed_scope, city=city,
        expected_source="Open-Meteo",
    ) is None
    old = dict(row.metadata_json)
    old["weather"] = {**old["weather"], "fetched_at": "2020-01-01T00:00:00+00:00"}
    stale_row = SimpleNamespace(**{**row.__dict__, "metadata_json": old})
    assert await orchestration_state.load_weather_evidence_checkpoint(
        _ReadSession([stale_row]), owner_id=owner_id, session_id=session_id,
        task_id="weather-retry", scope_ref=scope, city=city,
        expected_source="Open-Meteo",
    ) is None


@pytest.mark.asyncio
async def test_weather_checkpoint_writer_rejects_unverified_or_malformed_evidence(monkeypatch):
    durable = _WriteSession()
    monkeypatch.setattr(orchestration_state, "AsyncSessionFactory", lambda: durable)
    writer = orchestration_state.weather_evidence_checkpoint_writer(
        owner_id=uuid4(), session_id=uuid4(), task_id="weather-retry",
        scope_ref="scope", expected_source="Open-Meteo",
    )
    base = {
        "status": "AVAILABLE", "source": "Open-Meteo",
        "fetched_at": datetime.now(UTC).isoformat(), "days": [],
    }
    await writer({**base, "status": "FAILED"})
    await writer({**base, "status": "AVAILABLE", "days": []})
    await writer({**base, "days": [{"date": "2026-10-20", "description": "雨",
                                    "weather_code": 61,
                                    "precipitation_probability_max": 140}]})
    await writer({**base, "days": [{"date": "x", "description": "bad", "unexpected": 1}]})
    await writer({**base, "days": [{"date": "x", "description": "bad", "weather_code": float("nan")}]})
    assert durable.rows == []
    assert durable.committed is False


@pytest.mark.asyncio
async def test_map_search_checkpoint_is_query_scoped_and_restores_only_fresh_verified_places(monkeypatch):
    owner_id, session_id = uuid4(), uuid4()
    durable = _WriteSession()
    monkeypatch.setattr(orchestration_state, "AsyncSessionFactory", lambda: durable)
    city, keyword, source = "成都", "熊猫基地", "Local simulated maps"
    scope = orchestration_state.search_scope_ref(city, keyword, 3, source)
    assert orchestration_state.search_scope_ref(city, keyword, 4, source) == scope
    writer = orchestration_state.search_evidence_checkpoint_writer(
        owner_id=owner_id, session_id=session_id, task_id="map-retry",
        scope_ref=scope, expected_source=source,
    )
    result = {
        "status": "SIMULATED", "retrieved_at": datetime.now(UTC).isoformat(),
        "places": [{"provider": "fake", "provider_id": "panda-1", "name": "熊猫基地",
                    "address": "成都", "category": "景区", "latitude": 30.7,
                    "longitude": 104.1, "city_code": "028", "duration_min": 120,
                    "opening_hours": {"open": "09:00", "close": "17:00"},
                    "popularity_score": 4.7}],
        "keyword": keyword, "city": city, "user_goal": "private user query",
    }
    await writer(result)
    row = durable.rows[0]
    assert row.status == "RECOVERABLE"
    assert "keyword" not in row.metadata_json and "city" not in row.metadata_json
    assert "user_goal" not in str(row.metadata_json)
    assert "熊猫基地" in str(row.metadata_json["search"]["places"])

    loaded = await orchestration_state.load_search_evidence_checkpoint(
        _ReadSession([row]), owner_id=owner_id, session_id=session_id,
        task_id="map-retry", scope_ref=scope, expected_source=source,
    )
    assert loaded["places"][0]["provider_id"] == "panda-1"
    assert loaded["places"][0]["opening_hours"] == {"open": "09:00", "close": "17:00"}
    changed_scope = orchestration_state.search_scope_ref(city, "美食", 3, source)
    assert orchestration_state.search_scope_ref(city, keyword, 3, "Different map provider") != scope
    assert await orchestration_state.load_search_evidence_checkpoint(
        _ReadSession([row]), owner_id=owner_id, session_id=session_id,
        task_id="map-retry", scope_ref=changed_scope, expected_source=source,
    ) is None
    stale = dict(row.metadata_json)
    stale["search"] = {**stale["search"], "retrieved_at": "2020-01-01T00:00:00+00:00"}
    stale_row = SimpleNamespace(**{**row.__dict__, "metadata_json": stale})
    assert await orchestration_state.load_search_evidence_checkpoint(
        _ReadSession([stale_row]), owner_id=owner_id, session_id=session_id,
        task_id="map-retry", scope_ref=scope, expected_source=source,
    ) is None


@pytest.mark.asyncio
async def test_map_search_checkpoint_rejects_malformed_and_wrong_provider_data(monkeypatch):
    durable = _WriteSession()
    monkeypatch.setattr(orchestration_state, "AsyncSessionFactory", lambda: durable)
    writer = orchestration_state.search_evidence_checkpoint_writer(
        owner_id=uuid4(), session_id=uuid4(), task_id="map-retry",
        scope_ref="scope", expected_source="Local simulated maps",
    )
    base = {
        "status": "SIMULATED", "retrieved_at": datetime.now(UTC).isoformat(),
        "places": [{"provider": "fake", "provider_id": "p", "name": "x",
                    "latitude": 30.0, "longitude": 104.0}],
    }
    await writer({**base, "places": [{**base["places"][0], "latitude": float("nan")}]})
    await writer({**base, "places": [{**base["places"][0], "private_note": "no"}]})
    await writer({**base, "places": [{**base["places"][0], "provider": "amap"}]})
    await writer({**base, "places": [{
        **base["places"][0], "opening_hours": {"open": ["nested", "unsafe"]},
    }]})
    assert durable.rows == []


@pytest.mark.asyncio
async def test_direct_route_checkpoint_restores_valid_geometry_only_in_matching_scope(monkeypatch):
    owner_id, session_id = uuid4(), uuid4()
    durable = _WriteSession()
    monkeypatch.setattr(orchestration_state, "AsyncSessionFactory", lambda: durable)
    goal = "从宽窄巷子到熊猫基地查驾车路线"
    scope = orchestration_state.direct_route_scope_ref(
        goal, ["place-a", "place-b"], 7, "Local simulated maps",
    )
    writer = orchestration_state.direct_route_checkpoint_writer(
        owner_id=owner_id, session_id=session_id, task_id="route-key",
        scope_ref=scope, expected_source="Local simulated maps",
    )
    route = {
        "status": "SIMULATED", "origin_place_id": "place-a",
        "origin_name": "must not persist", "destination_place_id": "place-b",
        "destination_name": "must not persist", "points": [[30.6, 104.0], [30.7, 104.1]],
        "distance_m": 12000, "duration_min": 35, "mode": "DRIVING",
        "source": "Local simulated maps", "retrieved_at": datetime.now(UTC).isoformat(),
        "polyline": [[30.6, 104.0], [30.65, 104.05], [30.7, 104.1]],
        "segments": [{"mode": "DRIVING", "label": "模拟路线"}],
    }
    await writer(route)
    row = durable.rows[0]
    assert row.status == "RECOVERABLE"
    assert "origin_name" not in str(row.metadata_json)
    assert "destination_name" not in str(row.metadata_json)
    recovered = await orchestration_state.load_direct_route_checkpoint(
        _ReadSession([row]), owner_id=owner_id, session_id=session_id,
        task_id="route-key", scope_ref=scope, expected_source="Local simulated maps",
    )
    assert recovered["polyline"] == route["polyline"]
    changed_scope = orchestration_state.direct_route_scope_ref(
        goal, ["place-a", "place-c"], 7, "Local simulated maps",
    )
    assert await orchestration_state.load_direct_route_checkpoint(
        _ReadSession([row]), owner_id=owner_id, session_id=session_id,
        task_id="route-key", scope_ref=changed_scope,
        expected_source="Local simulated maps",
    ) is None
    stale = dict(row.metadata_json)
    stale["route"] = {**stale["route"], "retrieved_at": "2020-01-01T00:00:00+00:00"}
    stale_row = SimpleNamespace(**{**row.__dict__, "metadata_json": stale})
    assert await orchestration_state.load_direct_route_checkpoint(
        _ReadSession([stale_row]), owner_id=owner_id, session_id=session_id,
        task_id="route-key", scope_ref=scope,
        expected_source="Local simulated maps",
    ) is None
    await writer({**route, "polyline": [[130, 200]]})
    await writer({**route, "source": "wrong source"})
    assert len(durable.rows) == 1
