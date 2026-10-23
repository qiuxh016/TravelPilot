from types import SimpleNamespace
from uuid import uuid4

import pytest
from travelpilot_api.api.v1 import agent as agent_api
from travelpilot_api.api.v1.agent import (
    PlannerRequest,
    _distance_matrix_fallback,
    run_planner_agent,
)
from travelpilot_core.agents.loop import LoopResult
from travelpilot_core.agents.verifiers import verify_distance_matrix, verify_trip_context
from travelpilot_core.ports.tools import ToolFailure
from travelpilot_infra.db.models.trip import TripModel
from travelpilot_infra.db.models.workflow import AgentRunModel, MemoryModel


def test_trip_context_verifier_checks_scope_shape_and_coordinate_ranges():
    valid = {
        "trip_id": "trip-1",
        "wishlist": ["Museum", "Park"],
        "points": [(30.0, 104.0), (31, 105)],
        "preference": "slow pace",
        "memories": ["likes museums"],
    }

    assert verify_trip_context(valid, "trip-1")
    assert not verify_trip_context(valid, "trip-2")
    assert not verify_trip_context({**valid, "points": [(30.0, 104.0)]}, "trip-1")
    assert not verify_trip_context({**valid, "points": [(91.0, 104.0), (31.0, 105.0)]}, "trip-1")
    assert not verify_trip_context({**valid, "points": [(True, 104.0), (31.0, 105.0)]}, "trip-1")


@pytest.mark.parametrize("matrix", [
    [[0, 12], [13, 0]],
    [[0, 12.5], [13.1, 0]],
])
def test_matrix_verifier_accepts_square_finite_nonnegative_numeric_data(matrix):
    assert verify_distance_matrix({"durations_min": matrix}, 2)


@pytest.mark.parametrize("matrix", [
    [[0, 12], [13]],
    [[0, -1], [13, 0]],
    [[1, 12], [13, 0]],
    [[0, 12], [13, 2]],
    [[0, float("inf")], [13, 0]],
    [[0, True], [13, 0]],
    [[0, "12"], [13, 0]],
])
def test_matrix_verifier_rejects_malformed_or_nonfinite_duration_data(matrix):
    assert not verify_distance_matrix({"durations_min": matrix}, 2)


def test_rejected_distance_matrix_uses_explicit_local_fallback():
    result = LoopResult(
        "REJECTED", {"get_trip_context": {}},
        [{"tool": "get_distance_matrix", "status": "REJECTED"}],
        "VERIFICATION_FAILED",
    )

    assert _distance_matrix_fallback(result) == "地图矩阵输出未通过校验，使用本地距离估算"


def test_context_tool_failure_does_not_get_misclassified_as_distance_fallback():
    result = LoopResult(
        "FAILED", {}, [{"tool": "get_trip_context", "status": "FAILED"}], "DB_UNAVAILABLE"
    )

    with pytest.raises(ToolFailure, match="DB_UNAVAILABLE"):
        _distance_matrix_fallback(result)


class _Rows:
    def __init__(self, rows=()):
        self._rows = list(rows)

    def all(self):
        return self._rows


class _Session:
    def __init__(self, trip_id, wishlist_rows):
        self.trip_id = trip_id
        self.wishlist_rows = wishlist_rows
        self.run = None

    async def scalar(self, statement):
        entity = statement.column_descriptions[0]["entity"]
        if entity is TripModel:
            return self.trip_id
        if entity is AgentRunModel:
            return self.run
        raise AssertionError(f"unexpected scalar entity: {entity}")

    async def execute(self, _statement):
        return _Rows(self.wishlist_rows)

    async def scalars(self, statement):
        assert statement.column_descriptions[0]["entity"] is MemoryModel
        return _Rows()

    def add(self, instance):
        if isinstance(instance, AgentRunModel):
            self.run = instance

    async def commit(self):
        return None

    async def rollback(self):
        return None

    async def get(self, model, _identity):
        return self.run if model is AgentRunModel else None


@pytest.mark.asyncio
async def test_untrusted_matrix_is_rejected_then_plan_uses_local_distance_fallback(monkeypatch):
    trip_id = uuid4()
    poi_rows = [
        (SimpleNamespace(), SimpleNamespace(
            id=uuid4(), name=f"place-{index}", latitude=30.0 + index / 10, longitude=104.0
        ))
        for index in range(2)
    ]
    session = _Session(trip_id, poi_rows)
    plan = SimpleNamespace(id=uuid4(), warnings=[])
    plan_generation = {}

    class Maps:
        async def get_distance_matrix(self, _points):
            return [[0, -1], [15, 0]]

    class PlanService:
        def __init__(self, _session):
            pass

        async def generate(self, _trip_id, maps, _constraints, *, commit):
            plan_generation["maps"] = maps
            plan_generation["commit"] = commit
            return plan

        async def get(self, _trip_id, _plan_id):
            return plan, []

    monkeypatch.setattr(agent_api, "PlanService", PlanService)
    monkeypatch.setattr(agent_api, "to_read", lambda _plan, _rows: {"id": plan.id})
    monkeypatch.setattr(
        agent_api,
        "settings",
        SimpleNamespace(llm_api_key="", llm_model="", llm_base_url=""),
    )

    async def unexpected_model_call(*_args, **_kwargs):
        raise AssertionError("live model calls are disabled in this test")

    monkeypatch.setattr(agent_api, "decide_next_action", unexpected_model_call)

    result = await run_planner_agent(
        trip_id,
        PlannerRequest(preference="轻松一点"),
        session=session,
        maps=Maps(),
        _trip=SimpleNamespace(id=trip_id),
        idempotency_key=None,
    )

    assert result["status"] == "SUCCEEDED"
    assert plan_generation == {"maps": None, "commit": False}
    assert [call["tool"] for call in result["tool_calls"]] == [
        "get_trip_context", "get_distance_matrix"
    ], result
    assert "地图矩阵输出未通过校验，使用本地距离估算" in result["explanation"]
    assert session.run.output_data["checkpoint"]["state"] == "SUCCEEDED"
