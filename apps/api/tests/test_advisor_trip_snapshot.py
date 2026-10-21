from travelpilot_api.advisor.context import (
    _plan_node_meal_type,
    _prior_plan_assignment,
    _selected_candidates_after_trip_refresh,
)


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


def test_trip_snapshot_keeps_selected_lodging_when_plan_version_changes():
    context = {
        "revision": 4,
        "current_plan": {"id": "plan-1", "version": 2},
        "selected_candidate_ids": ["old-stop", "hotel-1"],
    }

    selected = _selected_candidates_after_trip_refresh(
        context,
        plan_id="plan-1",
        plan_version=3,
        plan_candidate_ids=["new-stop"],
        available_candidate_ids={"new-stop", "hotel-1"},
        retained_candidate_ids=["hotel-1"],
    )

    assert selected == ["new-stop", "hotel-1"]
    assert context["revision"] == 5


def test_trip_snapshot_keeps_pending_meal_selection_when_plan_version_changes():
    context = {
        "revision": 4,
        "current_plan": {"id": "plan-1", "version": 2},
        "selected_candidate_ids": ["old-stop", "lunch-assignment", "hotel-1"],
    }

    selected = _selected_candidates_after_trip_refresh(
        context,
        plan_id="plan-1",
        plan_version=3,
        plan_candidate_ids=["new-stop"],
        available_candidate_ids={"new-stop", "lunch-assignment", "hotel-1"},
        retained_candidate_ids=["hotel-1"],
    )

    assert selected == ["new-stop", "lunch-assignment", "hotel-1"]
    assert context["selected_candidate_ids"] == selected
    assert context["revision"] == 5


def test_trip_snapshot_drops_stale_pending_selection_but_keeps_available_meals():
    context = {
        "revision": 4,
        "current_plan": {"id": "plan-1", "version": 2},
        "selected_candidate_ids": ["deleted-meal", "lunch-assignment"],
    }

    selected = _selected_candidates_after_trip_refresh(
        context,
        plan_id="plan-1",
        plan_version=3,
        plan_candidate_ids=["new-stop"],
        available_candidate_ids={"new-stop", "lunch-assignment"},
    )

    assert selected == ["new-stop", "lunch-assignment"]


def test_plan_refresh_matches_same_restaurant_to_distinct_lunch_and_dinner_assignments():
    assignments = [
        {"id": "restaurant-lunch", "meal_type": "LUNCH"},
        {"id": "restaurant-dinner", "meal_type": "DINNER"},
    ]
    used: set[str] = set()

    lunch = _prior_plan_assignment(assignments, _plan_node_meal_type("用餐安排：午餐"), used)
    used.add(lunch["id"])
    dinner = _prior_plan_assignment(assignments, _plan_node_meal_type("用餐安排：晚餐"), used)

    assert lunch["id"] == "restaurant-lunch"
    assert dinner["id"] == "restaurant-dinner"
