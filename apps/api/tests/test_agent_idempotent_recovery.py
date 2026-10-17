import asyncio
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import uuid4

import pytest
from travelpilot_api.api.v1 import agent as agent_api
from travelpilot_api.api.v1.agent import PlannerRequest, run_planner_agent
from travelpilot_api.settings import settings
from travelpilot_infra.db.models.trip import TripModel
from travelpilot_infra.db.models.workflow import AgentRunModel, MemoryModel


class Rows:
    def all(self):
        return []


class FakeSession:
    def __init__(self, trip_id):
        self.trip_id = trip_id
        self.run = None
        self.commits = 0
        self.tool_calls = []

    async def scalar(self, statement):
        entity = statement.column_descriptions[0]["entity"]
        if entity is TripModel:
            return self.trip_id
        if entity is AgentRunModel:
            return self.run
        raise AssertionError(f"unexpected scalar entity: {entity}")

    async def execute(self, _statement):
        return Rows()

    async def scalars(self, statement):
        assert statement.column_descriptions[0]["entity"] is MemoryModel
        return Rows()

    def add(self, instance):
        if isinstance(instance, AgentRunModel):
            self.run = instance
        else:
            self.tool_calls.append(instance)

    async def commit(self):
        self.commits += 1

    async def rollback(self):
        return None

    async def get(self, model, _identity):
        return self.run if model is AgentRunModel else None


@pytest.mark.asyncio
async def test_interrupted_planner_run_recovers_with_same_idempotency_key(monkeypatch):
    # Recovery behavior is under test here; keep the planner deterministic even
    # when a developer's .env.local configures a real LLM provider.
    monkeypatch.setattr(settings, "llm_api_key", "")
    monkeypatch.setattr(settings, "llm_model", "")
    trip_id = uuid4()
    trip = SimpleNamespace(id=trip_id)
    session = FakeSession(trip_id)
    attempts = 0
    saved_plan = SimpleNamespace(id=uuid4(), warnings=[])

    class PlanService:
        def __init__(self, _session):
            pass

        async def generate(self, *_args, **_kwargs):
            nonlocal attempts
            attempts += 1
            if attempts == 1:
                raise asyncio.CancelledError
            return saved_plan

        async def get(self, _trip_id, _plan_id):
            return saved_plan, []

    monkeypatch.setattr(agent_api, "PlanService", PlanService)
    monkeypatch.setattr(agent_api, "to_read", lambda plan, _rows: {"id": plan.id})

    with pytest.raises(asyncio.CancelledError):
        await run_planner_agent(
            trip_id,
            PlannerRequest(preference="轻松一点"),
            session=session,
            maps=None,
            _trip=trip,
            idempotency_key="planner-recovery-001",
        )

    assert session.run.status == "RUNNING"
    assert session.run.output_data["checkpoint"]["state"] == "DECISION_VERIFIED"
    session.run.output_data["checkpoint"]["updated_at"] = (
        datetime.now(UTC) - timedelta(minutes=3)
    ).isoformat()

    result = await run_planner_agent(
        trip_id,
        PlannerRequest(preference="轻松一点"),
        session=session,
        maps=None,
        _trip=trip,
        idempotency_key="planner-recovery-001",
    )

    assert result["status"] == "SUCCEEDED"
    assert result["plan"]["id"] == saved_plan.id
    assert session.run.status == "SUCCEEDED"
    states = [item["state"] for item in session.run.output_data["checkpoints"]]
    assert "RECOVERING" in states
    assert states[-1] == "SUCCEEDED"
    assert attempts == 2
