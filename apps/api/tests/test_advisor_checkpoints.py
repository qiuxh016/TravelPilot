from uuid import uuid4

import pytest
from travelpilot_api.advisor import checkpoints


class _FakeSession:
    def __init__(self):
        self.rows = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        return None

    def add(self, row):
        self.rows.append(row)

    async def commit(self):
        return None


@pytest.mark.asyncio
async def test_checkpoint_persists_only_bounded_non_sensitive_state(monkeypatch):
    session = _FakeSession()
    monkeypatch.setattr(checkpoints, "AsyncSessionFactory", lambda: session)
    owner_id, session_id = uuid4(), uuid4()
    write = checkpoints.checkpoint_writer(
        owner_id=owner_id, session_id=session_id,
        task_id="same-client-retry-key", phase="route_query",
        context_revision=7,
    )
    second_write = checkpoints.checkpoint_writer(
        owner_id=owner_id, session_id=session_id,
        task_id="same-client-retry-key", phase="route_query",
        context_revision=7,
    )

    await write({
        "state": "VERIFIED", "transition": 2, "tool": "get_driving_route",
        "observation_keys": ["get_driving_route"],
        "question": "private question", "summary": "private summary",
        "place_name": "private location",
    })

    assert len(session.rows) == 1
    row = session.rows[0]
    assert row.owner_id == owner_id
    assert row.session_id == session_id
    assert row.status == "VERIFIED"
    assert row.metadata_json["context_revision"] == 7
    assert row.metadata_json["observation_keys"] == ["get_driving_route"]
    assert row.metadata_json["task_ref"] != "same-client-retry-key"
    first_attempt = row.metadata_json["attempt_ref"]
    assert len(first_attempt) == 24
    await second_write({"state": "OBSERVING", "transition": 0})
    assert session.rows[1].metadata_json["attempt_ref"] != first_attempt
    assert not {"question", "summary", "place_name"} & row.metadata_json.keys()


@pytest.mark.asyncio
async def test_checkpoint_keeps_only_allowlisted_stage_selection_fields(monkeypatch):
    session = _FakeSession()
    monkeypatch.setattr(checkpoints, "AsyncSessionFactory", lambda: session)
    write = checkpoints.checkpoint_writer(
        owner_id=uuid4(), session_id=uuid4(), task_id="retry-key",
        phase="turn_stage", context_revision=2,
    )

    await write({
        "state": "STAGE_SELECTED",
        "transition": 1,
        "tool": "ROUTE",
        "eligible_stages": ["SEARCH_WEATHER", "ROUTE"],
        "selected_order": ["ROUTE", "SEARCH_WEATHER"],
        "decision_source": "MODEL",
        "decision_summary": "private model rationale",
        "user_goal": "private trip request",
    })
    await write({
        "state": "STAGE_SELECTED",
        "transition": 2,
        "tool": "ROUTE",
        "eligible_stages": ["ROUTE"],
        "selected_order": ["SCHEDULE"],
        "decision_source": "MODEL",
    })

    assert session.rows[0].metadata_json["eligible_stages"] == ["SEARCH_WEATHER", "ROUTE"]
    assert session.rows[0].metadata_json["selected_order"] == ["ROUTE", "SEARCH_WEATHER"]
    assert session.rows[0].metadata_json["decision_source"] == "MODEL"
    assert not {"decision_summary", "user_goal"} & session.rows[0].metadata_json.keys()
    assert not {
        "eligible_stages", "selected_order", "decision_source",
    } & session.rows[1].metadata_json.keys()
