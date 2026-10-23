import json

import pytest

from tests.evaluation.advisor_fixed_workflow_baseline import (
    BASELINE_REVISION,
    DATASET,
    evaluate_case,
    run,
)


@pytest.mark.asyncio
async def test_product_grounded_b1_replays_committed_fixed_workflow_and_planner():
    result = await run()

    assert result["baseline_source_revision"] == BASELINE_REVISION
    assert result["case_count"] == 40
    assert result["planner_source"].endswith("build_itinerary")
    assert result["expected_action_matches"] < result["case_count"]
    assert result["expected_outcome_matches"] < result["case_count"]
    assert result["hard_invariant_violations"] > 0


@pytest.mark.asyncio
async def test_fixed_workflow_replays_planner_and_exposes_legacy_route_and_appointment_gaps():
    cases = json.loads(DATASET.read_text(encoding="utf-8"))["cases"]
    by_id = {case["id"]: case for case in cases}

    single = await evaluate_case(by_id["single-saved-place-skips-route"])
    assert single["outcome"] == "SINGLE_PLACE_DRAFT_WITHOUT_ROUTE"
    assert single["tool_attempts"] == []
    assert len(single["visits"]) == 1

    malformed = await evaluate_case(by_id["malformed-route-matrix-is-rejected"])
    assert malformed["outcome"] == "ROUTE_VERIFIED_DRAFT"
    assert malformed["hard_invariants"]["malformed_route_not_used_as_verified"] is False

    fixed = await evaluate_case(by_id["fixed-appointment-is-preserved-variant-1"])
    assert fixed["outcome"] == "DRAFT_WITH_FIXED_STOP_MOVED"
    assert fixed["hard_invariants"]["fixed_stop_not_moved"] is False
