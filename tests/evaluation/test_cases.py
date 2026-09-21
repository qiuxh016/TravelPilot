import json
from datetime import UTC, date, datetime, time
from pathlib import Path
from uuid import uuid4

import pytest
from travelpilot_core.engines.impact_analysis.replan import Node, plan_diff, replan
from travelpilot_core.engines.optimizer.itinerary import Candidate, build_itinerary
from travelpilot_infra.providers.maps.cached import CachedMapsProvider

from tests.evaluation.metrics.metrics import summarize

CASES = json.loads((Path(__file__).parent / "cases" / "scenarios.json").read_text(encoding="utf-8"))


def candidate(index: int, duration: int, hours=None):
    return Candidate(uuid4(), f"地点{index}", 30.6 + index * 0.01, 104.0, duration, 3, 0.5, hours)


@pytest.mark.parametrize("case", CASES, ids=lambda case: case["id"])
@pytest.mark.asyncio
async def test_fixed_scenario(case):
    kind = case["kind"]
    if kind == "plan":
        items = [candidate(index, duration) for index, duration in enumerate(case["durations"])]
        _, warnings = build_itinerary(date(2026, 10, 1), date(2026, 10, case["days"]), items)
        assert bool(warnings) is case["expect_warning"]
    elif kind == "opening":
        _, warnings = build_itinerary(date(2026, 10, 1), date(2026, 10, 1),
                                      [candidate(1, 120, {"open": "19:00", "close": "20:00"})])
        assert bool(warnings) is case["expect_warning"]
    elif kind == "route":
        items = [candidate(1, 120), candidate(2, 120)]
        _, warnings = build_itinerary(date(2026, 10, 1), date(2026, 10, 1), items,
                                      [[0, case["travel_min"]], [case["travel_min"], 0]])
        assert bool(warnings) is case["expect_warning"]
    elif kind == "event":
        when = datetime(2026, 10, 1, 9, tzinfo=UTC)
        nodes = [Node(uuid4(), f"地点{index}", "ATTRACTION", when.date(),
                      time(10 + index), time(11 + index), 3) for index in range(2)]
        payload = {"poi_id": str(nodes[0].poi_id)} if case["event_type"] == "POI_CANCELLED" else {}
        revised, _ = replan(nodes, case["event_type"], when, payload)
        operations = {change["operation"] for change in plan_diff(nodes, revised, case["event_type"])}
        assert case["expect_operation"] in operations
    elif kind == "provider":
        class Failing:
            async def get_route(self, origin, destination):
                raise RuntimeError("provider")

        cache = CachedMapsProvider(Failing())
        for _ in range(2):
            with pytest.raises(RuntimeError, match=case["expect_error"]):
                await cache.get_route((30, 104), (31, 105))


def test_metric_denominators_and_costs():
    result = summarize([{"plan_success": True, "is_replan": True, "replan_success": True,
                         "tool_calls": 2, "tool_successes": 1, "changed_nodes": 2,
                         "change_cost": 5, "latency_ms": 50, "token_cost": 10}])
    assert result["plan_success_rate"] == 1
    assert result["tool_success_rate"] == 0.5
    assert result["changed_node_count"] == 2
    assert result["change_cost"] == 5
