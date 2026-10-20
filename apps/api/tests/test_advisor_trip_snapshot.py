from travelpilot_api.advisor.context import _selected_candidates_after_trip_refresh


def test_trip_snapshot_keeps_draft_selection_overrides_for_the_same_plan():
    context = {
        "revision": 4,
        "current_plan": {"id": "plan-1", "version": 2},
        "selected_candidate_ids": ["removed", "kept", "stale"],
    }

    selected = _selected_candidates_after_trip_refresh(
        context,
        plan_id="plan-1",
        plan_version=2,
        plan_candidate_ids=["removed", "kept"],
        available_candidate_ids={"removed", "kept"},
    )

    assert selected == ["removed", "kept"]
    assert context["selected_candidate_ids"] == selected
    assert context["revision"] == 5


def test_trip_snapshot_does_not_restore_a_deselected_stop_until_the_plan_changes():
    context = {
        "revision": 4,
        "current_plan": {"id": "plan-1", "version": 2},
        "selected_candidate_ids": ["kept"],
    }

    selected = _selected_candidates_after_trip_refresh(
        context,
        plan_id="plan-1",
        plan_version=2,
        plan_candidate_ids=["removed", "kept"],
        available_candidate_ids={"removed", "kept"},
    )

    assert selected == ["kept"]
    assert context["revision"] == 4


def test_trip_snapshot_reloads_all_stops_after_the_plan_version_changes():
    context = {
        "revision": 4,
        "current_plan": {"id": "plan-1", "version": 2},
        "selected_candidate_ids": ["kept"],
    }

    selected = _selected_candidates_after_trip_refresh(
        context,
        plan_id="plan-1",
        plan_version=3,
        plan_candidate_ids=["new-stop", "kept"],
        available_candidate_ids={"new-stop", "kept"},
    )

    assert selected == ["new-stop", "kept"]
    assert context["revision"] == 5
