from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import uuid4

import pytest
from travelpilot_api.advisor import orchestration_state


class Database:
    def __init__(self, rows):
        self.rows = rows

    async def scalars(self, _statement):
        return SimpleNamespace(all=lambda: self.rows)


def _event(*, created_at, attempt_ref, state, transition, stage, task_ref, revision=4):
    return SimpleNamespace(
        created_at=created_at,
        metadata_json={
            "attempt_ref": attempt_ref,
            "task_ref": task_ref,
            "context_revision": revision,
            "state": state,
            "transition": transition,
            "tool": stage,
        },
    )


@pytest.mark.asyncio
async def test_load_turn_stage_checkpoint_finds_open_stage_and_next_sequence():
    now = datetime.now(UTC)
    owner_id, session_id = uuid4(), uuid4()
    task_id = "same-message-idempotency-key"
    task_ref = orchestration_state._task_ref(task_id)
    latest_attempt = "attempt-new"
    rows = [
        _event(
            created_at=now - timedelta(seconds=1), attempt_ref=latest_attempt,
            state="STAGE_STARTED", transition=2, stage="ROUTE", task_ref=task_ref,
        ),
        _event(
            created_at=now - timedelta(seconds=2), attempt_ref=latest_attempt,
            state="SUCCEEDED", transition=1, stage="SEARCH_WEATHER", task_ref=task_ref,
        ),
        _event(
            created_at=now - timedelta(seconds=3), attempt_ref="attempt-old",
            state="STAGE_STARTED", transition=99, stage="SCHEDULE", task_ref=task_ref,
        ),
    ]
    recovered = await orchestration_state.load_turn_stage_checkpoint(
        Database(rows), owner_id=owner_id, session_id=session_id,
        task_id=task_id, context_revision=4,
    )

    assert recovered == {
        "next_transition": 3,
        "has_previous_attempt": True,
        "interrupted_stage": "ROUTE",
        "completed_stages": ["SEARCH_WEATHER"],
        "selection_history": [],
    }


@pytest.mark.asyncio
async def test_load_turn_stage_checkpoint_ignores_wrong_context_and_expired_events():
    now = datetime.now(UTC)
    task_id = "same-message-idempotency-key"
    task_ref = orchestration_state._task_ref(task_id)
    rows = [
        _event(
            created_at=now - timedelta(seconds=1), attempt_ref="wrong-context",
            state="STAGE_STARTED", transition=7, stage="ROUTE", task_ref=task_ref,
            revision=5,
        ),
        _event(
            created_at=now - timedelta(minutes=11), attempt_ref="expired",
            state="STAGE_STARTED", transition=3, stage="SEARCH_WEATHER", task_ref=task_ref,
        ),
    ]
    result = await orchestration_state.load_turn_stage_checkpoint(
        Database(rows), owner_id=uuid4(), session_id=uuid4(),
        task_id=task_id, context_revision=4,
    )

    assert result is None


@pytest.mark.asyncio
async def test_load_turn_stage_checkpoint_restores_only_bounded_stage_decisions():
    now = datetime.now(UTC)
    task_id = "same-message-idempotency-key"
    task_ref = orchestration_state._task_ref(task_id)
    selected = _event(
        created_at=now - timedelta(seconds=2), attempt_ref="attempt-new",
        state="STAGE_SELECTED", transition=1, stage="ROUTE", task_ref=task_ref,
    )
    selected.metadata_json.update({
        "eligible_stages": ["SEARCH_WEATHER", "ROUTE"],
        "selected_order": ["ROUTE", "SEARCH_WEATHER"],
        "decision_source": "MODEL",
    })
    completed = _event(
        created_at=now - timedelta(seconds=1), attempt_ref="attempt-new",
        state="SUCCEEDED", transition=1, stage="ROUTE", task_ref=task_ref,
    )

    result = await orchestration_state.load_turn_stage_checkpoint(
        Database([completed, selected]), owner_id=uuid4(), session_id=uuid4(),
        task_id=task_id, context_revision=4,
    )

    assert result["selection_history"] == [{
        "eligible_stages": ["SEARCH_WEATHER", "ROUTE"],
        "order": ["ROUTE", "SEARCH_WEATHER"],
        "decision_source": "MODEL",
    }]


@pytest.mark.asyncio
async def test_load_turn_stage_checkpoint_rejects_malformed_decision_history():
    now = datetime.now(UTC)
    task_id = "same-message-idempotency-key"
    task_ref = orchestration_state._task_ref(task_id)
    selected = _event(
        created_at=now - timedelta(seconds=1), attempt_ref="attempt-new",
        state="STAGE_SELECTED", transition=1, stage="ROUTE", task_ref=task_ref,
    )
    selected.metadata_json.update({
        "eligible_stages": ["ROUTE"],
        "selected_order": ["SCHEDULE"],
        "decision_source": "MODEL",
    })
    result = await orchestration_state.load_turn_stage_checkpoint(
        Database([selected]), owner_id=uuid4(), session_id=uuid4(),
        task_id=task_id, context_revision=4,
    )
    assert result["selection_history"] == []


@pytest.mark.asyncio
async def test_load_turn_stage_checkpoint_uses_latest_lifecycle_state_per_stage():
    now = datetime.now(UTC)
    task_id = "same-message-idempotency-key"
    task_ref = orchestration_state._task_ref(task_id)
    attempt = "attempt-retried-stage"
    # An earlier success and an earlier open stage must not survive a later
    # failed retry of the same stage in this attempt.
    rows = [
        _event(created_at=now - timedelta(seconds=1), attempt_ref=attempt,
               state="FAILED", transition=4, stage="ROUTE", task_ref=task_ref),
        _event(created_at=now - timedelta(seconds=2), attempt_ref=attempt,
               state="STAGE_STARTED", transition=3, stage="ROUTE", task_ref=task_ref),
        _event(created_at=now - timedelta(seconds=3), attempt_ref=attempt,
               state="SUCCEEDED", transition=2, stage="ROUTE", task_ref=task_ref),
        _event(created_at=now - timedelta(seconds=4), attempt_ref=attempt,
               state="FAILED", transition=5, stage="SCHEDULE", task_ref=task_ref),
        _event(created_at=now - timedelta(seconds=5), attempt_ref=attempt,
               state="SUCCEEDED", transition=2, stage="SCHEDULE", task_ref=task_ref),
    ]

    result = await orchestration_state.load_turn_stage_checkpoint(
        Database(rows), owner_id=uuid4(), session_id=uuid4(),
        task_id=task_id, context_revision=4,
    )

    assert result is not None
    assert result["interrupted_stage"] is None
    assert result["completed_stages"] == []
