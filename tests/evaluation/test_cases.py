import json
from datetime import UTC, date, datetime, time
from pathlib import Path
from uuid import uuid4

import pytest
from travelpilot_api.advisor.policies import hard_limit_conflicts as _hard_limit_conflicts
from travelpilot_core.agents.extraction import fallback_extract
from travelpilot_core.engines.impact_analysis.replan import Node, plan_diff, replan
from travelpilot_core.engines.optimizer.itinerary import Candidate, build_itinerary
from travelpilot_core.skills import (
    check_skill_applicability,
    load_skill,
    select_skill,
    select_skill_decision,
)
from travelpilot_infra.providers.maps.cached import CachedMapsProvider

from tests.evaluation.metrics.metrics import summarize

CASES = json.loads((Path(__file__).parent / "cases" / "scenarios.json").read_text(encoding="utf-8"))


def test_user_add_requires_api_candidate_lookup_instead_of_silent_core_noop():
    when = datetime(2026, 10, 1, 9, tzinfo=UTC)
    existing = Node(uuid4(), "已计划地点", "ATTRACTION", when.date(), time(10), time(11), 3)

    with pytest.raises(ValueError, match="USER_ADD_REQUIRES_CANDIDATE_LOOKUP"):
        replan([existing], "USER_ADD", when, {"poi_id": str(uuid4())})


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
                      time(10 + index), time(11 + index), 3,
                      environment_profile={"exposure": "OUTDOOR", "sensitivity": {"RAIN": "HIGH"},
                                           "source": "EDITORIAL", "confidence": 0.9})
                 for index in range(2)]
        payload = (
            {"poi_id": str(nodes[0].poi_id)} if case["event_type"] == "POI_CANCELLED"
            else {"condition": "RAIN"} if case["event_type"] == "WEATHER_CHANGE"
            else {}
        )
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
    elif kind == "interest":
        result = fallback_extract(case["text"])
        assert case.get("expect_query") in result.search_queries if "expect_query" in case else True
        assert case.get("reject_query") not in result.search_queries if "reject_query" in case else True
    elif kind == "memory":
        memory = {"constraint_type": "HARD_LIMIT", "text": case["memory"]}
        conflicts = _hard_limit_conflicts(
            {"name": case["candidate"], "category": case["category"]}, [memory]
        )
        assert bool(conflicts) is case["expect_conflict"]
    elif kind == "skill":
        slug = "content-import" if case["task"] == "content_import" else "weather-replan"
        skill = load_skill(slug)
        context = {"task": case["task"], **case["context"]}
        decision = check_skill_applicability(skill, context)
        selection = select_skill_decision(case["task"], context=context)
        assert decision.applicable is case["expect_selected"]
        assert selection.applicability == decision
        assert (selection.skill is not None) is decision.applicable
        if not decision.applicable:
            record = selection.to_record()
            assert record["selected"] is False
            assert record["reason_code"] == case["expect_reason"]
            assert record["missing_requirements"] == list(decision.missing_requirements)
            assert record["matched_exclusions"] == list(decision.matched_exclusions)
        if decision.applicable:
            assert select_skill(case["task"], context=context).name == case["expect_skill"]
        else:
            assert decision.reason_code == case["expect_reason"]


def test_metric_denominators_and_costs():
    result = summarize([{"plan_success": True, "is_replan": True, "replan_success": True,
                         "tool_calls": 2, "tool_successes": 1, "changed_nodes": 2,
                         "change_cost": 5, "latency_ms": 50, "token_cost": 10}])
    assert result["plan_success_rate"] == 1
    assert result["tool_success_rate"] == 0.5
    assert result["changed_node_count"] == 2
    assert result["change_cost"] == 5
    assert result["p95_latency_ms"] == 50


def test_skill_loader_rejects_unpinned_version(tmp_path, monkeypatch):
    import travelpilot_core.skills as skills_module

    skill_dir = tmp_path / "content-import"
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text(
        "---\nname: Test\nversion: 2.0.0\ndescription: test\n"
        "applies_to: content_import\nrequires: user_content\nexcludes: provider_unavailable\n---\nbody\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(skills_module, "_SKILLS_ROOT", tmp_path)
    with pytest.raises(ValueError, match="not pinned"):
        skills_module.load_skill("content-import")


def test_origin_leg_selects_and_delays_first_day_stop():
    first = candidate(1, 45)
    second = candidate(2, 45)
    visits, warnings = build_itinerary(
        date(2026, 10, 1), date(2026, 10, 1), [first, second],
        [[0, 5], [5, 0]], start_travel_min=[75, 15],
    )
    assert not warnings
    assert visits[0].candidate == second
    assert visits[0].start_time == time(9, 15)


def test_origin_leg_rejects_mismatched_estimates():
    with pytest.raises(ValueError, match="start travel estimates"):
        build_itinerary(date(2026, 10, 1), date(2026, 10, 1), [candidate(1, 45)],
                        start_travel_min=[])
